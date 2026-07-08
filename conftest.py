"""Pytest fixtures for PicHost.

Configures isolated temp paths via env vars *before* importing the app, so tests
never touch the real images.db / uploads/ directory.
"""
import os
import io
import sqlite3
import tempfile
from pathlib import Path

# ── Isolate data paths BEFORE importing the app ──────────────────────────────
_TMP = Path(tempfile.mkdtemp(prefix="pichost-test-"))
os.environ["PICHOST_PASSWORD"] = "test-secret-pw"
os.environ["PICHOST_DB_PATH"] = str(_TMP / "images.db")
os.environ["PICHOST_UPLOAD_DIR"] = str(_TMP / "uploads")

import pytest
from PIL import Image
from fastapi.testclient import TestClient

import main  # noqa: E402  (must come after env setup above)

PASSWORD = "test-secret-pw"


@pytest.fixture
def mainmod():
    """The imported `main` module, for tests that monkeypatch internals."""
    return main


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_state():
    """Wipe DB rows and upload files after each test for isolation."""
    yield
    try:
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM images")
        conn.execute("DELETE FROM folders")
        conn.commit()
        conn.close()
    except Exception:
        pass
    for d in (main.UPLOAD_DIR, main.THUMB_DIR):
        for p in d.glob("*"):
            if p.is_file():
                p.unlink()


@pytest.fixture
def auth(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── Image factories ──────────────────────────────────────────────────────────
@pytest.fixture
def make_png():
    def _make(width=10, height=10, color=(255, 0, 0)):
        buf = io.BytesIO()
        Image.new("RGB", (width, height), color).save(buf, format="PNG")
        return buf.getvalue()
    return _make


@pytest.fixture
def make_jpeg_exif():
    """A landscape JPEG carrying an EXIF Orientation=6 (rotate 90° CW) tag.

    Stored pixels are `width`×`height`; a correct viewer renders it rotated to
    `height`×`width`. Used to test EXIF-aware thumbnailing.
    """
    def _make(width=20, height=10, orientation=6):
        img = Image.new("RGB", (width, height), (0, 128, 255))
        exif = img.getexif()
        exif[0x0112] = orientation
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        return buf.getvalue()
    return _make


@pytest.fixture
def make_svg():
    def _make(script=False):
        payload = "<script>window.__xss=1</script>" if script else ""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
            f'{payload}<rect width="50" height="50" fill="green"/></svg>'
        )
        return svg.encode()
    return _make
