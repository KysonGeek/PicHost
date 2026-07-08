"""Folder feature: schema migration, CRUD, upload-to-folder, filtering, moving."""
import asyncio
import sqlite3
import pytest


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    conn.close()
    return cols


def test_migration_adds_folder_schema(tmp_path, mainmod, monkeypatch):
    """A legacy DB (images table without folder_id) must be upgraded by init_db."""
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.execute("""
        CREATE TABLE images (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, orig_name TEXT NOT NULL,
            size INTEGER NOT NULL, width INTEGER, height INTEGER,
            mime_type TEXT NOT NULL, created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO images VALUES ('old1','a.png','a.png',1,1,1,'image/png','2025-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mainmod, "DB_PATH", legacy)
    asyncio.run(mainmod.init_db())

    assert "folder_id" in _columns(legacy, "images")
    assert set(_columns(legacy, "folders")) == {"id", "name", "created_at"}
    conn = sqlite3.connect(legacy)
    row = conn.execute("SELECT folder_id FROM images WHERE id='old1'").fetchone()
    conn.close()
    assert row[0] is None  # legacy rows become uncategorized


def test_fresh_db_has_folder_schema(client, mainmod):
    # client fixture triggers lifespan/init_db on the (fresh) test DB
    assert "folder_id" in _columns(mainmod.DB_PATH, "images")
    assert "folders" in [
        r[0] for r in sqlite3.connect(mainmod.DB_PATH)
        .execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]


def test_upload_default_folder_is_null(client, auth, make_png, mainmod):
    r = client.post("/api/upload", headers=auth,
                    files={"file": ("x.png", make_png(), "image/png")})
    assert r.status_code == 200
    conn = sqlite3.connect(mainmod.DB_PATH)
    row = conn.execute("SELECT folder_id FROM images WHERE id=?",
                       (r.json()["id"],)).fetchone()
    conn.close()
    assert row[0] is None


# ── Folder CRUD ───────────────────────────────────────────────────────────────
def _create_folder(client, auth, name):
    return client.post("/api/folders", headers=auth, json={"name": name})


def test_folder_create_and_list(client, auth):
    r = _create_folder(client, auth, "旅行")
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "旅行" and body["id"]

    r = client.get("/api/folders", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["uncategorized"] == 0
    assert [f["name"] for f in data["folders"]] == ["旅行"]
    assert data["folders"][0]["count"] == 0


def test_folder_name_is_trimmed(client, auth):
    r = _create_folder(client, auth, "  截图  ")
    assert r.status_code == 201
    assert r.json()["name"] == "截图"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 51])
def test_folder_invalid_name_rejected(client, auth, bad):
    assert _create_folder(client, auth, bad).status_code == 422


def test_folder_duplicate_name_conflict(client, auth):
    assert _create_folder(client, auth, "旅行").status_code == 201
    assert _create_folder(client, auth, "旅行").status_code == 409


def test_folder_rename(client, auth):
    fid = _create_folder(client, auth, "旧名").json()["id"]
    r = client.patch(f"/api/folders/{fid}", headers=auth, json={"name": "新名"})
    assert r.status_code == 200 and r.json()["name"] == "新名"


def test_folder_rename_conflict_and_missing(client, auth):
    _create_folder(client, auth, "甲")
    fid = _create_folder(client, auth, "乙").json()["id"]
    assert client.patch(f"/api/folders/{fid}", headers=auth,
                        json={"name": "甲"}).status_code == 409
    assert client.patch("/api/folders/nope", headers=auth,
                        json={"name": "丙"}).status_code == 404


def test_folder_delete(client, auth):
    fid = _create_folder(client, auth, "临时").json()["id"]
    assert client.delete(f"/api/folders/{fid}", headers=auth).status_code == 200
    assert client.delete(f"/api/folders/{fid}", headers=auth).status_code == 404
    assert client.get("/api/folders", headers=auth).json()["folders"] == []


def test_folders_require_auth(client):
    assert client.get("/api/folders").status_code == 401
    assert client.post("/api/folders", json={"name": "x"}).status_code == 401


# ── Upload into folder ────────────────────────────────────────────────────────
def _upload(client, auth, make_png, folder_id=None, name="x.png"):
    url = "/api/upload" + (f"?folder_id={folder_id}" if folder_id else "")
    return client.post(url, headers=auth,
                       files={"file": (name, make_png(), "image/png")})


def test_upload_into_folder(client, auth, make_png):
    fid = _create_folder(client, auth, "壁纸").json()["id"]
    r = _upload(client, auth, make_png, folder_id=fid)
    assert r.status_code == 200
    assert r.json()["folder_id"] == fid

    data = client.get("/api/folders", headers=auth).json()
    assert data["folders"][0]["count"] == 1
    assert data["uncategorized"] == 0


def test_upload_into_missing_folder_rejected(client, auth, make_png, mainmod):
    r = _upload(client, auth, make_png, folder_id="nope")
    assert r.status_code == 404
    conn = sqlite3.connect(mainmod.DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    conn.close()
    assert n == 0  # nothing persisted for a rejected upload


def test_upload_without_folder_reports_null(client, auth, make_png):
    r = _upload(client, auth, make_png)
    assert r.status_code == 200
    assert r.json()["folder_id"] is None
