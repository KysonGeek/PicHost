"""Folder feature: schema migration, CRUD, upload-to-folder, filtering, moving."""
import asyncio
import sqlite3


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
