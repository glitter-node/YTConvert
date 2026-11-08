import asyncio
import json
from time import sleep
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.security import RateLimiter, normalize_youtube_url
from app.main import create_app
from app.services import metadata
from app.services.metadata import MediaAnalysisError, MetadataService, _snapshot_from_info


VIDEO_ID = "dQw4w9WgXcQ"


def sample_info():
    return {
        "id": VIDEO_ID,
        "title": "Example Video",
        "description": "Example description",
        "channel": "Example Channel",
        "channel_url": "https://www.youtube.com/channel/Example",
        "duration": 212,
        "thumbnail": f"https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg",
        "upload_date": "20260101",
        "view_count": 1000000,
        "live_status": "not_live",
        "availability": "public",
        "formats": [
            {"format_id": "137", "ext": "mp4", "container": "mp4_dash", "vcodec": "avc1.640028", "acodec": "none", "width": 1920, "height": 1080, "fps": 30, "vbr": 4500, "tbr": 4500, "filesize_approx": 120000000, "dynamic_range": "SDR"},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128, "filesize": 10000},
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 360, "abr": 96},
        ],
    }


def sample_snapshot():
    return _snapshot_from_info(VIDEO_ID, sample_info())


def test_supported_urls_and_normalization():
    urls = [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com/watch?v={VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42s&list=PL123&index=2",
        f"https://youtu.be/{VIDEO_ID}?si=share-token",
        f"https://www.youtube.com/shorts/{VIDEO_ID}?feature=share",
    ]
    for url in urls:
        assert normalize_youtube_url(url) == (VIDEO_ID, f"https://www.youtube.com/watch?v={VIDEO_ID}")


def test_bad_urls_are_rejected():
    urls = [
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://127.0.0.1/watch?v=dQw4w9WgXcQ",
        "https://localhost/watch?v=dQw4w9WgXcQ",
        "https://10.0.0.1/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://user:secret@www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=short",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?v={VIDEO_ID}",
        "https://www.youtube.com/watch?list=PL123",
        "https://www.youtube.com/watch?v=" + "x" * 2100,
    ]
    for url in urls:
        try:
            normalize_youtube_url(url)
            raise AssertionError(f"Accepted invalid URL: {url[:60]}")
        except ValueError:
            pass


def test_metadata_and_format_mapping():
    snapshot = sample_snapshot()
    assert snapshot.media.title == "Example Video"
    assert snapshot.media.duration == 212
    assert snapshot.media.format_count == 3
    assert snapshot.media.webpage_url.endswith(VIDEO_ID)
    formats = snapshot.formats.formats
    assert formats[0].has_video and not formats[0].has_audio
    assert formats[1].has_audio and not formats[1].has_video
    assert formats[2].has_audio and formats[2].has_video
    assert formats[0].extension == "mp4" and formats[0].video_codec == "avc1.640028"
    assert formats[0].filesize is None and formats[0].filesize_approx == 120000000
    assert any(option.resolution == "1080p" and option.requires_audio_merge for option in snapshot.formats.options)
    assert all(option.resolution != "2160p" for option in snapshot.formats.options)
    assert any(option.format.value == "mp3" for option in snapshot.formats.options)
    assert all(item.extension != "mp3" for item in formats)


def test_untrusted_metadata_is_filtered():
    info = sample_info()
    info["description"] = "a" * 10000
    info["thumbnail"] = "http://127.0.0.1/private"
    info["channel_url"] = "https://evil.example/channel"
    snapshot = _snapshot_from_info(VIDEO_ID, info)
    assert len(snapshot.media.description) == 4000
    assert snapshot.media.thumbnail is None
    assert snapshot.media.channel_url is None
    info["entries"] = []
    try:
        _snapshot_from_info(VIDEO_ID, info)
        raise AssertionError("Playlist was accepted")
    except MediaAnalysisError:
        pass


def test_yt_dlp_is_called_without_download():
    calls = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            calls["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download, ie_key):
            calls["url"] = url
            calls["download"] = download
            calls["ie_key"] = ie_key
            return sample_info()

    original = metadata._yt_dlp_module
    metadata._yt_dlp_module = lambda: SimpleNamespace(YoutubeDL=FakeYoutubeDL, utils=SimpleNamespace(DownloadError=Exception))
    try:
        result = metadata.extract_youtube(VIDEO_ID)
    finally:
        metadata._yt_dlp_module = original
    assert result.media.media_id == VIDEO_ID
    assert calls["url"] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert calls["download"] is False and calls["ie_key"] == "Youtube"
    assert calls["options"]["skip_download"] is True
    assert calls["options"]["noplaylist"] is True
    assert calls["options"]["socket_timeout"] == 8
    assert calls["options"]["js_runtimes"] == {}
    assert calls["options"]["remote_components"] == []


def test_cache_reuse_and_expiry():
    calls = []

    def extract(media_id):
        calls.append(media_id)
        return sample_snapshot()

    async def check():
        service = MetadataService(extractor=extract, ttl_seconds=600)
        await service.get(VIDEO_ID)
        await service.get(VIDEO_ID)
        assert len(calls) == 1
        expiring = MetadataService(extractor=extract, ttl_seconds=0)
        await expiring.get(VIDEO_ID)
        await expiring.get(VIDEO_ID)
        assert len(calls) == 3

    asyncio.run(check())


def test_concurrent_requests_share_one_extraction():
    calls = []

    def extract(media_id):
        calls.append(media_id)
        sleep(0.02)
        return sample_snapshot()

    async def check():
        service = MetadataService(extractor=extract)
        results = await asyncio.gather(*(service.get(VIDEO_ID) for _ in range(5)))
        assert all(item.media.media_id == VIDEO_ID for item in results)

    asyncio.run(check())
    assert len(calls) == 1


def test_extraction_failure_and_timeout():
    def failure(media_id):
        raise MediaAnalysisError(502, "extraction_failed", "Video metadata could not be retrieved")

    async def check():
        service = MetadataService(extractor=failure)
        for _ in range(2):
            try:
                await service.get(VIDEO_ID)
                raise AssertionError("Failure was hidden")
            except MediaAnalysisError as exc:
                assert exc.code == "extraction_failed"
        slow = MetadataService(extractor=lambda media_id: (sleep(0.03), sample_snapshot())[1], timeout_seconds=0.001)
        try:
            await slow.get(VIDEO_ID)
            raise AssertionError("Timeout was hidden")
        except MediaAnalysisError as exc:
            assert exc.status_code == 504
        await asyncio.sleep(0.04)

    asyncio.run(check())


def test_api_success_and_error():
    app = create_app()
    app.state.metadata_service = MetadataService(extractor=lambda media_id: sample_snapshot())
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/analyze", json={"url": f"https://youtu.be/{VIDEO_ID}"})
        assert response.status_code == 200
        assert response.json()["media_id"] == VIDEO_ID
        response = client.get(f"/api/formats/{VIDEO_ID}")
        assert response.status_code == 200
        assert len(response.json()["formats"]) == 3
    app = create_app()
    app.state.metadata_service = MetadataService(extractor=lambda media_id: (_ for _ in ()).throw(MediaAnalysisError(403, "private_video", "This video is private")))
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/analyze", json={"url": f"https://www.youtube.com/watch?v={VIDEO_ID}"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "private_video"
        assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_browser_json_matches_manual_analysis_request():
    raw_url = f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42s&list=PL123&index=2"
    seen = []
    app = create_app()
    app.state.metadata_service = MetadataService(extractor=lambda media_id: (seen.append(media_id), sample_snapshot())[1])
    with TestClient(app, base_url="https://ytconvert.glitter.kr") as client:
        browser = client.post("/api/analyze", content=json.dumps({"url": raw_url}), headers={"Accept": "application/json", "Content-Type": "application/json"})
        assert browser.status_code == 200
        assert browser.json()["media_id"] == VIDEO_ID
        assert browser.json()["webpage_url"] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
        assert browser.headers["Content-Security-Policy"]
        assert browser.headers["X-Frame-Options"] == "DENY"
        assert browser.headers["Cache-Control"] == "no-store"
        manual = client.post("/api/analyze", json={"url": f"https://www.youtube.com/watch?v={VIDEO_ID}"})
        assert manual.status_code == 200
        assert manual.json() == browser.json()
        assert seen == [VIDEO_ID]
        invalid = client.post("/api/analyze", content=json.dumps({"url": "https://evil.example/watch?v=" + VIDEO_ID}), headers={"Accept": "application/json", "Content-Type": "application/json"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"
        assert invalid.json()["error"]["message"] == "Invalid request data"
        assert invalid.json()["error"]["details"][0]["location"] == ["body", "url"]
        assert "evil.example" not in str(invalid.json())


def test_network_guard_checks_service_and_scheme_without_dns_policy():
    assert metadata._safe_network_target("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert metadata._safe_network_target("https://rr1.googlevideo.com/videoplayback")
    assert not metadata._safe_network_target("https://127.0.0.1/video")
    assert not metadata._safe_network_target("https://evil.example/video")
    assert not metadata._safe_network_target("http://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_rate_limiter():
    limiter = RateLimiter(limit=2, window_seconds=60)
    assert limiter.allow("client-a")
    assert limiter.allow("client-a")
    assert not limiter.allow("client-a")
    assert limiter.allow("client-b")
