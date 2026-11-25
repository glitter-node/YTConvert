import json
import logging
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import PROJECT_ROOT
from app.core.http_policy import IPBlocklist, cache_policy, sensitive_path
from app.main import create_app


def test_trusted_hosts_and_proxy_header_spoofing():
    app = create_app()
    assert any(item.cls is TrustedHostMiddleware for item in app.user_middleware)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"Host": "ytconvert.glitter.kr"}).status_code == 200
        response = client.get("/api/health", headers={"Host": "evil.example", "X-Forwarded-Host": "ytconvert.glitter.kr"})
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_host"
        assert client.get("/api/health", headers={"X-Forwarded-Host": "evil.example"}).status_code == 200


def test_additional_host_uses_environment_setting():
    prior = os.environ.get("APP_ALLOWED_HOSTS")
    os.environ["APP_ALLOWED_HOSTS"] = "ytconvert.glitter.kr,127.0.0.1,localhost,media.example"
    try:
        with TestClient(create_app(), base_url="http://localhost") as client:
            assert client.get("/api/health", headers={"Host": "media.example"}).status_code == 200
            assert client.get("/api/health", headers={"Host": "unknown.example"}).status_code == 400
    finally:
        if prior is None:
            os.environ.pop("APP_ALLOWED_HOSTS", None)
        else:
            os.environ["APP_ALLOWED_HOSTS"] = prior


def test_sensitive_paths_and_security_headers():
    for path in ("/.env", "/.git/config", "/.svn/entries", "/data/config/env/ytconvert/.env", "/app/main.py", "/backup.sql", "/assets/%252eenv", "/assets/config.yaml", "/assets/archive.backup", "/assets/.gitignore"):
        assert sensitive_path(path)
    with TestClient(create_app(), base_url="http://localhost") as client:
        for path in ("/.env", "/.git/config", "/data/config/env/ytconvert/.env", "/assets/%252eenv", "/backup.sql"):
            response = client.get(path)
            assert response.status_code == 404 and response.headers["Cache-Control"] == "no-store"
        response = client.get("/api/health")
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert response.headers["Cross-Origin-Embedder-Policy"] == "unsafe-none"
        assert "geolocation=()" in response.headers["Permissions-Policy"]
        csp = response.headers["Content-Security-Policy"]
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "connect-src 'self'" in csp and "https://i.ytimg.com" in csp
        assert response.headers["Cache-Control"] == "no-store"
        assert client.get("/js/app.js").headers["Cache-Control"] == "public, max-age=300"
        assert client.get("/css/app.css").status_code == 200
        assert client.get("/api/status").headers["Cache-Control"] == "no-store"
        assert cache_policy("/js/app.0123456789abcdef.js", 200) == "public, max-age=31536000, immutable"


def test_ipv4_ipv6_blocklist_reload_and_spoofed_forwarding():
    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "data/temp") as directory:
        path = Path(directory) / "blocked.json"
        path.write_text(json.dumps(["203.0.113.0/24", "2001:db8::/32"]), encoding="utf-8")
        prior = os.environ.get("APP_IP_BLOCKLIST_FILE")
        os.environ["APP_IP_BLOCKLIST_FILE"] = str(path)
        try:
            app = create_app()
            with TestClient(app, base_url="http://localhost", client=("203.0.113.9", 50000)) as client:
                assert client.get("/api/health").status_code == 403
            with TestClient(app, base_url="http://localhost", client=("2001:db8::9", 50000)) as client:
                assert client.get("/api/health").status_code == 403
            with TestClient(app, base_url="http://localhost", client=("198.51.100.9", 50000)) as client:
                assert client.get("/api/health", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200
                path.write_text("{invalid", encoding="utf-8")
                assert client.get("/api/health").status_code == 200
            with TestClient(app, base_url="http://localhost", client=("203.0.113.9", 50000)) as client:
                assert client.get("/api/health").status_code == 403
            path.write_text(json.dumps(["198.51.100.0/24"]), encoding="utf-8")
            with TestClient(app, base_url="http://localhost", client=("198.51.100.9", 50000)) as client:
                assert client.get("/api/health").status_code == 403
            with TestClient(app, base_url="http://localhost", client=("203.0.113.9", 50000)) as client:
                assert client.get("/api/health").status_code == 200
        finally:
            if prior is None:
                os.environ.pop("APP_IP_BLOCKLIST_FILE", None)
            else:
                os.environ["APP_IP_BLOCKLIST_FILE"] = prior


def test_invalid_initial_blocklist_fails_closed():
    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "data/temp") as directory:
        path = Path(directory) / "blocked.json"
        path.write_text('not json', encoding="utf-8")
        blocklist = IPBlocklist(path, logging.getLogger("ytconvert.test"))
        assert blocklist.blocks("203.0.113.9")
        path.write_text("[]", encoding="utf-8")
        assert not blocklist.blocks("203.0.113.9")


def test_openapi_requires_explicit_development_setting():
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert all(client.get(path).status_code == 404 for path in ("/openapi.json", "/docs", "/redoc"))
    prior = {key: os.environ.get(key) for key in ("APP_ENV", "APP_ENABLE_OPENAPI")}
    try:
        os.environ["APP_ENV"] = "production"
        os.environ["APP_ENABLE_OPENAPI"] = "true"
        with TestClient(create_app(), base_url="http://localhost") as client:
            assert all(client.get(path).status_code == 404 for path in ("/openapi.json", "/docs", "/redoc"))
        os.environ["APP_ENV"] = "development"
        with TestClient(create_app(), base_url="http://localhost") as client:
            assert client.get("/openapi.json").status_code == 200
            for path in ("/docs", "/redoc"):
                response = client.get(path)
                assert response.status_code == 200
                assert "nonce-" in response.headers["Content-Security-Policy"]
                assert 'script nonce="' in response.text
                assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
