"""HEIC/HEIF upload: transcode to JPEG, strip metadata, keep pipeline uniform."""
import io

import pytest
from PIL import Image


@pytest.fixture
def make_heic():
    """Real HEIC bytes via pillow-heif; optionally with EXIF tags."""
    from pillow_heif import register_heif_opener
    register_heif_opener()

    def _make(width=32, height=16, orientation=None, tagged=False):
        img = Image.new("RGB", (width, height), (10, 200, 30))
        exif = img.getexif()
        if orientation:
            exif[0x0112] = orientation           # Orientation
        if tagged:
            exif[0x010F] = "Apple"               # Make
            exif[0x0132] = "2026:07:09 10:00:00" # DateTime
        buf = io.BytesIO()
        img.save(buf, format="HEIF", exif=exif.tobytes() if (orientation or tagged) else None)
        return buf.getvalue()
    return _make


def _upload_heic(client, auth, body, content_type="image/heic", name="photo.heic"):
    return client.post("/api/upload", headers=auth,
                       files={"file": (name, body, content_type)})


def test_heic_transcodes_to_jpeg(client, auth, make_heic, mainmod):
    r = _upload_heic(client, auth, make_heic())
    assert r.status_code == 200
    data = r.json()
    assert data["mime_type"] == "image/jpeg"
    assert data["filename"].endswith(".jpg")
    assert data["orig_name"] == "photo.heic"
    assert data["width"] == 32 and data["height"] == 16

    stored = mainmod.UPLOAD_DIR / data["filename"]
    assert stored.exists()
    with Image.open(stored) as out:
        assert out.format == "JPEG"
    assert (mainmod.THUMB_DIR / data["filename"]).exists()
    assert not list(mainmod.UPLOAD_DIR.glob("*.heic"))


def test_heic_as_octet_stream(client, auth, make_heic):
    r = _upload_heic(client, auth, make_heic(), content_type="application/octet-stream")
    assert r.status_code == 200
    assert r.json()["mime_type"] == "image/jpeg"


def test_heif_extension_accepted(client, auth, make_heic):
    r = _upload_heic(client, auth, make_heic(),
                     content_type="application/octet-stream", name="photo.heif")
    assert r.status_code == 200


def test_heic_orientation_baked_in(client, auth, make_heic):
    """Orientation=6 (rotate 90°) must end up in pixels: 32x16 → 16x32."""
    r = _upload_heic(client, auth, make_heic(orientation=6))
    assert r.status_code == 200
    data = r.json()
    assert (data["width"], data["height"]) == (16, 32)


def test_heic_metadata_stripped(client, auth, make_heic, mainmod):
    r = _upload_heic(client, auth, make_heic(tagged=True))
    assert r.status_code == 200
    stored = mainmod.UPLOAD_DIR / r.json()["filename"]
    with Image.open(stored) as out:
        assert dict(out.getexif()) == {}


def test_fake_heic_bytes_rejected(client, auth, mainmod):
    import sqlite3
    r = _upload_heic(client, auth, b"not a real heic at all" * 10)
    assert r.status_code == 415
    conn = sqlite3.connect(mainmod.DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    conn.close()


def test_heic_joins_folders(client, auth, make_heic):
    fid = client.post("/api/folders", headers=auth, json={"name": "苹果照片"}).json()["id"]
    r = client.post(f"/api/upload?folder_id={fid}", headers=auth,
                    files={"file": ("p.heic", make_heic(), "image/heic")})
    assert r.status_code == 200 and r.json()["folder_id"] == fid
    listed = client.get(f"/api/images?folder={fid}", headers=auth).json()
    assert listed["total"] == 1
