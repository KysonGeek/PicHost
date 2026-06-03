"""Smoke tests: confirm the test harness and existing happy-path behavior."""


def test_login_returns_token(client):
    r = client.post("/api/auth/login", json={"password": "test-secret-pw"})
    assert r.status_code == 200
    assert r.json().get("token")


def test_login_wrong_password_rejected(client):
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 401


def test_upload_png_roundtrip(client, auth, make_png):
    r = client.post(
        "/api/upload", headers=auth,
        files={"file": ("x.png", make_png(10, 10), "image/png")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mime_type"] == "image/png"
    assert data["width"] == 10 and data["height"] == 10


def test_upload_requires_auth(client, make_png):
    r = client.post(
        "/api/upload",
        files={"file": ("x.png", make_png(), "image/png")},
    )
    assert r.status_code == 401
