"""Apple photo uploads (HEIC/HEIF and multi-frame MPO): transcode to JPEG,
strip metadata, keep pipeline uniform."""
import io

import pytest
from PIL import Image


@pytest.fixture
def make_heic():
    """Real HEIC bytes via pillow-heif; optionally with EXIF tags."""
    from pillow_heif import register_heif_opener
    register_heif_opener()

    def _make(width=32, height=16, orientation=None, tagged=False, gps=False):
        img = Image.new("RGB", (width, height), (10, 200, 30))
        exif = img.getexif()
        if orientation:
            exif[0x0112] = orientation           # Orientation
        if tagged:
            exif[0x010F] = "Apple"               # Make
            exif[0x0132] = "2026:07:09 10:00:00" # DateTime
        if gps:
            from PIL.ExifTags import IFD
            from PIL.TiffImagePlugin import IFDRational
            gps_ifd = exif.get_ifd(IFD.GPSInfo)
            gps_ifd[1] = "N"                      # GPSLatitudeRef
            gps_ifd[2] = (IFDRational(31, 1), IFDRational(13, 1), IFDRational(0, 1))  # GPSLatitude 31°13'
        buf = io.BytesIO()
        img.save(buf, format="HEIF", exif=exif.tobytes() if (orientation or tagged or gps) else None)
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
    r = _upload_heic(client, auth, make_heic(tagged=True, gps=True))
    assert r.status_code == 200
    stored = mainmod.UPLOAD_DIR / r.json()["filename"]
    with Image.open(stored) as out:
        exif = out.getexif()
        assert dict(exif) == {}
        from PIL.ExifTags import IFD
        assert dict(exif.get_ifd(IFD.GPSInfo)) == {}
    raw = stored.read_bytes()
    assert b"Apple" not in raw


def test_fake_heic_bytes_rejected(client, auth, mainmod):
    import sqlite3
    r = _upload_heic(client, auth, b"not a real heic at all" * 10)
    assert r.status_code == 415
    conn = sqlite3.connect(mainmod.DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    conn.close()
    assert not [p for p in mainmod.UPLOAD_DIR.glob("*") if p.is_file()]


def test_oversized_heic_rejected_before_write(client, auth, make_heic, mainmod, monkeypatch):
    monkeypatch.setattr(mainmod, "MAX_PIXELS", 100)  # 32x16 = 512 > 100
    r = _upload_heic(client, auth, make_heic())
    assert r.status_code == 415
    assert not list(mainmod.UPLOAD_DIR.glob("*.jpg"))
    assert not list(mainmod.THUMB_DIR.glob("*.jpg"))


def test_heic_joins_folders(client, auth, make_heic):
    fid = client.post("/api/folders", headers=auth, json={"name": "苹果照片"}).json()["id"]
    r = client.post(f"/api/upload?folder_id={fid}", headers=auth,
                    files={"file": ("p.heic", make_heic(), "image/heic")})
    assert r.status_code == 200 and r.json()["folder_id"] == fid
    listed = client.get(f"/api/images?folder={fid}", headers=auth).json()
    assert listed["total"] == 1


# ── MPO (iPhone multi-frame JPEG) ─────────────────────────────────────────────
@pytest.fixture
def make_mpo():
    """Two-frame MPO (what iPhones produce for burst/depth shots)."""
    def _make(width=32, height=16, tagged=False):
        f1 = Image.new("RGB", (width, height), (200, 10, 10))
        f2 = Image.new("RGB", (width, height), (10, 10, 200))
        exif = f1.getexif()
        if tagged:
            exif[0x010F] = "Apple"               # Make
            exif[0x0132] = "2026:07:09 10:00:00" # DateTime
        buf = io.BytesIO()
        kwargs = {"exif": exif.tobytes()} if tagged else {}
        f1.save(buf, format="MPO", save_all=True, append_images=[f2], **kwargs)
        return buf.getvalue()
    return _make


def test_mpo_transcodes_to_single_frame_jpeg(client, auth, make_mpo, mainmod):
    """iPhone MPO uploads must not 415; first frame becomes a plain JPEG."""
    r = client.post("/api/upload", headers=auth,
                    files={"file": ("burst.jpg", make_mpo(), "image/jpeg")})
    assert r.status_code == 200
    data = r.json()
    assert data["mime_type"] == "image/jpeg"
    assert data["filename"].endswith(".jpg")
    assert data["width"] == 32 and data["height"] == 16

    stored = mainmod.UPLOAD_DIR / data["filename"]
    with Image.open(stored) as out:
        assert out.format == "JPEG"
        assert getattr(out, "n_frames", 1) == 1  # hidden frames dropped
    assert (mainmod.THUMB_DIR / data["filename"]).exists()


def test_mpo_metadata_stripped(client, auth, make_mpo, mainmod):
    r = client.post("/api/upload", headers=auth,
                    files={"file": ("burst.jpg", make_mpo(tagged=True), "image/jpeg")})
    assert r.status_code == 200
    stored = mainmod.UPLOAD_DIR / r.json()["filename"]
    with Image.open(stored) as out:
        assert dict(out.getexif()) == {}
    assert b"Apple" not in stored.read_bytes()
