# PicHost 苹果照片(HEIC/HEIF)上传设计

日期:2026-07-08
状态:已确认
前置:在统一对话框分支(feat/app-dialogs)合并后实施。

## 目标

支持上传 iPhone/Mac 的 HEIC/HEIF 照片:服务端转成 JPEG 存储,外链在所有浏览器可显示;转码时去除 EXIF 元数据(GPS/设备信息不随公开外链泄露)。

## 关键决策

- **转 JPEG 存储**:HEIC 仅 Safari 可显示,而图床核心用途是外链分享。上传时解码 HEIC → 高质量 JPEG(quality=92);不保留 HEIC 原文件。
- **去除元数据**:EXIF 方向先烘入像素(`exif_transpose`),转出的 JPEG 不写任何 EXIF——拍攞时间、设备型号、GPS 全部去除。
- **对系统其余部分透明**:入库记录 `mime_type = image/jpeg`、文件名 `{file_id}.jpg`、尺寸为转码后像素;`orig_name` 保留用户的 `xxx.heic` 原名。前端展示/链接/文件夹/缩略图零特殊逻辑。

## 依赖

`requirements.txt` 新增 `pillow-heif`(提供预编译 wheel);main.py 启动时:

```python
from pillow_heif import register_heif_opener
register_heif_opener()
```

此后 `Image.open` 可识别 HEIC/HEIF,`src.format` 为 `"HEIF"`。

## 后端

### 放行初筛

- `ALLOWED_MIME` 加 `image/heic`、`image/heif`。
- 与 glb 同理,浏览器/系统可能报 `application/octet-stream` 或空 content_type:文件名(小写)以 `.heic` 或 `.heif` 结尾且 content_type ∈ {`application/octet-stream`, 空, None} 时同样放行,最终以字节解码为准。

### 转码分支(_process_upload)

Pillow 打开且 `(src.format or "").upper() == "HEIF"` 时:

1. 像素数检查(`MAX_PIXELS`)照常。
2. `ImageOps.exif_transpose` 烘入方向。
3. 转 `RGB`(丢 alpha,HEIC 照片本就无 alpha)。
4. `save(dest, "JPEG", quality=92, optimize=True)` 到 `{file_id}.jpg`——不传 exif 参数,即不写元数据。
5. 正常生成缩略图(既有 `_make_thumbnail`)。
6. 返回 `{filename: "{file_id}.jpg", width/height: 转码后尺寸, mime: "image/jpeg"}`。

其余图片格式路径不变(原字节直存)。`PIL_FORMAT_TO_MIME` 不加 HEIF(HEIF 不作为存储格式存在)。

### 限制与错误

- 大小上限沿用图片 20MB;解码失败/伪装字节 → 现有 415「无法解析为图片」,不落盘。
- 转码在 `run_in_threadpool` 中执行(既有结构),不阻塞事件循环。

## 前端

- `ALLOWED_UPLOAD_TYPES` 加 `image/heic`、`image/heif`。
- 拖拽/粘贴过滤器放行 `.heic`/`.heif` 后缀(与 .glb 的做法一致,统一为一个 `isSpecialUpload`/扩展判定即可,实现计划里定名)。
- `<input accept>` 保持 `image/*,.glb` 再加 `,.heic,.heif`。
- 上传提示文案:格式列表加 HEIC。
- 展示/链接/灯箱零改动(存储即 JPEG)。

## 测试

`tests/test_heic.py`:

- `make_heic` fixture:用 pillow-heif 把 Pillow 图像编码成真实 HEIC 字节;可选注入 EXIF 方向与 GPS。
- 以 `image/heic` 上传:200,响应 `mime_type == "image/jpeg"`、filename 以 `.jpg` 结尾、`orig_name` 保留 `.heic` 原名;uploads/ 与 thumbs/ 均有 `.jpg` 文件,无 `.heic` 文件。
- 以 `application/octet-stream` + `.heic` 文件名上传:200。
- 带 EXIF Orientation=6 的 HEIC:转码后 width/height 为旋转后的值。
- 带 GPS EXIF 的 HEIC:转出的 JPEG 用 Pillow 读回 `getexif()` 为空(隐私断言)。
- 伪造字节(非 HEIC 内容 + .heic 名/heic 类型):415,不落盘。
- 转码后的图参与文件夹/筛选(一条冒烟即可,复用既有 helper)。

## 边界(明确不做)

- Live Photo 的视频部分(.mov)不支持。
- 不保留 HEIC 原文件,无「下载原图」概念。
- AVIF 等其他新格式不在本次范围。
