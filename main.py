import io
import os
import re
import uuid
import hmac
import hashlib
import time
import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

# Let Pillow decode Apple's HEIC/HEIF; uploads are transcoded to JPEG.
register_heif_opener()

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = Path(os.environ.get("PICHOST_UPLOAD_DIR", BASE_DIR / "uploads"))
THUMB_DIR = UPLOAD_DIR / "thumbs"
DB_PATH = Path(os.environ.get("PICHOST_DB_PATH", BASE_DIR / "images.db"))
STATIC_DIR = BASE_DIR / "static"
ENV_FILE = BASE_DIR / ".env"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/svg+xml",
    "image/heic", "image/heif",
    "model/gltf-binary",
}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".glb", ".heic", ".heif"}
MAX_SIZE = 20 * 1024 * 1024  # 20 MB (compressed upload cap)
MAX_GLB_SIZE = 100 * 1024 * 1024  # 100 MB cap for .glb models (images stay at MAX_SIZE)
MAX_PIXELS = 50_000_000      # decoded-pixel cap (decompression-bomb / pixel-flood guard)
THUMB_SIZE = (400, 400)
TOKEN_EXPIRE_SECONDS = 30 * 86400  # 30 days

# Real (sniffed) Pillow format -> canonical MIME. SVG is handled separately
# because Pillow cannot decode it.
PIL_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif",
    "WEBP": "image/webp", "BMP": "image/bmp",
}

# Make Pillow itself refuse absurd images on decode, in addition to our own check.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        """)
        # Idempotent migration: pre-folder DBs lack images.folder_id.
        async with db.execute("PRAGMA table_info(images)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        if "folder_id" not in cols:
            await db.execute("ALTER TABLE images ADD COLUMN folder_id TEXT")
        # Gallery lists ORDER BY created_at DESC; index it to avoid full sorts.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_created_at ON images(created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_folder_id ON images(folder_id)"
        )
        # Heal any folder_id left dangling by historical races: images must
        # never point at a folder row that no longer exists.
        await db.execute(
            "UPDATE images SET folder_id = NULL "
            "WHERE folder_id IS NOT NULL AND folder_id NOT IN (SELECT id FROM folders)"
        )
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="PicHost", lifespan=lifespan)


@app.middleware("http")
async def add_security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    if request.url.path.startswith("/uploads/"):
        # User-supplied content (esp. SVG): sandbox + no script execution, and
        # cache forever since filenames are unique per upload.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        )
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


# ── Static files ───────────────────────────────────────────────────────────────
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/static",  StaticFiles(directory=str(STATIC_DIR)),  name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# ── Auth endpoints ─────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str

class FolderName(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 50:
            raise ValueError("文件夹名称需为 1-50 个字符")
        return v

@app.post("/api/auth/login")
async def login(body: LoginRequest):
    if not hmac.compare_digest(body.password, PASSWORD):
        raise HTTPException(status_code=401, detail="密码错误")
    return {"token": make_token()}


# ── Folders ────────────────────────────────────────────────────────────────────
@app.get("/api/folders", dependencies=[Depends(require_auth)])
async def list_folders():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT f.id, f.name, f.created_at, COUNT(i.id) AS count
            FROM folders f LEFT JOIN images i ON i.folder_id = f.id
            GROUP BY f.id ORDER BY f.created_at ASC
        """) as cursor:
            folders = [dict(r) for r in await cursor.fetchall()]
        async with db.execute(
            "SELECT COUNT(*) FROM images WHERE folder_id IS NULL"
        ) as cursor:
            row = await cursor.fetchone()
            uncategorized = row[0] if row else 0
    return {"folders": folders, "uncategorized": uncategorized}


@app.post("/api/folders", status_code=201, dependencies=[Depends(require_auth)])
async def create_folder(body: FolderName):
    folder_id = uuid.uuid4().hex[:12]
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO folders (id, name, created_at) VALUES (?,?,?)",
                (folder_id, body.name, created_at),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(status_code=409, detail="同名文件夹已存在")
    return {"id": folder_id, "name": body.name, "created_at": created_at}


@app.patch("/api/folders/{folder_id}", dependencies=[Depends(require_auth)])
async def rename_folder(folder_id: str, body: FolderName):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM folders WHERE id = ?", (folder_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="文件夹不存在")
        try:
            await db.execute(
                "UPDATE folders SET name = ? WHERE id = ?", (body.name, folder_id)
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(status_code=409, detail="同名文件夹已存在")
    return {"id": folder_id, "name": body.name, "created_at": row["created_at"]}


@app.delete("/api/folders/{folder_id}", dependencies=[Depends(require_auth)])
async def delete_folder(folder_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM folders WHERE id = ?", (folder_id,)
        ) as cursor:
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="文件夹不存在")
        # Non-destructive: images fall back to uncategorized, same transaction.
        await db.execute(
            "UPDATE images SET folder_id = NULL WHERE folder_id = ?", (folder_id,)
        )
        await db.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        await db.commit()
    return {"ok": True}


# ── Upload ─────────────────────────────────────────────────────────────────────
@app.post("/api/upload", dependencies=[Depends(require_auth)])
async def upload(file: UploadFile = File(...), folder_id: str | None = Query(None)):
    name_lower = (file.filename or "").lower()
    # Browsers/OSes often report .glb and .heic as application/octet-stream
    # (or nothing); fall back to the extension for those. Real validation is
    # always by bytes.
    generic_type = file.content_type in (None, "", "application/octet-stream")
    is_glb = file.content_type == "model/gltf-binary" or (
        name_lower.endswith(".glb") and generic_type
    )
    ext_sniffable = name_lower.endswith((".heic", ".heif")) and generic_type
    if not is_glb and not ext_sniffable and file.content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {file.content_type}")

    if folder_id:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM folders WHERE id = ?", (folder_id,)
            ) as cursor:
                if not await cursor.fetchone():
                    raise HTTPException(status_code=404, detail="文件夹不存在")
    else:
        folder_id = None  # normalize "" to NULL

    content = await file.read()
    size_cap = MAX_GLB_SIZE if is_glb else MAX_SIZE
    if len(content) > size_cap:
        limit_label = "100MB" if is_glb else "20MB"
        raise HTTPException(status_code=413, detail=f"文件大小超过 {limit_label} 限制")

    file_id = uuid.uuid4().hex[:12]
    is_svg = file.content_type == "image/svg+xml"

    # Validate the actual bytes and write original + thumbnail, off the event loop.
    try:
        meta = await run_in_threadpool(_process_upload, content, is_svg, is_glb, file_id)
    except _UploadRejected as e:
        raise HTTPException(status_code=415, detail=str(e))

    filename = meta["filename"]
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Re-validate inside the same connection as the INSERT: the folder
            # may have been deleted while we were processing the image bytes.
            # Files are already written, so fall back to uncategorized instead
            # of erroring, rather than persist a dangling folder_id.
            if folder_id:
                async with db.execute(
                    "SELECT 1 FROM folders WHERE id = ?", (folder_id,)
                ) as cursor:
                    if not await cursor.fetchone():
                        folder_id = None
            await db.execute(
                "INSERT INTO images "
                "(id, filename, orig_name, size, width, height, mime_type, created_at, folder_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (file_id, filename, file.filename or filename,
                 len(content), meta["width"], meta["height"], meta["mime"], created_at, folder_id)
            )
            await db.commit()
    except Exception:
        # Don't leak orphaned files if the metadata insert fails.
        for p in (UPLOAD_DIR / filename, THUMB_DIR / filename):
            p.unlink(missing_ok=True)
        raise

    return {
        "id": file_id, "filename": filename, "orig_name": file.filename,
        "size": len(content), "width": meta["width"], "height": meta["height"],
        "mime_type": meta["mime"], "created_at": created_at, "folder_id": folder_id,
    }


# ── Image list ─────────────────────────────────────────────────────────────────
@app.get("/api/images", dependencies=[Depends(require_auth)])
async def list_images(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    folder: str | None = Query(None),
):
    offset = (page - 1) * per_page
    where, params = "", []
    if folder == "none":
        where = "WHERE folder_id IS NULL"
    elif folder:
        where, params = "WHERE folder_id = ?", [folder]

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM images {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, per_page, offset)
        ) as cursor:
            rows = await cursor.fetchall()
        async with db.execute(
            f"SELECT COUNT(*) FROM images {where}", params
        ) as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0

    return {
        "total": total, "page": page, "per_page": per_page,
        "images": [dict(r) for r in rows],
    }


# ── Move image ─────────────────────────────────────────────────────────────────
class ImageMove(BaseModel):
    folder_id: str | None = None


@app.patch("/api/images/{image_id}", dependencies=[Depends(require_auth)])
async def move_image(image_id: str, body: ImageMove):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM images WHERE id = ?", (image_id,)
        ) as cursor:
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="图片不存在")
        if body.folder_id is not None:
            async with db.execute(
                "SELECT 1 FROM folders WHERE id = ?", (body.folder_id,)
            ) as cursor:
                if not await cursor.fetchone():
                    raise HTTPException(status_code=404, detail="文件夹不存在")
        await db.execute(
            "UPDATE images SET folder_id = ? WHERE id = ?",
            (body.folder_id, image_id),
        )
        await db.commit()
    return {"ok": True, "folder_id": body.folder_id}


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
        "model/gltf-binary": ".glb",
    }.get(mime, ".jpg")


class _UploadRejected(Exception):
    """Raised inside the upload worker when bytes are not a supported image."""


def _svg_dimensions(content: bytes):
    """Best-effort width/height from the SVG root element (None if absent)."""
    text = content[:1024].decode("utf-8", "ignore")
    w = re.search(r'\bwidth="(\d+)', text)
    h = re.search(r'\bheight="(\d+)', text)
    return (int(w.group(1)) if w else None, int(h.group(1)) if h else None)


def _make_thumbnail(img: "Image.Image", dest: Path):
    thumb = img.copy()
    thumb.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
    if thumb.mode in ("RGBA", "LA", "P"):
        if thumb.mode != "RGBA":
            thumb = thumb.convert("RGBA")
        bg = Image.new("RGB", thumb.size, (255, 255, 255))
        bg.paste(thumb, mask=thumb.split()[3])
        thumb = bg
    elif thumb.mode != "RGB":
        thumb = thumb.convert("RGB")
    thumb.save(dest, quality=85, optimize=True)


def _process_upload(content: bytes, is_svg: bool, is_glb: bool, file_id: str) -> dict:
    """Validate bytes, then write the original (+ thumbnail for images).

    Runs in a threadpool. Files are written ONLY after validation succeeds, so
    rejected uploads never persist. Raises _UploadRejected on anything that
    isn't a supported file. Returns {filename, width, height, mime}.
    """
    if is_glb:
        # GLB header: magic "glTF" + uint32 version (little-endian) == 2.
        if len(content) < 12 or content[0:4] != b"glTF" \
                or int.from_bytes(content[4:8], "little") != 2:
            raise _UploadRejected("文件内容不是有效的 GLB")
        filename = f"{file_id}.glb"
        (UPLOAD_DIR / filename).write_bytes(content)  # no thumbnail for models
        return {"filename": filename, "width": None, "height": None,
                "mime": "model/gltf-binary"}

    if is_svg:
        head = content.lstrip()[:512].lower()
        if b"<svg" not in head and b"<?xml" not in head:
            raise _UploadRejected("文件内容不是有效的 SVG")
        filename = f"{file_id}.svg"
        (UPLOAD_DIR / filename).write_bytes(content)
        (THUMB_DIR / filename).write_bytes(content)  # SVG is its own thumbnail
        width, height = _svg_dimensions(content)
        return {"filename": filename, "width": width, "height": height, "mime": "image/svg+xml"}

    try:
        with Image.open(io.BytesIO(content)) as src:
            fmt = (src.format or "").upper()
            if fmt == "HEIF":
                # Apple photos: transcode to JPEG so links render in every
                # browser. Orientation is baked into pixels and no EXIF is
                # written — GPS/device metadata must not leak via public URLs.
                w0, h0 = src.size
                if w0 * h0 > MAX_PIXELS:
                    raise _UploadRejected("图片像素数超过限制")
                img = ImageOps.exif_transpose(src) or src
                if img.mode != "RGB":
                    img = img.convert("RGB")
                width, height = img.size
                filename = f"{file_id}.jpg"
                img.save(UPLOAD_DIR / filename, "JPEG", quality=92, optimize=True)
                try:
                    _make_thumbnail(img, THUMB_DIR / filename)
                except Exception:
                    pass
                return {"filename": filename, "width": width, "height": height,
                        "mime": "image/jpeg"}
            mime = PIL_FORMAT_TO_MIME.get(fmt)
            if mime is None:
                raise _UploadRejected(f"不支持的图片格式: {src.format or 'unknown'}")
            w0, h0 = src.size
            if w0 * h0 > MAX_PIXELS:
                raise _UploadRejected("图片像素数超过限制")
            # Honour EXIF orientation so thumbnails and stored dimensions match
            # what browsers render.
            img = ImageOps.exif_transpose(src) or src
            width, height = img.size
            filename = f"{file_id}{_mime_to_ext(mime)}"
            (UPLOAD_DIR / filename).write_bytes(content)
            # Thumbnail is a best-effort nicety: a quirky-but-valid image that
            # fails here still gets stored (client falls back to the full image).
            try:
                _make_thumbnail(img, THUMB_DIR / filename)
            except Exception:
                pass
    except _UploadRejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise _UploadRejected("无法解析为图片") from e

    return {"filename": filename, "width": width, "height": height, "mime": mime}
