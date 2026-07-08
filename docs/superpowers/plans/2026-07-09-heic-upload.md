# HEIC/HEIF 上传支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持上传苹果 HEIC/HEIF 照片:服务端转成无 EXIF 的高质量 JPEG 存储,外链全浏览器可显示,GPS/设备隐私信息不外泄。

**Architecture:** 引入 pillow-heif 让 Pillow 能解码 HEIF;`_process_upload` 在识别到 `format == "HEIF"` 时走转码分支(exif_transpose 烘入方向 → RGB → JPEG quality=92、不写 EXIF),入库即普通 JPEG(`{file_id}.jpg` / `image/jpeg`),前端与存储层零特殊逻辑。

**Tech Stack:** pillow-heif 1.4.0(连带 Pillow 升至 12.3.0,已在本机验证 54 测试全绿),FastAPI,原生 JS。

**Spec:** `docs/superpowers/specs/2026-07-08-heic-upload-design.md`

## Global Constraints

- 依赖:`requirements.txt` 中 `Pillow==12.3.0`、新增 `pillow-heif==1.4.0`(本机已安装并验证 HEIF 编解码往返)。
- 转码参数:JPEG `quality=92, optimize=True`;save 时**不传 exif**(即去除全部元数据);方向先 `ImageOps.exif_transpose` 烘入像素;非 RGB 转 RGB。
- 入库:`mime_type = "image/jpeg"`、文件名 `{file_id}.jpg`、宽高为转码后尺寸;`orig_name` 保留用户原始 `.heic` 文件名。uploads/ 与 thumbs/ 不得出现 `.heic` 文件。
- 放行初筛:`ALLOWED_MIME` 加 `image/heic`、`image/heif`;或文件名(小写)以 `.heic`/`.heif` 结尾且 content_type ∈ {application/octet-stream, 空, None}。
- 大小上限沿用图片 20MB;`MAX_PIXELS` 像素防护对 HEIF 同样生效;解码失败 → 现有 415「无法解析为图片」,不落盘。
- 既有 glb/SVG/普通图片路径不得改动行为。
- 测试命令 `python3 -m pytest`;每任务结束全绿再提交。当前基线 54 个测试。
- 生产提醒(写入 README):部署机需 `pip install -r requirements.txt` 并重启服务才生效。

---

### Task 1: 后端 —— HEIF 解码注册、放行与转码

**Files:**
- Modify: `requirements.txt`(Pillow 版本 + pillow-heif)
- Modify: `main.py:19` 附近(import + register)、`main.py:31-35`(ALLOWED_MIME/EXT)、`main.py:273-292`(upload 初筛)、`main.py:464+`(`_process_upload` HEIF 分支)
- Test: `tests/test_heic.py`(新建)

**Interfaces:**
- Consumes: 既有 `_process_upload(content, is_svg, is_glb, file_id)`、`_make_thumbnail`、`_UploadRejected`。
- Produces: HEIC 上传响应 `{mime_type: "image/jpeg", filename: "*.jpg", width/height: 转码后}`;Task 2(前端)不依赖新后端接口,仅依赖此行为。

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_heic.py`:

```python
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
```

关于方向断言的说明:无论 pillow-heif 在解码时是否已自动应用方向,`exif_transpose` 之后结果都应是旋转后的 16×32——若实测发现该库行为导致断言失败,报告实际行为而不是改断言迁就(那说明实现分支有误)。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_heic.py -v`
Expected: FAIL(415「不支持的文件类型: image/heic」——初筛未放行)

- [ ] **Step 3: 实现**

`requirements.txt`:`Pillow==12.2.0` 改为 `Pillow==12.3.0`,末尾加 `pillow-heif==1.4.0`。

`main.py` import 区,`from PIL import ...` 之后加:

```python
from pillow_heif import register_heif_opener

# Let Pillow decode Apple's HEIC/HEIF; uploads are transcoded to JPEG.
register_heif_opener()
```

`ALLOWED_MIME` 加 `"image/heic", "image/heif",`;`ALLOWED_EXT` 加 `".heic", ".heif"`。

upload 初筛(main.py:273-282)改为:

```python
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
```

`_process_upload`(main.py:464+)的 Pillow 分支里,把开头几行:

```python
    try:
        with Image.open(io.BytesIO(content)) as src:
            mime = PIL_FORMAT_TO_MIME.get((src.format or "").upper())
```

改为(HEIF 优先判断,其余不动):

```python
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
```

(`PIL_FORMAT_TO_MIME` 不加 HEIF——HEIF 不作为存储格式;伪造字节走 `UnidentifiedImageError` → 现有 415「无法解析为图片」。)

- [ ] **Step 4: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全部 PASS(54 旧 + 7 新 = 61)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt main.py tests/test_heic.py
git commit -m "feat(api): HEIC/HEIF uploads transcode to EXIF-free JPEG"
```

---

### Task 2: 前端接受 + README

**Files:**
- Modify: `static/app.js`(ALLOWED_UPLOAD_TYPES、扩展名判定、拖拽/粘贴过滤)
- Modify: `static/index.html`(accept 属性、提示文案)
- Modify: `README.md`(格式行、功能行、上传校验句、部署提醒)

**Interfaces:**
- Consumes: Task 1 的后端行为(HEIC 转 JPEG)。
- Produces: 无后续任务。

- [ ] **Step 1: app.js**

`isGlbFile` 定义(约 33 行)下方加统一的扩展名判定,并保留 `isGlbFile`(uploadSingle 的 100MB 分支仍用它):

```js
function hasSniffableExt(file) {
  const n = (file.name || '').toLowerCase();
  return n.endsWith('.glb') || n.endsWith('.heic') || n.endsWith('.heif');
}
```

`ALLOWED_UPLOAD_TYPES` 字面量加 `'image/heic', 'image/heif',`。

拖拽过滤(约 57 行)与粘贴过滤(约 75 行)里的 `isGlbFile(f)` 都改为 `hasSniffableExt(f)`:

```js
  const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('image/') || hasSniffableExt(f));
```

```js
    .filter(f => f.type.startsWith('image/') || hasSniffableExt(f));
```

(`uploadSingle` 无需改动:heic 的 type 是 `image/heic`(已入白名单)或空(预检自动放行);大小走图片 20MB 分支,正确。)

- [ ] **Step 2: index.html**

`accept="image/*,.glb"` 改为 `accept="image/*,.glb,.heic,.heif"`;格式提示行改为:

```html
          <p class="upload-hint-sub">支持 JPG · PNG · GIF · WebP · BMP · SVG · HEIC · GLB &nbsp;|&nbsp; 图片最大 20MB · 模型最大 100MB</p>
```

- [ ] **Step 3: README**

- 「支持格式」行改为:`支持格式：JPG · PNG · GIF · WebP · BMP · SVG（最大 20MB）· HEIC/HEIF（转 JPEG 存储）· GLB 3D 模型（最大 100MB）`
- 功能列表加一行:`- HEIC（苹果照片）自动转为 JPEG 存储，并去除 EXIF 元数据（GPS/设备信息不外泄）`
- 「上传校验」句尾追加:`HEIC 上传会转码为 JPEG 并去除全部 EXIF 元数据。`
- 部署段(pip install 之后)加提醒:`> 升级到支持 HEIC 的版本后,需重新执行 pip install -r requirements.txt 并重启服务。`

- [ ] **Step 4: 验证 + Commit**

Run: `python3 -m pytest tests/ -q` → 61 passed
Run: `node --check static/app.js` → 通过

```bash
git add static/app.js static/index.html README.md
git commit -m "feat(web): accept HEIC/HEIF in picker, drag-drop and paste; README"
```
