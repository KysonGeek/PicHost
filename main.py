import os
import uuid
import hmac
import hashlib
import time
import aiosqlite
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
THUMB_DIR = UPLOAD_DIR / "thumbs"
DB_PATH = BASE_DIR / "images.db"
STATIC_DIR = BASE_DIR / "static"
ENV_FILE = BASE_DIR / ".env"

UPLOAD_DIR.mkdir(exist_ok=True)
THUMB_DIR.mkdir(exist_ok=True)

ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/svg+xml",
}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
MAX_SIZE = 20 * 1024 * 1024  # 20 MB
THUMB_SIZE = (400, 400)
TOKEN_EXPIRE_SECONDS = 30 * 86400  # 30 days


# ── Config ─────────────────────────────────────────────────────────────────────
def _load_env_file():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env_file()

PASSWORD = os.environ.get("PICHOST_PASSWORD", "")
if not PASSWORD:
    raise RuntimeError(
        "PICHOST_PASSWORD is not set. "
        "Create /opt/app/img/.env with PICHOST_PASSWORD=yourpassword"
    )


# ── Token helpers ──────────────────────────────────────────────────────────────
def _sign(payload: str) -> str:
    return hmac.new(PASSWORD.encode(), payload.encode(), hashlib.sha256).hexdigest()

def make_token() -> str:
    exp = int(time.time()) + TOKEN_EXPIRE_SECONDS
    sig = _sign(str(exp))
    return f"{exp}.{sig}"

def verify_token(token: str) -> bool:
    try:
        exp_str, sig = token.split(".", 1)
        if time.time() > int(exp_str):
            return False
        return hmac.compare_digest(sig, _sign(exp_str))
    except Exception:
        return False


# ── Auth dependency ────────────────────────────────────────────────────────────
_bearer = HTTPBearer(auto_error=False)

def require_auth(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if creds is None or not verify_token(creds.credentials):
        raise HTTPException(status_code=401, detail="未授权，请先登录")


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="PicHost")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS images (
                id        TEXT PRIMARY KEY,
                filename  TEXT NOT NULL,
                orig_name TEXT NOT NULL,
                size      INTEGER NOT NULL,
                width     INTEGER,
                height    INTEGER,
                mime_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()


@app.on_event("startup")
async def startup():
    await init_db()


# ── Static files ───────────────────────────────────────────────────────────────
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/static",  StaticFiles(directory=str(STATIC_DIR)),  name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# ── Auth endpoints ─────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str

@app.post("/api/auth/login")
async def login(body: LoginRequest):
    if not hmac.compare_digest(body.password, PASSWORD):
        raise HTTPException(status_code=401, detail="密码错误")
    return {"token": make_token()}


# ── Upload ─────────────────────────────────────────────────────────────────────
@app.post("/api/upload", dependencies=[Depends(require_auth)])
async def upload(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {file.content_type}")

    orig_ext = Path(file.filename).suffix.lower() if file.filename else ""
    if orig_ext not in ALLOWED_EXT:
        orig_ext = _mime_to_ext(file.content_type)

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="文件大小超过 20MB 限制")

    file_id = uuid.uuid4().hex[:12]
    filename = f"{file_id}{orig_ext}"
    dest_path = UPLOAD_DIR / filename
    dest_path.write_bytes(content)

    width, height = None, None
    try:
        with Image.open(dest_path) as img:
            width, height = img.size
            if file.content_type != "image/svg+xml":
                thumb = img.copy()
                thumb.thumbnail(THUMB_SIZE, Image.LANCZOS)
                if thumb.mode in ("RGBA", "P"):
                    bg = Image.new("RGB", thumb.size, (255, 255, 255))
                    if thumb.mode == "P":
                        thumb = thumb.convert("RGBA")
                    bg.paste(thumb, mask=thumb.split()[3] if thumb.mode == "RGBA" else None)
                    thumb = bg
                thumb.save(THUMB_DIR / filename, quality=85, optimize=True)
            else:
                (THUMB_DIR / filename).write_bytes(content)
    except (UnidentifiedImageError, Exception):
        pass

    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO images VALUES (?,?,?,?,?,?,?,?)",
            (file_id, filename, file.filename or filename,
             len(content), width, height, file.content_type, created_at)
        )
        await db.commit()

    return {
        "id": file_id, "filename": filename, "orig_name": file.filename,
        "size": len(content), "width": width, "height": height,
        "mime_type": file.content_type, "created_at": created_at,
    }


# ── Image list ─────────────────────────────────────────────────────────────────
@app.get("/api/images", dependencies=[Depends(require_auth)])
async def list_images(page: int = 1, per_page: int = 50):
    offset = (page - 1) * per_page
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM images ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, offset)
        ) as cursor:
            rows = await cursor.fetchall()
        async with db.execute("SELECT COUNT(*) FROM images") as cursor:
            total = (await cursor.fetchone())[0]

    return {
        "total": total, "page": page, "per_page": per_page,
        "images": [dict(r) for r in rows],
    }


# ── Delete ─────────────────────────────────────────────────────────────────────
@app.delete("/api/images/{image_id}", dependencies=[Depends(require_auth)])
async def delete_image(image_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM images WHERE id = ?", (image_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="图片不存在")

        filename = row["filename"]
        await db.execute("DELETE FROM images WHERE id = ?", (image_id,))
        await db.commit()

    for path in (UPLOAD_DIR / filename, THUMB_DIR / filename):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    return {"ok": True}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _mime_to_ext(mime: str) -> str:
    return {
        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "image/webp": ".webp", "image/bmp": ".bmp", "image/svg+xml": ".svg",
    }.get(mime, ".jpg")
