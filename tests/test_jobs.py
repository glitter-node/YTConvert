import asyncio
import json
import logging
import secrets
import signal
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from time import sleep
from uuid import UUID

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.schemas.api import CreateJobRequest, OutputFormat
from app.services.formats import build_formats_response, select_streams
from app.services.encoders import REQUIRED_ENCODERS, detect_encoders, unsupported_reason
from app.services.jobs import JobError, JobManager, select_source, worker_unavailable_reason
from app.services.metadata import MetadataService, _snapshot_from_info
from app.services.storage import job_directory, job_file
from app.services.transcoder import ffmpeg_command, probe_media, probe_video_frames
from app.services import transcoder
from tests.test_metadata import VIDEO_ID, sample_info, sample_snapshot


def _manager(base: Path, **changes):
    settings = replace(get_settings(), jobs_dir=base / "jobs", media_worker_enabled=False, **changes)
    manager = JobManager(settings)
    manager.ready_reason = None
    manager.encoders = frozenset().union(*REQUIRED_ENCODERS.values())
    manager.encoder_reason = None
    return manager


def _request(format="mp4", resolution="1080p", audio_bitrate_kbps=None):
    return CreateJobRequest(media_id=VIDEO_ID, format=format, resolution=resolution, audio_bitrate_kbps=audio_bitrate_kbps, confirm_rights=True)


def test_format_filtering_and_dynamic_heights():
    raw = sample_info()["formats"] + [
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},
        {"format_id": "sb4", "ext": "jpg", "vcodec": "none", "acodec": "none"},
        {"format_id": "bad", "ext": "mp4", "vcodec": "none", "acodec": "none"},
        {"format_id": "240", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 240},
        {"format_id": "240-duplicate", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 240},
    ]
    result = build_formats_response(VIDEO_ID, raw)
    assert len(result.formats) == 5
    assert all(item.format_id not in {"sb0", "sb4", "bad"} for item in result.formats)
    assert sum(item.format.value == "mp4" and item.resolution == "240p" for item in result.options) == 1
    assert any(item.resolution == "1080p" for item in result.options)


def test_quality_options_follow_actual_usable_heights():
    audio = {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}
    def video(height):
        return {"format_id": f"v{height}", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": height}
    for heights in ((144, 360, 480), (144, 360, 480, 720, 1080), (144, 480)):
        result = build_formats_response(VIDEO_ID, [video(height) for height in heights] + [audio])
        actual = [option.resolution for option in result.options if option.format.value == "mp4"]
        assert actual == [f"{height}p" for height in heights]
        assert all(option.requires_audio_merge for option in result.options if option.format.value == "mp4")


def test_1080_av1_aac_selection_matches_transcode_command():
    info = sample_info()
    info["formats"] = [
        {"format_id": "399-sr", "ext": "mp4", "container": "mp4_dash", "vcodec": "av01.0.08M.08", "acodec": "none", "width": 1440, "height": 1080, "fps": 30, "vbr": 447.305},
        {"format_id": "140", "ext": "m4a", "container": "m4a_dash", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129.512},
    ]
    snapshot = _snapshot_from_info(VIDEO_ID, info)
    assert [(option.format.value, option.resolution) for option in snapshot.formats.options if option.format.value == "mp4"] == [("mp4", "1080p")]
    assert select_source(snapshot, _request()) == ("399-sr", "140")
    settings = get_settings()
    command = ffmpeg_command(settings, settings.temp_dir / "source.mkv", settings.output_dir / "result.mp4", "mp4", video_height=1080, source_height=1080, source_video_codec="av1", source_audio_codec="aac")
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "-vf" not in command
    assert command[command.index("-map") + 1:command.index("-map") + 4] == ["0:v:0", "-map", "0:a:0"]


def test_quality_options_require_compatible_video_and_audio():
    video = {"format_id": "v480", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 480}
    audio = {"format_id": "a128", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}
    combined = {"format_id": "c360", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 360, "abr": 96}
    assert not [item for item in build_formats_response(VIDEO_ID, [video]).options if item.format.value == "mp4"]
    assert not [item for item in build_formats_response(VIDEO_ID, [audio]).options if item.format.value == "mp4"]
    assert not [item for item in build_formats_response(VIDEO_ID, [{**video, "vcodec": "unsupported.codec"}, audio]).options if item.format.value == "mp4"]
    assert not [item for item in build_formats_response(VIDEO_ID, [{**video, "height": None}, audio]).options if item.format.value == "mp4"]
    assert not [item for item in build_formats_response(VIDEO_ID, [video, {**audio, "acodec": "unsupported.codec"}]).options if item.format.value == "mp4"]
    assert not [item for item in build_formats_response(VIDEO_ID, [video, combined]).options if item.format.value == "mp4" and item.resolution == "480p"]
    result = build_formats_response(VIDEO_ID, [video, audio, combined])
    assert next(item for item in result.options if item.format.value == "mp4" and item.resolution == "360p").requires_audio_merge is False
    assert next(item for item in result.options if item.format.value == "mp4" and item.resolution == "480p").requires_audio_merge is True


def test_mp4_prefers_h264_over_av1_at_same_height():
    audio = {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}
    av1 = {"format_id": "399-sr", "ext": "mp4", "vcodec": "av01.0.08M.08", "acodec": "none", "height": 1080, "width": 1440, "vbr": 447}
    h264 = {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "width": 1920, "vbr": 4500}
    formats = build_formats_response(VIDEO_ID, [av1, h264, audio])
    chosen = select_streams(formats.formats, OutputFormat.mp4, 1080)
    assert chosen[0].format_id == "137"
    assert next(item for item in formats.options if item.format == OutputFormat.mp4 and item.resolution == "1080p").requires_audio_merge


def test_mp4_keeps_av1_when_no_alternative_exists():
    av1 = {"format_id": "399-sr", "ext": "mp4", "vcodec": "av01.0.08M.08", "acodec": "none", "height": 1080, "width": 1440, "vbr": 447}
    audio = {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}
    formats = build_formats_response(VIDEO_ID, [av1, audio])
    assert select_streams(formats.formats, OutputFormat.mp4, 1080)[0].format_id == "399-sr"


def test_duplicate_heights_and_audio_quality_use_selected_streams():
    raw = [
        {"format_id": "webm480", "ext": "webm", "vcodec": "vp09.00", "acodec": "none", "height": 480},
        {"format_id": "mp4480", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 480},
        {"format_id": "a128", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128},
        {"format_id": "a192", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 192},
    ]
    formats = build_formats_response(VIDEO_ID, raw)
    mp4 = [item for item in formats.options if item.format.value == "mp4"]
    assert len(mp4) == 1 and mp4[0].resolution == "480p"
    chosen = select_streams(formats.formats, mp4[0].format, 480)
    assert chosen[0].format_id == "mp4480" and chosen[1].format_id == "a192"
    mp3 = [item.audio_bitrate_kbps for item in formats.options if item.format.value == "mp3"]
    assert mp3 == [128, 192]
    assert all(item not in mp3 for item in (256, 320))
    assert select_streams(formats.formats, next(item.format for item in formats.options if item.format.value == "mp3"))[0].format_id == "a192"


def test_quality_api_rejects_unsupported_and_other_media_before_queueing():
    other_id = "other123456"
    def extract(media_id):
        info = sample_info()
        info["id"] = media_id
        if media_id == other_id:
            info["formats"] = [
                {"format_id": "v480", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 480},
                {"format_id": "a128", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128},
            ]
        return _snapshot_from_info(media_id, info)
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        manager.start = lambda: None
        async def stop_without_dispatch():
            return None
        manager.stop = stop_without_dispatch
        app = create_app()
        app.state.job_manager = manager
        app.state.metadata_service = MetadataService(extractor=extract)
        with TestClient(app, base_url="https://ytconvert.glitter.kr", client=("127.0.0.1", 50000)) as client:
            unsupported = client.post("/api/jobs", json={"media_id": VIDEO_ID, "format": "mp4", "resolution": "720p", "confirm_rights": True})
            assert unsupported.status_code == 422 and unsupported.json()["error"]["code"] == "unsupported_output"
            other = client.post("/api/jobs", json={"media_id": other_id, "format": "mp4", "resolution": "1080p", "confirm_rights": True})
            assert other.status_code == 422 and other.json()["error"]["code"] == "unsupported_output"
            valid = client.post("/api/jobs", json={"media_id": VIDEO_ID, "format": "mp4", "resolution": "1080p", "confirm_rights": True})
            assert valid.status_code == 201
            assert manager.records[str(valid.json()["job_id"])]["video_format_id"] == "137"
            assert manager.records[str(valid.json()["job_id"])]["audio_format_id"] == "140"
        assert len(manager.records) == 1
        try:
            select_source(extract(VIDEO_ID), CreateJobRequest(media_id=other_id, format="mp4", resolution="1080p", confirm_rights=True))
            raise AssertionError("Mismatched analysis was accepted")
        except JobError as error:
            assert error.status_code == 422 and error.code == "unsupported_source"


def test_job_validation_and_queue_limit():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base), media_max_queued_jobs=1)
        snapshot = sample_snapshot()
        record = manager.create(_request(), snapshot, "owner-token")
        assert record["state"] == "queued"
        assert record["video_format_id"] == "137" and record["audio_format_id"] == "140"
        try:
            manager.create(_request(), snapshot, "owner-token")
            raise AssertionError("Queue limit ignored")
        except JobError as error:
            assert error.code == "queue_full"
        try:
            select_source(snapshot, _request(resolution="240p"))
            raise AssertionError("Unsupported quality accepted")
        except JobError as error:
            assert error.code == "unsupported_output"
        try:
            select_source(snapshot, _request(format="flv"))
            raise AssertionError("Invalid format accepted")
        except ValueError:
            pass


def test_duplicate_active_job_is_rejected():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        manager.create(_request(), sample_snapshot(), "owner-token")
        try:
            manager.create(_request(), sample_snapshot(), "owner-token")
            raise AssertionError("Duplicate active job was accepted")
        except JobError as error:
            assert error.status_code == 409 and error.code == "duplicate_job"


def test_job_size_limit_and_subtitle_rejection():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base), media_max_download_bytes=100)
        try:
            manager.create(_request(), sample_snapshot(), "owner-token")
            raise AssertionError("Size limit ignored")
        except JobError as error:
            assert error.code == "size_limit"
        manager.settings = replace(manager.settings, media_max_download_bytes=1000000000)
        request = _request()
        request.include_subtitles = True
        try:
            manager.create(request, sample_snapshot(), "owner-token")
            raise AssertionError("Unsupported subtitle option accepted")
        except JobError as error:
            assert error.code == "unsupported_option"


def test_authorization_cancel_and_restart_recovery():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        record = manager.create(_request(), sample_snapshot(), "owner-token")
        job_id = UUID(record["job_id"])
        try:
            manager.get(job_id, "wrong-token")
            raise AssertionError("Wrong owner accepted")
        except JobError as error:
            assert error.status_code == 404
        assert manager.get(job_id, "owner-token")["state"] == "queued"
        try:
            manager._update(str(job_id), state="completed")
            raise AssertionError("Invalid state transition accepted")
        except JobError as error:
            assert error.code == "invalid_transition"
        asyncio.run(manager.cancel(job_id, "owner-token"))
        assert manager.get(job_id, "owner-token")["state"] == "cancelled"
        asyncio.run(manager.cancel(job_id, "owner-token"))
        assert manager.get(job_id, "owner-token")["state"] == "cancelled"
        second = manager.create(_request(), sample_snapshot(), "owner-token")
        recovered = _manager(Path(base))
        assert recovered.get(UUID(second["job_id"]), "owner-token")["state"] == "failed"
        assert recovered.get(UUID(second["job_id"]), "owner-token")["error"] == "Interrupted by service restart"
        recovered.records[second["job_id"]]["created_at"] = "2000-01-01T00:00:00+00:00"
        try:
            recovered.get(UUID(second["job_id"]), "owner-token")
            raise AssertionError("Expired session accepted")
        except JobError as error:
            assert error.status_code == 404


def test_job_records_survive_restart_and_list_requires_session():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        directory = Path(base)
        token = secrets.token_urlsafe(32)
        manager = _manager(directory)
        failed = manager.create(_request(), sample_snapshot(), token)
        failed_id = failed["job_id"]
        persisted = directory / "jobs" / f"{failed_id}.json"
        assert persisted.is_file()
        assert json.loads(persisted.read_text(encoding="utf-8"))["state"] == "queued"
        manager._update(failed_id, state="failed", error="Conversion failed or timed out", finished_at="2026-11-07T18:21:10+00:00")
        cancelled = manager.create(_request(), sample_snapshot(), token)
        manager._update(cancelled["job_id"], state="cancelled", finished_at="2026-11-07T18:22:10+00:00")
        completed = manager.create(_request(), sample_snapshot(), token)
        manager._update(completed["job_id"], state="downloading")
        manager._update(completed["job_id"], state="processing")
        manager._update(completed["job_id"], state="completed", finished_at="2026-11-07T18:23:10+00:00")

        recovered = _manager(directory)
        assert len(recovered.records) == 3
        assert recovered.get(UUID(failed_id), token)["error"] == "Conversion failed or timed out"
        assert {record["state"] for record in recovered.list(token)} == {"failed", "cancelled", "completed"}
        assert recovered.list(None) == []
        assert recovered.list(secrets.token_urlsafe(32)) == []

        app = create_app()
        app.state.job_manager = recovered
        with TestClient(app, base_url="https://ytconvert.glitter.kr") as client:
            assert client.get("/api/jobs").json() == {"jobs": []}
            client.cookies.set("ytconvert_session", token)
            response = client.get("/api/jobs")
            assert response.status_code == 200
            jobs = response.json()["jobs"]
            assert {job["state"] for job in jobs} == {"failed", "cancelled", "completed"}
            assert next(job for job in jobs if job["job_id"] == failed_id)["error"] == "Conversion failed or timed out"


def test_ffmpeg_command_is_bounded():
    settings = get_settings()
    source = settings.temp_dir / "source.mp4"
    destination = settings.output_dir / "result.mp4"
    command = ffmpeg_command(settings, source, destination, "mp4", video_height=720, source_height=1080, source_video_codec="h264", source_audio_codec="aac")
    assert command[0] == str(settings.ffmpeg_path)
    assert "-threads" in command and command[command.index("-threads") + 1] == "2"
    assert "libx264" in command and "scale=-2:720" in command
    assert "shell=True" not in command
    m4a = ffmpeg_command(settings, source, settings.output_dir / "result.m4a", "m4a", audio_bitrate_kbps=128, source_audio_codec="aac")
    assert m4a[m4a.index("-c:a") + 1] == "aac" and "128k" in m4a
    opus = ffmpeg_command(settings, source, settings.output_dir / "result.opus", "opus", source_audio_codec="aac")
    assert "libopus" in opus and "-vn" in opus


def test_ffprobe_metadata_and_frame_checks_are_separate():
    calls = []
    original = transcoder.subprocess.run
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "-count_frames" in command:
            return SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "nb_read_frames": "42"}]}))
        return SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac"}]}))
    transcoder.subprocess.run = fake_run
    try:
        metadata = probe_media(get_settings().ffprobe_path, get_settings().temp_dir / "source.mkv")
        frames = probe_video_frames(get_settings().ffprobe_path, get_settings().temp_dir / "source.mkv")
    finally:
        transcoder.subprocess.run = original
    assert metadata["video_codec"] == "h264" and metadata["height"] == 1080 and "-count_frames" not in calls[0][0]
    assert frames == 42 and "-count_frames" in calls[1][0]
    assert calls[0][1]["timeout"] == 5 and calls[1][1]["timeout"] == 30


def test_api_jobs_sse_and_download_without_worker():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        app = create_app()
        manager = _manager(Path(base))
        app.state.job_manager = manager
        app.state.metadata_service = MetadataService(extractor=lambda media_id: sample_snapshot())
        created_output = []

        async def fake_run(job_id):
            manager._update(job_id, state="downloading", started_at="2026-01-01T00:00:00+00:00")
            manager._update(job_id, state="processing", progress=50)
            directory = job_directory("output", UUID(job_id))
            directory.mkdir(exist_ok=False)
            created_output.append(directory)
            output = directory / "result.mp4"
            output.write_bytes(b"mock-output")
            manager._update(job_id, state="completed", progress=100, finished_at="2026-01-01T00:00:00+00:00", output_filename="result.mp4", output_bytes=11)

        manager._run = fake_run
        try:
            with TestClient(app, base_url="https://ytconvert.glitter.kr", client=("127.0.0.1", 50000)) as client:
                response = client.post("/api/jobs", json={"media_id": VIDEO_ID, "format": "mp4", "resolution": "1080p", "confirm_rights": True, "title": "Injected client title"})
                assert response.status_code == 201
                job_id = response.json()["job_id"]
                assert manager.records[job_id]["source_title"] == "Example Video"
                assert "source_title" not in response.json()
                sleep(0.03)
                assert client.get("/api/jobs").json()["jobs"][0]["job_id"] == job_id
                assert client.get(f"/api/jobs/{job_id}").json()["state"] == "completed"
                events = client.get(f"/api/jobs/{job_id}/events")
                assert events.status_code == 200 and "event: job" in events.text
                assert '"state":"completed"' in client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "999"}).text
                download = client.get(f"/api/download/{job_id}")
                assert download.content == b"mock-output"
                assert download.headers["Cache-Control"] == "private, no-store"
                assert download.headers["Content-Type"].startswith("video/mp4")
                assert download.headers["Content-Length"] == "11"
                assert "Example%20Video.mp4" in download.headers["Content-Disposition"]
                manager.settings = replace(manager.settings, media_max_output_bytes=5)
                assert client.get(f"/api/download/{job_id}").status_code == 404
                assert client.post(f"/api/jobs/{job_id}/cancel").json()["state"] == "completed"
            with TestClient(app, base_url="https://ytconvert.glitter.kr") as stranger:
                assert stranger.get(f"/api/jobs/{job_id}").status_code == 404
                assert stranger.get(f"/api/download/{job_id}").status_code == 404
        finally:
            for directory in created_output:
                shutil.rmtree(directory)


def test_worker_disabled_api_blocks_creation_without_network():
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/jobs", json={"media_id": VIDEO_ID, "format": "mp4", "resolution": "1080p"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "worker_unavailable"


def test_worker_checks_required_tools():
    settings = replace(get_settings(), media_worker_enabled=True, ffprobe_path=Path("/nonexistent/ffprobe"))
    assert worker_unavailable_reason(settings) == "ffprobe is unavailable"


def test_root_preflight_inherits_identity_without_user_arguments():
    import app.services.jobs as jobs_module
    from app.core.config import PROJECT_ROOT
    from types import SimpleNamespace

    executable = PROJECT_ROOT / "venv/bin/python"
    settings = replace(get_settings(), media_worker_enabled=True, ffmpeg_path=executable, ffprobe_path=executable)
    calls = []
    original_run = jobs_module.subprocess.run
    original_geteuid = jobs_module.os.geteuid

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout=" V....D libx264\n A....D libmp3lame\n A....D aac\n A....D libopus\n")

    jobs_module.subprocess.run = fake_run
    jobs_module.os.geteuid = lambda: 0
    try:
        assert worker_unavailable_reason(settings) is None
    finally:
        jobs_module.subprocess.run = original_run
        jobs_module.os.geteuid = original_geteuid
    assert len(calls) == 2
    assert all(not any(name in call for name in ("user", "group", "extra_groups")) for call in calls)


def test_encoder_detection_and_output_requirements():
    import app.services.encoders as encoder_module
    from types import SimpleNamespace

    original = encoder_module.subprocess.run
    encoder_module.subprocess.run = lambda *args, **kwargs: SimpleNamespace(stdout="Encoders:\n V....D libx264 H.264\n A....D aac AAC\n A....D libmp3lame MP3\n A....D libopus Opus\n V....D other_codec Other\n")
    try:
        encoders = detect_encoders(get_settings())
    finally:
        encoder_module.subprocess.run = original
    assert all(unsupported_reason(name, encoders) is None for name in ("mp4", "mp3", "m4a", "opus"))
    assert unsupported_reason("webm", encoders) == "Required FFmpeg encoder unavailable: libvpx-vp9"
    assert unsupported_reason("mp4", frozenset({"libx264"})) == "Required FFmpeg encoder unavailable: aac"


def test_webm_only_is_disabled_in_api_and_jobs():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        manager.encoders = frozenset({"libx264", "aac", "libmp3lame", "libopus"})
        with TestClient(create_app(), base_url="https://ytconvert.glitter.kr", client=("127.0.0.1", 50000)) as client:
            client.app.state.job_manager = manager
            client.app.state.metadata_service = MetadataService(extractor=lambda media_id: sample_snapshot())
            formats = client.get(f"/api/formats/{VIDEO_ID}")
            assert formats.status_code == 200
            options = formats.json()["options"]
            assert all(not item["available"] and "libvpx-vp9" in item["unavailable_reason"] for item in options if item["format"] == "webm")
            assert all(item["available"] for item in options if item["format"] in {"mp4", "mp3", "m4a", "opus"})
            denied = client.post("/api/jobs", json={"media_id": VIDEO_ID, "format": "webm", "resolution": "1080p", "confirm_rights": True})
            assert denied.status_code == 422 and denied.json()["error"]["code"] == "unsupported_encoder"
            status = client.get("/api/status").json()["capabilities"]
            assert status["conversion_worker"] == "available"
            assert status["conversion_webm"] == "unavailable" and status["conversion_mp4"] == "available"
        assert manager.records == {}


def test_formats_remain_selectable_when_worker_is_disabled():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        manager.ready_reason = "Media worker is disabled"
        manager.encoders = frozenset({"libx264", "aac", "libmp3lame", "libopus"})
        app = create_app()
        app.state.job_manager = manager
        app.state.metadata_service = MetadataService(extractor=lambda media_id: sample_snapshot())
        with TestClient(app, base_url="https://ytconvert.glitter.kr", client=("127.0.0.1", 50000)) as client:
            response = client.get(f"/api/formats/{VIDEO_ID}")
            assert response.status_code == 200
            options = response.json()["options"]
            assert all(item["available"] and item["unavailable_reason"] is None for item in options if item["format"] in {"mp4", "mp3", "m4a", "opus"})
            assert all(not item["available"] and "libvpx-vp9" in item["unavailable_reason"] for item in options if item["format"] == "webm")
            assert client.get("/api/status").json()["capabilities"]["conversion_worker"] == "unavailable"
            denied = client.post("/api/jobs", json={"media_id": VIDEO_ID, "format": "mp4", "resolution": "1080p", "confirm_rights": True})
            assert denied.status_code == 503 and denied.json()["error"]["code"] == "worker_unavailable"


def test_encoder_discovery_runs_while_worker_is_disabled():
    import app.services.jobs as jobs_module
    from app.core.config import PROJECT_ROOT

    settings = replace(get_settings(), media_worker_enabled=False, ffmpeg_path=PROJECT_ROOT / "venv/bin/python")
    original = jobs_module.detect_encoders
    calls = []

    def fake_detect(current):
        calls.append(current.ffmpeg_path)
        return frozenset({"libx264", "aac", "libmp3lame", "libopus"})

    jobs_module.detect_encoders = fake_detect
    try:
        reason, encoders, encoder_reason = jobs_module.worker_capabilities(settings)
    finally:
        jobs_module.detect_encoders = original
    assert reason == "Media worker is disabled" and encoder_reason is None
    assert "libx264" in encoders and "libvpx-vp9" not in encoders
    assert calls == [settings.ffmpeg_path]


def test_supported_outputs_can_be_queued_without_vp9():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        manager.encoders = frozenset({"libx264", "aac", "libmp3lame", "libopus"})
        snapshot = sample_snapshot()
        for output_format, resolution, bitrate in (("mp4", "1080p", None), ("mp3", None, 128), ("m4a", None, 128), ("opus", None, 128)):
            record = manager.create(_request(format=output_format, resolution=resolution, audio_bitrate_kbps=bitrate), snapshot, "owner-token")
            assert record["format"] == output_format and record["state"] == "queued"


def test_internal_storage_symlinks_are_rejected():
    from uuid import uuid4

    job_id = uuid4()
    target = job_directory("output", uuid4())
    link = get_settings().temp_dir / str(job_id)
    link.symlink_to(target)
    try:
        try:
            job_directory("temp", job_id)
            raise AssertionError("Linked job directory was accepted")
        except ValueError:
            pass
    finally:
        link.unlink()
    directory = job_directory("output", job_id)
    directory.mkdir()
    (directory / "other.mp4").write_bytes(b"safe")
    (directory / "result.mp4").symlink_to(directory / "other.mp4")
    try:
        try:
            job_file("output", job_id, "result.mp4")
            raise AssertionError("Linked job file was accepted")
        except ValueError:
            pass
    finally:
        shutil.rmtree(directory)


def test_mock_worker_timeout_and_cleanup():
    import app.services.jobs as jobs_module

    class FakeInput:
        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

    class FakeOutput:
        async def readline(self):
            await asyncio.sleep(1)
            return b""

    class FakeErrors:
        async def read(self, size):
            return b""

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeInput()
            self.stdout = FakeOutput()
            self.stderr = FakeErrors()
            self.returncode = None
            self.pid = 12345

    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base), media_job_timeout_seconds=0.01)
        record = manager.create(_request(), sample_snapshot(), "owner-token")
        job_id = UUID(record["job_id"])
        temp_directory = job_directory("temp", job_id)
        temp_directory.mkdir()
        (temp_directory / "partial.part").write_bytes(b"partial")
        output_directory = job_directory("output", job_id)
        output_directory.mkdir()
        (output_directory / "partial.mp4").write_bytes(b"partial")
        original = jobs_module.asyncio.create_subprocess_exec
        spawn_calls = []

        def fake_spawn(*args, **kwargs):
            spawn_calls.append(kwargs)
            return asyncio.sleep(0, result=FakeProcess())

        jobs_module.asyncio.create_subprocess_exec = fake_spawn

        async def terminate(process):
            process.returncode = -15

        manager._terminate = terminate
        try:
            asyncio.run(manager._run(str(job_id)))
            assert record["state"] == "failed"
            assert record["error"] == "Conversion timed out"
            assert not temp_directory.exists() and not output_directory.exists()
            assert len(spawn_calls) == 1
            assert not any(name in spawn_calls[0] for name in ("user", "group", "extra_groups"))
        finally:
            jobs_module.asyncio.create_subprocess_exec = original
            if temp_directory.exists():
                shutil.rmtree(temp_directory)
            if output_directory.exists():
                shutil.rmtree(output_directory)


def test_worker_failure_is_logged_and_persisted_for_sse():
    import app.services.jobs as jobs_module

    class FakeInput:
        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

    class FakeOutput:
        def __init__(self, lines):
            self.lines = list(lines)

        async def readline(self):
            return self.lines.pop(0) if self.lines else b""

    class FakeErrors:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, size):
            return self.chunks.pop(0) if self.chunks else b""

    class FakeProcess:
        def __init__(self, include_event=True, python_error=False):
            self.stdin = FakeInput()
            lines = [
                b'{"state":"processing_progress","progress":99}\n',
                b'{"state":"failed","code":"ffmpeg_failed","stage":"transcode","tool":"ffmpeg","exit_code":9,"timed_out":false,"exception_type":null,"diagnostic":"Invalid data at https://example.test/media?token=private-value","ffmpeg_cpu_seconds":118.5,"cpu_limit_seconds":120}\n',
            ] if include_event else []
            if python_error:
                lines = [b'{"state":"failed","code":"worker_failed","stage":"transcode","tool":"worker","exit_code":null,"timed_out":false,"exception_type":"ValueError","diagnostic":"Invalid FFmpeg progress value"}\n']
            self.stdout = FakeOutput(lines)
            self.stderr = FakeErrors([b"Worker failed at https://example.test/private?token=private-value", b""])
            self.returncode = None

        async def wait(self):
            self.returncode = 1
            return 1

    class CapturedHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        termination_calls = []
        async def unexpected_termination(process):
            termination_calls.append(process)
        manager._terminate = unexpected_termination
        token = secrets.token_urlsafe(32)
        record = manager.create(_request(), sample_snapshot(), token)
        job_id = record["job_id"]
        handler = CapturedHandler()
        logger = logging.getLogger("ytconvert")
        logger.addHandler(handler)
        original_spawn = jobs_module.asyncio.create_subprocess_exec
        jobs_module.asyncio.create_subprocess_exec = lambda *args, **kwargs: asyncio.sleep(0, result=FakeProcess())
        try:
            asyncio.run(manager._run(job_id))
        finally:
            jobs_module.asyncio.create_subprocess_exec = original_spawn
            logger.removeHandler(handler)
        assert record["state"] == "failed" and record["error"] == "FFmpeg conversion failed"
        assert record["progress"] == 99.5 and record["download_progress"] == 100 and record["conversion_progress"] == 99
        assert json.loads((Path(base) / "jobs" / f"{job_id}.json").read_text())["error"] == "FFmpeg conversion failed"
        failure = next(item for item in handler.records if item.getMessage() == "Media job failed")
        assert failure.job_id == job_id and failure.stage == "transcode" and failure.tool == "ffmpeg"
        assert failure.exit_code == 9 and failure.timed_out is False
        assert failure.ffmpeg_cpu_seconds == 118.5 and failure.cpu_limit_seconds == 120
        assert failure.termination_reason == "unknown" and termination_calls == []
        assert "[url]" in failure.diagnostic and "private-value" not in failure.diagnostic
        from app.core.logging import JsonFormatter
        structured = json.loads(JsonFormatter().format(failure))
        assert structured["job_id"] == job_id and structured["exit_code"] == 9
        assert structured["ffmpeg_cpu_seconds"] == 118.5 and structured["cpu_limit_seconds"] == 120
        assert structured["diagnostic"] == failure.diagnostic
        app = create_app()
        app.state.job_manager = manager
        with TestClient(app, base_url="https://ytconvert.glitter.kr") as client:
            client.cookies.set("ytconvert_session", token)
            response = client.get(f"/api/jobs/{job_id}/events")
            assert response.status_code == 200 and '"state":"failed"' in response.text
            assert '"error":"FFmpeg conversion failed"' in response.text

        unknown = manager.create(_request(), sample_snapshot(), token)
        logger.addHandler(handler)
        jobs_module.asyncio.create_subprocess_exec = lambda *args, **kwargs: asyncio.sleep(0, result=FakeProcess(include_event=False))
        try:
            asyncio.run(manager._run(unknown["job_id"]))
        finally:
            jobs_module.asyncio.create_subprocess_exec = original_spawn
            logger.removeHandler(handler)
        assert unknown["state"] == "failed" and unknown["error"] == "Conversion failed"
        fallback = handler.records[-1]
        assert fallback.job_id == unknown["job_id"] and "[url]" in fallback.diagnostic
        assert "private-value" not in fallback.diagnostic

        internal = manager.create(_request(), sample_snapshot(), token)
        logger.addHandler(handler)
        jobs_module.asyncio.create_subprocess_exec = lambda *args, **kwargs: asyncio.sleep(0, result=FakeProcess(python_error=True))
        try:
            asyncio.run(manager._run(internal["job_id"]))
        finally:
            jobs_module.asyncio.create_subprocess_exec = original_spawn
            logger.removeHandler(handler)
        assert internal["state"] == "failed" and internal["error"] == "Conversion failed"
        parsed_failure = handler.records[-1]
        assert parsed_failure.tool == "worker" and parsed_failure.exception_type == "ValueError"
        assert parsed_failure.exit_code is None


def test_mock_active_cancellation_and_failure():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        record = manager.create(_request(), sample_snapshot(), "owner-token")
        job_id = UUID(record["job_id"])
        process = type("Process", (), {"returncode": None})()
        manager.active[str(job_id)] = process
        stopped = []

        async def terminate(target):
            stopped.append(target)
            target.returncode = -15

        manager._terminate = terminate
        asyncio.run(manager.cancel(job_id, "owner-token"))
        assert stopped == [process]
        assert record["state"] == "cancelled"
        second = manager.create(_request(), sample_snapshot(), "owner-token")
        second_id = UUID(second["job_id"])
        manager.active[str(second_id)] = process

        async def failed_terminate(target):
            raise OSError("stop failed")

        manager._terminate = failed_terminate
        try:
            asyncio.run(manager.cancel(second_id, "owner-token"))
            raise AssertionError("Failed termination was reported as success")
        except JobError as error:
            assert error.code == "cancellation_failed"
        assert second["state"] == "queued"


def test_cancellation_during_worker_spawn():
    import app.services.jobs as jobs_module

    class FakeProcess:
        returncode = None

    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        record = manager.create(_request(), sample_snapshot(), "owner-token")
        job_id = UUID(record["job_id"])
        release = asyncio.Event()
        original = jobs_module.asyncio.create_subprocess_exec

        async def fake_spawn(*args, **kwargs):
            await release.wait()
            return FakeProcess()

        async def fake_terminate(process):
            process.returncode = -15

        manager._terminate = fake_terminate
        jobs_module.asyncio.create_subprocess_exec = fake_spawn

        async def scenario():
            running = asyncio.create_task(manager._run(str(job_id)))
            while str(job_id) not in manager.spawn_events:
                await asyncio.sleep(0)
            cancelling = asyncio.create_task(manager.cancel(job_id, "owner-token"))
            await asyncio.sleep(0)
            release.set()
            cancelled = await cancelling
            await running
            assert cancelled["state"] == "cancelled"
            assert not manager.active and not manager.spawn_events

        try:
            asyncio.run(scenario())
        finally:
            jobs_module.asyncio.create_subprocess_exec = original


def test_process_group_termination_is_requested():
    import app.services.jobs as jobs_module

    class FakeProcess:
        pid = 45678
        returncode = None

        async def wait(self):
            self.returncode = -15
            return self.returncode

    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        calls = []
        original = jobs_module.os.killpg
        def fake_killpg(pid, sig):
            calls.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError()

        jobs_module.os.killpg = fake_killpg
        try:
            asyncio.run(manager._terminate(FakeProcess()))
        finally:
            jobs_module.os.killpg = original
        assert calls[0] == (45678, signal.SIGTERM)
        assert calls[1] == (45678, 0)


def test_lingering_process_group_receives_sigkill():
    import app.services.jobs as jobs_module

    class FakeProcess:
        pid = 45679
        returncode = -15

    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        calls = []
        original = jobs_module.os.killpg
        jobs_module.os.killpg = lambda pid, sig: calls.append((pid, sig))
        try:
            asyncio.run(manager._terminate(FakeProcess()))
        finally:
            jobs_module.os.killpg = original
        assert calls == [(45679, signal.SIGTERM), (45679, 0), (45679, signal.SIGKILL)]
