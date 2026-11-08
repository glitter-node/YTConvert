from fastapi.testclient import TestClient

from app.main import create_app


def test_health():
    with TestClient(create_app(), base_url="http://localhost") as client:
        response = client.get("/api/health", headers={"Host": "ytconvert.glitter.kr"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_status():
    with TestClient(create_app(), base_url="http://localhost") as client:
        response = client.get("/api/status", headers={"Host": "ytconvert.glitter.kr"})
    assert response.status_code == 200
    assert response.json()["service"] == "YTConvert"
    assert response.json()["capabilities"]["conversion_worker"] == "unavailable"
