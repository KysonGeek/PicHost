"""GLB (binary glTF 2.0) upload support: acceptance, validation, size caps."""
import struct
import sqlite3

import pytest


@pytest.fixture
def make_glb():
    """Minimal valid GLB: 12-byte header + one JSON chunk, padded to `size`."""
    def _make(size=None, magic=b"glTF", version=2, truncate=False):
        json_payload = b'{"asset":{"version":"2.0"}}'
        pad = (4 - len(json_payload) % 4) % 4
        json_payload += b" " * pad
        if size is not None:
            extra = size - (12 + 8 + len(json_payload))
            if extra > 0:
                json_payload += b" " * extra
        chunk = struct.pack("<II", len(json_payload), 0x4E4F534A) + json_payload
        total = 12 + len(chunk)
        header = magic + struct.pack("<II", version, total)
        full = header + chunk
        if truncate:
            return full[:11]
        return full
    return _make


def _upload_glb(client, auth, body, content_type="model/gltf-binary",
                name="model.glb", folder_id=None):
    url = "/api/upload" + (f"?folder_id={folder_id}" if folder_id else "")
    return client.post(url, headers=auth, files={"file": (name, body, content_type)})


def test_glb_upload_roundtrip(client, auth, make_glb, mainmod):
    r = _upload_glb(client, auth, make_glb())
    assert r.status_code == 200
    data = r.json()
    assert data["mime_type"] == "model/gltf-binary"
    assert data["width"] is None and data["height"] is None
    assert data["filename"].endswith(".glb")
    assert (mainmod.UPLOAD_DIR / data["filename"]).exists()
    assert not (mainmod.THUMB_DIR / data["filename"]).exists()  # no thumbnail


def test_glb_upload_as_octet_stream(client, auth, make_glb):
    """Browsers often report .glb as application/octet-stream (or nothing)."""
    r = _upload_glb(client, auth, make_glb(), content_type="application/octet-stream")
    assert r.status_code == 200
    assert r.json()["mime_type"] == "model/gltf-binary"


def test_glb_upload_with_empty_content_type(client, auth, make_glb):
    """Some browsers send no content type at all for .glb files."""
    r = _upload_glb(client, auth, make_glb(), content_type="")
    assert r.status_code == 200
    assert r.json()["mime_type"] == "model/gltf-binary"


def test_octet_stream_without_glb_name_rejected(client, auth, make_glb):
    r = _upload_glb(client, auth, make_glb(), content_type="application/octet-stream",
                    name="model.bin")
    assert r.status_code == 415


@pytest.mark.parametrize("kwargs", [{"magic": b"FAKE"}, {"version": 1}, {"truncate": True}])
def test_invalid_glb_bytes_rejected(client, auth, make_glb, mainmod, kwargs):
    r = _upload_glb(client, auth, make_glb(**kwargs))
    assert r.status_code == 415
    conn = sqlite3.connect(mainmod.DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    conn.close()
    assert not any(p.is_file() for p in mainmod.UPLOAD_DIR.glob("*.glb"))


def test_glb_name_with_image_type_goes_image_path(client, auth, make_glb):
    """A .glb named file claiming image/png must pass Pillow — and fail."""
    r = _upload_glb(client, auth, make_glb(), content_type="image/png")
    assert r.status_code == 415


def test_glb_over_glb_cap_rejected(client, auth, make_glb, mainmod, monkeypatch):
    monkeypatch.setattr(mainmod, "MAX_GLB_SIZE", 1024)
    r = _upload_glb(client, auth, make_glb(size=2048))
    assert r.status_code == 413
    # detail wording is type-based ("100MB" for glb), independent of the patched cap
    assert "100MB" in r.json()["detail"]


def test_glb_between_image_and_glb_cap_allowed(client, auth, make_glb):
    """>20MB is fine for glb (its cap is 100MB), while images stay capped."""
    r = _upload_glb(client, auth, make_glb(size=21 * 1024 * 1024))
    assert r.status_code == 200


def test_image_cap_unchanged(client, auth):
    blob = b"\x89PNG" + b"\x00" * (21 * 1024 * 1024)
    r = client.post("/api/upload", headers=auth,
                    files={"file": ("big.png", blob, "image/png")})
    assert r.status_code == 413
    assert "20MB" in r.json()["detail"]


def test_glb_participates_in_folders(client, auth, make_glb):
    fid = client.post("/api/folders", headers=auth, json={"name": "模型"}).json()["id"]
    up = _upload_glb(client, auth, make_glb(), folder_id=fid)
    assert up.status_code == 200 and up.json()["folder_id"] == fid

    listed = client.get(f"/api/images?folder={fid}", headers=auth).json()
    assert listed["total"] == 1
    assert listed["images"][0]["mime_type"] == "model/gltf-binary"

    img_id = up.json()["id"]
    assert client.patch(f"/api/images/{img_id}", headers=auth,
                        json={"folder_id": None}).status_code == 200
    assert client.delete(f"/api/images/{img_id}", headers=auth).status_code == 200
