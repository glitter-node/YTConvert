import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import PROJECT_ROOT, get_settings, project_path
from app.main import create_app
from app.services.storage import job_file
from app.services.transcoder import ffmpeg_command


def test_factory_and_routes():
    app = create_app()
    paths = set(app.openapi()["paths"])
    assert {"/api/health", "/api/status", "/api/analyze", "/api/formats/{media_id}", "/api/jobs", "/api/jobs/{job_id}", "/api/jobs/{job_id}/events", "/api/download/{job_id}"}.issubset(paths)
    assert app.router.lifespan_context is not None


def test_favicon_routes_and_existing_assets():
    icon = PROJECT_ROOT / "frontend/favicon.ico"
    data = icon.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00" and len(data) > 100
    with TestClient(create_app(), base_url="https://ytconvert.glitter.kr") as client:
        for path, mime in (("/favicon.ico", "image/x-icon"), ("/favicon.svg", "image/svg+xml")):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(mime)
            assert response.headers["cache-control"] == "public, max-age=300"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "img-src 'self'" in response.headers["content-security-policy"]
            assert response.content
        page = client.get("/")
        assert page.status_code == 200 and 'href="/favicon.svg"' in page.text
        assert client.get("/css/app.css").status_code == 200
        assert client.get("/js/app.js").status_code == 200
        assert client.get("/api/status").status_code == 200
        assert client.get("/missing-favicon-route").status_code == 404


def test_settings():
    previous = os.environ.get("APP_PORT")
    try:
        os.environ["APP_PORT"] = "8021"
        settings = get_settings()
        assert settings.app_port == 8021
        assert settings.app_timezone == "Asia/Seoul"
        assert settings.temp_dir == PROJECT_ROOT / "data/temp"
        assert settings.output_dir == PROJECT_ROOT / "data/output"
    finally:
        if previous is None:
            os.environ.pop("APP_PORT", None)
        else:
            os.environ["APP_PORT"] = previous


def test_outside_paths_are_rejected():
    try:
        project_path("../glitterkr")
        raise AssertionError("Parent escape was accepted")
    except ValueError:
        pass
    try:
        project_path("/etc/passwd")
        raise AssertionError("Outside path was accepted")
    except ValueError:
        pass
    try:
        job_file("output", uuid4(), "../escape.mp4")
        raise AssertionError("Invalid filename was accepted")
    except ValueError:
        pass
    settings = get_settings()
    try:
        ffmpeg_command(settings, Path("/etc/passwd"), settings.output_dir / "file.mp4", "mp4")
        raise AssertionError("Outside input was accepted")
    except ValueError:
        pass


def test_external_symlink_is_rejected():
    link = PROJECT_ROOT / "data/temp" / f"test-link-{uuid4().hex}"
    link.symlink_to("/etc/passwd")
    try:
        try:
            project_path(link)
            raise AssertionError("External symlink was accepted")
        except ValueError:
            pass
    finally:
        link.unlink()


def test_stubs_and_errors():
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/analyze", json={"url": "http://127.0.0.1/private"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        response = client.post("/api/analyze", json={"url": "https://user:secret@www.youtube.com/watch?v=example"})
        assert response.status_code == 422
        assert "secret" not in str(response.json())
        response = client.get("/api/health", headers={"Host": "other.example"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_host"
        response = client.get(f"/api/jobs/{uuid4()}/events")
        assert response.status_code == 404
        response = client.get(f"/api/download/{uuid4()}")
        assert response.status_code == 404
        media_id = uuid4()
        job_id = uuid4()
        response = client.get("/api/formats/not-an-id")
        assert response.status_code == 422
        response = client.post("/api/jobs", json={"media_id": str(media_id), "format": "mp4", "resolution": "720p"})
        assert response.status_code == 422
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 404
        response = client.delete(f"/api/jobs/{job_id}")
        assert response.status_code == 404
        response = client.get("/api/unknown")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "http_error"


def test_frontend():
    with TestClient(create_app(), base_url="http://localhost") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert '<html lang="en">' in response.text
        assert client.get("/css/app.css").status_code == 200
        assert client.get("/js/app.js").status_code == 200
