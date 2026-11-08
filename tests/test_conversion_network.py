import os
from ipaddress import ip_network

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import get_settings
from app.main import create_app


def _settings(value):
    previous = os.environ.get("INTERNAL_IP_RANGES")
    try:
        if value is None:
            os.environ.pop("INTERNAL_IP_RANGES", None)
        else:
            os.environ["INTERNAL_IP_RANGES"] = value
        return get_settings()
    finally:
        if previous is None:
            os.environ.pop("INTERNAL_IP_RANGES", None)
        else:
            os.environ["INTERNAL_IP_RANGES"] = previous


def _job_response(app, address, headers=None, proxy=False, payload=None):
    target = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1") if proxy else app
    with TestClient(target, base_url="http://localhost", client=(address, 50000)) as client:
        body = payload if payload is not None else {"media_id": "dQw4w9WgXcQ", "format": "mp4", "resolution": "720p", "confirm_rights": True}
        return client.post("/api/jobs", json=body, headers=headers or {})


def test_conversion_ranges_and_invalid_configuration():
    import app.core.config as config
    original = config._env_file_values
    config._env_file_values = lambda: {}
    try:
        assert _settings(None).internal_ip_ranges == ()
    finally:
        config._env_file_values = original
    assert _settings("").internal_ip_ranges == ()
    ranges = _settings("127.0.0.1/32,192.168.0.0/16,10.0.0.0/8,::1/128")
    assert len(ranges.internal_ip_ranges) == 4
    assert ip_network("192.168.0.0/16") in ranges.internal_ip_ranges
    for value in ("127.0.0.1,not-an-ip", "10.0.0.0/8,", "10.0.0.0/99"):
        try:
            _settings(value)
            raise AssertionError("Invalid range was accepted")
        except ValueError as exc:
            assert "INTERNAL_IP_RANGES" in str(exc)


def test_conversion_gate_denies_before_analysis_and_queueing():
    app = create_app()
    app.state.settings = _settings("127.0.0.1/32,192.168.0.0/16,::1/128")
    calls = []

    async def forbidden_analysis(media_id):
        calls.append(media_id)
        raise AssertionError("Analysis must not run")

    app.state.metadata_service.get = forbidden_analysis
    for address, expected in (("127.0.0.1", 503), ("192.168.2.9", 503), ("::1", 503), ("::ffff:192.168.2.9", 503), ("198.51.100.9", 403), ("testclient", 403)):
        response = _job_response(app, address)
        assert response.status_code == expected, (address, response.text)
        if expected == 403:
            assert response.json()["error"] == {"code": "network_restricted", "message": "Conversion is restricted to authorized networks", "details": None}
    assert calls == [] and app.state.job_manager.records == {} and app.state.job_manager.queue.empty()
    app.state.settings = _settings("")
    assert _job_response(app, "127.0.0.1").status_code == 403
    assert calls == [] and app.state.job_manager.queue.empty()


def test_proxy_header_trust_boundary_and_analysis_unchanged():
    app = create_app()
    app.state.settings = _settings("10.0.0.0/8")
    spoofed = {"X-Forwarded-For": "10.2.3.4", "X-Real-IP": "10.2.3.4", "Forwarded": "for=10.2.3.4"}
    assert _job_response(app, "198.51.100.9", spoofed, proxy=True).status_code == 403
    assert _job_response(app, "127.0.0.1", {"X-Forwarded-For": "198.51.100.9", **{key: value for key, value in spoofed.items() if key != "X-Forwarded-For"}}, proxy=True).status_code == 403
    assert _job_response(app, "127.0.0.1", {"X-Forwarded-For": "10.2.3.4, 198.51.100.9"}, proxy=True).status_code == 403
    assert _job_response(app, "127.0.0.1", {"X-Forwarded-For": "10.2.3.4"}, proxy=True).status_code == 503
    with TestClient(app, base_url="http://localhost", client=("198.51.100.9", 50000)) as client:
        assert client.post("/api/analyze", json={"url": "https://example.test/video"}).status_code == 422
        assert client.get("/api/jobs").status_code == 200


def test_network_dependency_precedes_body_validation():
    app = create_app()
    app.state.settings = _settings("10.0.0.0/8")
    app.state.job_manager.ready_reason = None
    calls = []

    async def forbidden_analysis(media_id):
        calls.append(media_id)
        raise AssertionError("Analysis must not run")

    app.state.metadata_service.get = forbidden_analysis
    bodies = ({}, {"media_id": "invalid", "format": "mp4"}, {"media_id": "dQw4w9WgXcQ", "format": "mp4", "confirm_rights": True})
    for body in bodies:
        response = _job_response(app, "172.236.157.187", payload=body)
        assert response.status_code == 403 and response.json()["error"]["code"] == "network_restricted"
    assert _job_response(app, "172.236.157.187", {"X-Forwarded-For": "10.11.12.10"}, proxy=True, payload={}).status_code == 403
    for body in bodies[:2]:
        response = _job_response(app, "10.11.12.10", payload=body)
        assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
    with TestClient(app, base_url="http://localhost", client=("172.236.157.187", 50000)) as client:
        malformed = client.post("/api/jobs", content="{", headers={"Content-Type": "application/json"})
        assert malformed.status_code == 422 and malformed.json()["error"]["code"] == "validation_error"
    assert calls == [] and app.state.job_manager.records == {} and app.state.job_manager.queue.empty()
