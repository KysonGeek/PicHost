# PicHost

私人图床，基于 FastAPI + 本地存储，支持密钥登录。
![example](example.png)

## 功能

- 拖拽 / 点击 / Ctrl+V 粘贴上传
- 上传后自动生成直链、BBCode、Markdown、HTML 四种格式，一键复制
- 自动生成缩略图（400×400）
- 图片画廊，支持大图预览、复制链接、删除
- HMAC Token 鉴权，密钥保存在本地，Token 有效期 30 天
- 图片直链公开访问，方便外链分享

支持格式：JPG · PNG · GIF · WebP · BMP · SVG，单文件最大 20MB

## 部署

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置密码

```bash
echo "PICHOST_PASSWORD=yourpassword" > .env
chmod 600 .env
```

### 3. 启动

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

访问 `http://localhost:8000`，输入密码登录即可使用。

## systemd 服务

```ini
# /etc/systemd/system/pichost.service
[Unit]
Description=PicHost Image Hosting Service
After=network.target

[Service]
WorkingDirectory=/opt/app/img
ExecStart=/usr/local/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now pichost
```

## Caddy 反代

```
img.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

## 目录结构

```
.
├── main.py          # FastAPI 后端
├── requirements.txt
├── .env             # 密码（不入库）
├── images.db        # SQLite 元数据（不入库）
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
└── uploads/         # 图片存储（不入库）
    └── thumbs/      # 缩略图
```

## API

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/auth/login` | — | 密码换 Token |
| POST | `/api/upload` | ✓ | 上传图片 |
| GET | `/api/images` | ✓ | 图片列表（分页） |
| DELETE | `/api/images/:id` | ✓ | 删除图片 |
| GET | `/uploads/*` | — | 图片直链 |
