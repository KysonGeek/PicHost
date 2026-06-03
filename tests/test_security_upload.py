"""Tests for upload validation and safe serving of user content."""
import pytest


def _upload(client, auth, name, data, ct):
    return client.post("/api/upload", headers=auth, files={"file": (name, data, ct)})


# ── Safe serving of /uploads (SVG XSS neutralisation, caching, nosniff) ──────
def test_svg_served_with_sandbox_csp(client, auth, make_svg):
    """An uploaded SVG must be served with a CSP that blocks script execution."""
    r = _upload(client, auth, "x.svg", make_svg(script=True), "image/svg+xml")
    assert r.status_code == 200
    filename = r.json()["filename"]

    served = client.get(f"/uploads/{filename}")
    assert served.status_code == 200
    csp = served.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "sandbox" in csp
    assert served.headers.get("x-content-type-options") == "nosniff"


def test_uploads_have_immutable_cache(client, auth, make_png):
    r = _upload(client, auth, "x.png", make_png(), "image/png")
    filename = r.json()["filename"]
    served = client.get(f"/uploads/{filename}")
    assert "immutable" in served.headers.get("cache-control", "")


def test_uploads_have_nosniff(client, auth, make_png):
    r = _upload(client, auth, "x.png", make_png(), "image/png")
    filename = r.json()["filename"]
    served = client.get(f"/uploads/{filename}")
    assert served.headers.get("x-content-type-options") == "nosniff"


def test_raster_image_still_served_with_correct_type(client, auth, make_png):
    r = _upload(client, auth, "x.png", make_png(), "image/png")
    filename = r.json()["filename"]
    served = client.get(f"/uploads/{filename}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/")


# ── Real-bytes validation (don't trust client content_type) ──────────────────
def test_non_image_bytes_rejected_and_not_persisted(client, auth, mainmod):
    before = len([p for p in mainmod.UPLOAD_DIR.glob("*") if p.is_file()])
    r = _upload(client, auth, "evil.png", b"<html>not an image</html>", "image/png")
    assert r.status_code == 415
    after = len([p for p in mainmod.UPLOAD_DIR.glob("*") if p.is_file()])
    assert after == before  # nothing written to disk


def test_content_type_spoof_uses_real_format(client, auth, make_png):
    """PNG bytes declared as image/jpeg should be recorded as the real type."""
    r = _upload(client, auth, "x.jpg", make_png(), "image/jpeg")
    assert r.status_code == 200
    assert r.json()["mime_type"] == "image/png"


# ── SVG gets a thumbnail (previous dead-code branch) ─────────────────────────
def test_svg_upload_creates_thumbnail(client, auth, make_svg, mainmod):
    r = _upload(client, auth, "x.svg", make_svg(), "image/svg+xml")
    assert r.status_code == 200
    filename = r.json()["filename"]
    assert (mainmod.THUMB_DIR / filename).exists()


# ── EXIF orientation applied to thumbnails / stored dimensions ───────────────
def test_exif_orientation_applied(client, auth, make_jpeg_exif):
    """Orientation=6 turns a stored 20x10 into a displayed 10x20."""
    r = _upload(client, auth, "p.jpg", make_jpeg_exif(20, 10, orientation=6), "image/jpeg")
    assert r.status_code == 200
    data = r.json()
    assert data["width"] == 10 and data["height"] == 20


# ── Decompression-bomb / pixel-flood guard ───────────────────────────────────
def test_pixel_bomb_rejected(client, auth, make_png, mainmod, monkeypatch):
    monkeypatch.setattr(mainmod, "MAX_PIXELS", 50)
    r = _upload(client, auth, "big.png", make_png(100, 100), "image/png")
    assert r.status_code == 415


# ── Orphan-file cleanup when the metadata insert fails ───────────────────────
def test_orphan_files_cleaned_when_db_insert_fails(client, auth, make_png, mainmod, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(mainmod.aiosqlite, "connect", boom)

    before = len([p for p in mainmod.UPLOAD_DIR.glob("*") if p.is_file()])
    with pytest.raises(RuntimeError):
        _upload(client, auth, "x.png", make_png(), "image/png")
    after = len([p for p in mainmod.UPLOAD_DIR.glob("*") if p.is_file()])
    assert after == before  # written file was rolled back
