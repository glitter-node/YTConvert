import contextlib
import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.core.security import canonical_youtube_url
from app.core.diagnostics import safe_diagnostic
from app.services.storage import job_directory
from app.workers import converter
from tests.test_metadata import VIDEO_ID


def test_mock_download_probe_transcode_pipeline():
    job_id = uuid4()
    temp = job_directory("temp", job_id)
    output = job_directory("output", job_id)
    task = {
        "job_id": str(job_id), "media_id": VIDEO_ID, "format": "mp4", "resolution": "360p",
        "audio_bitrate_kbps": None, "include_metadata": True, "video_format_id": "18",
        "audio_format_id": None, "ffmpeg_path": "/opt/bin/ffmpeg", "ffprobe_path": "/opt/bin/ffprobe",
        "max_download_bytes": 1000, "max_output_bytes": 1000, "timeout_seconds": 30,
        "duration_seconds": 2,
    }
    original_limits = converter._limits
    original_module = converter._yt_dlp_module
    original_probe = converter.probe_media
    original_popen = converter.subprocess.Popen
    commands = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def process_info(self, info):
            return None

        def download(self, urls):
            assert urls == [canonical_youtube_url(VIDEO_ID)]
            assert self.options["format"] == "18"
            self.process_info({"id": VIDEO_ID, "format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 360, "abr": 96})
            (temp / "source.mp4").write_bytes(b"mock-source")
            self.options["progress_hooks"][0]({"status": "downloading", "downloaded_bytes": 11, "total_bytes": 11})
            return 0

    class FakeProcess:
        pid = 3210

        def __init__(self, command, **kwargs):
            commands.append(command)
            Path(command[-1]).write_bytes(b"mock-output")
            self.stdout = ["out_time_ms=1000000\n"]

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    converter._limits = lambda _: None
    converter._yt_dlp_module = lambda: SimpleNamespace(YoutubeDL=FakeYoutubeDL)
    converter.probe_media = lambda *args: {"video_codec": "h264", "audio_codec": "aac", "height": 360, "width": 640, "duration": "2", "format_name": "mov,mp4,m4a"}
    converter.subprocess.Popen = FakeProcess
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            converter.run_conversion(task)
        events = [json.loads(line) for line in captured.getvalue().splitlines()]
        assert [event["state"] for event in events] == ["downloading", "processing", "processing_progress", "completed"]
        assert (output / "result.mp4").read_bytes() == b"mock-output"
        assert commands[0][0] == "/opt/bin/ffmpeg"
        assert "-protocol_whitelist" in commands[0]
    finally:
        converter._limits = original_limits
        converter._yt_dlp_module = original_module
        converter.probe_media = original_probe
        converter.subprocess.Popen = original_popen
        if temp.exists():
            shutil.rmtree(temp)
        if output.exists():
            shutil.rmtree(output)


def _diagnostic_pipeline(output_format: str, failure: str | None = None, progress_lines: list[str] | None = None, video_height: int = 360, video_codec: str = "avc1", probe_video_codec: str = "h264"):
    job_id = uuid4()
    temp = job_directory("temp", job_id)
    output = job_directory("output", job_id)
    task = {
        "job_id": str(job_id), "media_id": VIDEO_ID, "format": output_format,
        "resolution": f"{video_height}p" if output_format == "mp4" else None,
        "audio_bitrate_kbps": 128 if output_format == "mp3" else None,
        "include_metadata": True, "video_format_id": "399-sr" if output_format == "mp4" and video_height == 1080 else "18" if output_format == "mp4" else "140",
        "audio_format_id": "140" if output_format == "mp4" and video_height == 1080 else None, "ffmpeg_path": "/opt/bin/ffmpeg", "ffprobe_path": "/opt/bin/ffprobe",
        "max_download_bytes": 1000, "max_output_bytes": 1000, "timeout_seconds": 30,
        "duration_seconds": 2,
    }
    original_limits = converter._limits
    original_module = converter._yt_dlp_module
    original_probe = converter.probe_media
    original_popen = converter.subprocess.Popen
    commands = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def process_info(self, info):
            return None

        def download(self, urls):
            assert urls == [canonical_youtube_url(VIDEO_ID)]
            if failure == "download":
                raise RuntimeError("Download refused at https://example.test/media?token=private-value")
            if failure == "download_code":
                self.options["logger"].error("HTTP 403 for https://example.test/media?token=private-value")
                return 1
            if failure == "merge":
                self.options["postprocessor_hooks"][0]({"status": "started", "postprocessor": "FFmpegMerger"})
                raise RuntimeError("Audio merge failed at https://example.test/stream?token=private-value")
            info = {"id": VIDEO_ID, "format_id": task["video_format_id"], "ext": "mp4" if output_format == "mp4" else "m4a", "vcodec": video_codec if output_format == "mp4" else "none", "acodec": "mp4a.40.2", "height": video_height + 120 if failure == "source_changed" else video_height if output_format == "mp4" else None, "abr": 128}
            if task["audio_format_id"]:
                info = {"id": VIDEO_ID, "requested_formats": [{**info, "acodec": "none"}, {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}]}
            self.process_info(info)
            (temp / ("source.mkv" if task["audio_format_id"] else "source.mp4" if output_format == "mp4" else "source.m4a")).write_bytes(b"mock-source")
            return 0

    class FakeProcess:
        pid = 3210

        def __init__(self, command, **kwargs):
            commands.append(command)
            if failure == "python_error":
                def broken_output():
                    yield "out_time_ms=1000000\n"
                    raise ValueError("Internal progress iterator failed")
                self.stdout = broken_output()
            else:
                self.stdout = progress_lines if progress_lines is not None else ["out_time_ms=1000000\n"]
            self.returncode = None
            if failure == "ffmpeg":
                kwargs["stderr"].write("Error opening https://example.test/stream?token=private-value: Invalid data\n")
                kwargs["stderr"].flush()
            elif failure != "output_missing":
                Path(command[-1]).write_bytes(b"mock-output")

        def wait(self, timeout=None):
            if failure == "timeout" and timeout is not None:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            self.returncode = 9 if failure == "ffmpeg" else 0
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    converter._limits = lambda _: None
    converter._yt_dlp_module = lambda: SimpleNamespace(YoutubeDL=FakeYoutubeDL)
    def fake_probe(*args):
        if failure == "probe":
            raise subprocess.CalledProcessError(2, ["ffprobe"], stderr="Invalid source at https://example.test/media?token=private-value")
        return {"video_codec": probe_video_codec if output_format == "mp4" else None, "audio_codec": "aac", "height": video_height if output_format == "mp4" else None, "width": 1440 if output_format == "mp4" else None, "duration": "2" if output_format == "mp4" else None, "format_name": "mov,mp4,m4a" if output_format == "mp4" else "mp3", "video_frames": 0 if failure == "output_frames" else 60 if output_format == "mp4" else None}

    converter.probe_media = fake_probe
    converter.subprocess.Popen = FakeProcess
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            try:
                converter.run_conversion(task)
                error = None
            except converter.WorkerFailure as raised:
                error = raised
        result_file = output / f"result.{output_format}"
        return error, [json.loads(line) for line in captured.getvalue().splitlines()], commands, result_file.read_bytes() if result_file.exists() else None
    finally:
        converter._limits = original_limits
        converter._yt_dlp_module = original_module
        converter.probe_media = original_probe
        converter.subprocess.Popen = original_popen
        if temp.exists():
            shutil.rmtree(temp)
        if output.exists():
            shutil.rmtree(output)


def test_mock_mp4_and_mp3_success_paths():
    for output_format in ("mp4", "mp3"):
        error, events, commands, content = _diagnostic_pipeline(output_format)
        assert error is None
        assert events[-1]["state"] == "completed"
        assert content == b"mock-output"
        assert commands[0][commands[0].index("-c:a") + 1] == ("copy" if output_format == "mp4" else "libmp3lame")


def test_mock_1080_av1_aac_conversion_and_failure():
    error, events, commands, content = _diagnostic_pipeline("mp4", video_height=1080, video_codec="av01.0.08M.08", probe_video_codec="av1")
    assert error is None and content == b"mock-output" and events[-1]["state"] == "completed"
    assert commands[0][commands[0].index("-c:v") + 1] == "libx264"
    assert commands[0][commands[0].index("-c:a") + 1] == "copy"
    assert "-vf" not in commands[0]
    error, events, commands, _ = _diagnostic_pipeline("mp4", "ffmpeg", video_height=1080, video_codec="av01.0.08M.08", probe_video_codec="av1")
    assert error.code == "ffmpeg_failed" and error.exit_code == 9
    assert "Invalid data" in error.diagnostic and events[-1]["state"] == "processing_progress"
    error, events, commands, content = _diagnostic_pipeline("mp4", video_height=480)
    assert error is None and content == b"mock-output" and events[-1]["state"] == "completed"


def test_output_validation_does_not_count_all_video_frames():
    error, events, commands, content = _diagnostic_pipeline("mp4", "output_frames", video_height=1080, video_codec="av01.0.08M.08", probe_video_codec="h264")
    assert error is None and events[-1]["state"] == "completed"
    assert commands and content == b"mock-output"


def test_changed_source_is_rejected_before_media_download():
    error, events, commands, content = _diagnostic_pipeline("mp4", "source_changed")
    assert error.code == "download_failed" and error.stage == "download"
    assert "Selected source streams changed" in error.diagnostic
    assert events == [] and commands == [] and content is None


def test_selected_streams_match_download_time_source():
    video = {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none", "height": 1080}
    audio = {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}
    task = {"media_id": VIDEO_ID, "format": "mp4", "resolution": "1080p", "audio_bitrate_kbps": None, "video_format_id": "137", "audio_format_id": "140"}
    info = {"id": VIDEO_ID, "requested_formats": [video, audio]}
    assert converter._selected_source_matches(task, info)
    assert not converter._selected_source_matches(task, {**info, "requested_formats": [{**video, "height": 720}, audio]})
    assert not converter._selected_source_matches(task, {**info, "requested_formats": [{**video, "vcodec": "unknown.codec"}, audio]})
    assert not converter._selected_source_matches(task, {**info, "requested_formats": [video, {**audio, "format_id": "other"}]})
    assert not converter._selected_source_matches(task, {"id": VIDEO_ID, "requested_formats": [video]})
    assert not converter._selected_source_matches(task, {**info, "id": "other123456"})
    mp3 = {"media_id": VIDEO_ID, "format": "mp3", "resolution": None, "audio_bitrate_kbps": 128, "video_format_id": "140", "audio_format_id": None}
    assert converter._selected_source_matches(mp3, {"id": VIDEO_ID, **audio})
    assert not converter._selected_source_matches(mp3, {"id": VIDEO_ID, **{**audio, "abr": 96}})


def test_1080_av1_aac_selected_streams_and_changed_source():
    video = {"format_id": "399-sr", "ext": "mp4", "vcodec": "av01.0.08M.08", "acodec": "none", "width": 1440, "height": 1080}
    audio = {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129.512}
    task = {"media_id": VIDEO_ID, "format": "mp4", "resolution": "1080p", "audio_bitrate_kbps": None, "video_format_id": "399-sr", "audio_format_id": "140"}
    info = {"id": VIDEO_ID, "requested_formats": [video, audio]}
    assert converter._selected_source_matches(task, info)
    assert not converter._selected_source_matches(task, {**info, "requested_formats": [{**video, "height": 720}, audio]})
    assert not converter._selected_source_matches(task, {**info, "requested_formats": [video, {**audio, "format_id": "other"}]})
    assert not converter._selected_source_matches(task, {**info, "requested_formats": [video]})


def test_mock_download_and_ffmpeg_failure_diagnostics():
    error, events, commands, _ = _diagnostic_pipeline("mp3", "download")
    assert error.code == "download_failed" and error.stage == "download" and error.tool == "yt-dlp"
    assert str(error) == "Download failed"
    assert "[url]" in error.diagnostic and "private-value" not in error.diagnostic
    assert commands == [] and not any(event["state"] == "completed" for event in events)

    error, events, commands, _ = _diagnostic_pipeline("mp3", "download_code")
    assert error.code == "download_failed" and error.exit_code == 1
    assert "HTTP 403" in error.diagnostic and "private-value" not in error.diagnostic
    assert commands == [] and events == []

    error, events, commands, _ = _diagnostic_pipeline("mp4", "merge")
    assert error.code == "merge_failed" and error.stage == "merge" and commands == []
    assert str(error) == "Audio merge failed"
    assert "private-value" not in error.diagnostic

    error, events, commands, _ = _diagnostic_pipeline("mp4", "ffmpeg")
    assert error.code == "ffmpeg_failed" and error.exit_code == 9 and error.stage == "transcode"
    assert str(error) == "FFmpeg conversion failed"
    assert "Invalid data" in error.diagnostic and "private-value" not in error.diagnostic
    assert error.ffmpeg_pid == 3210
    assert commands and events[-1]["state"] == "processing_progress"

    error, events, commands, _ = _diagnostic_pipeline("mp3", "ffmpeg")
    assert error.code == "audio_extraction_failed" and error.exit_code == 9
    assert str(error) == "Audio extraction failed"
    assert commands and events[-1]["state"] == "processing_progress"


def test_mock_ffmpeg_timeout_diagnostic():
    error, events, commands, _ = _diagnostic_pipeline("mp4", "timeout")
    assert error.code == "conversion_timeout" and error.timed_out
    assert str(error) == "Conversion timed out"
    assert error.exception_type == "TimeoutExpired" and commands
    assert events[-1]["state"] == "processing_progress"


def test_cpu_limit_allows_parallel_ffmpeg_and_preserves_other_limits():
    original_euid = converter.os.geteuid
    original_umask = converter.os.umask
    original_setrlimit = converter.resource.setrlimit
    calls = []
    converter.os.geteuid = lambda: 0
    converter.os.umask = lambda mask: None
    converter.resource.setrlimit = lambda kind, limits: calls.append((kind, limits))
    try:
        converter._limits({"timeout_seconds": 1800, "max_download_bytes": 1000, "max_output_bytes": 2000})
    finally:
        converter.os.geteuid = original_euid
        converter.os.umask = original_umask
        converter.resource.setrlimit = original_setrlimit
    assert dict(calls) == {
        converter.resource.RLIMIT_CORE: (0, 0),
        converter.resource.RLIMIT_CPU: (7200, 7205),
        converter.resource.RLIMIT_FSIZE: (2000, 2000),
        converter.resource.RLIMIT_NOFILE: (64, 64),
        converter.resource.RLIMIT_AS: (1610612736, 1610612736),
    }


def test_ffmpeg_failure_reports_cpu_budget_and_usage():
    error, _, _, _ = _diagnostic_pipeline("mp4", "ffmpeg")
    assert error.event()["cpu_limit_seconds"] == 120
    assert error.event()["ffmpeg_cpu_seconds"] >= 0


def test_progress_values_skip_na_empty_and_malformed_lines():
    assert converter._progress_seconds("out_time_ms=N/A\n") is None
    assert converter._progress_seconds("out_time_us=\n") is None
    assert converter._progress_seconds("out_time_ms=not-a-number\n") is None
    assert converter._progress_seconds("out_time_ms=NaN\n") is None
    assert converter._progress_seconds("out_time_us=inf\n") is None
    assert converter._progress_seconds("out_time_ms=-1\n") is None
    assert converter._progress_seconds("out_time_ms=1000000\n") == 1
    assert converter._progress_seconds("out_time_us= 2000000 \n") == 2
    lines = ["out_time_ms=N/A\n", "out_time_ms=\n", "out_time_us=   \n", "out_time_ms=invalid\n", "out_time_us=1000000\n", "out_time_ms=2000000\n"]
    error, events, commands, content = _diagnostic_pipeline("mp3", progress_lines=lines)
    assert error is None and content == b"mock-output" and commands
    assert [event["progress"] for event in events if event["state"] == "processing_progress"] == [50, 99]
    assert events[-1]["state"] == "completed"


def test_python_progress_exception_has_no_ffmpeg_exit_code():
    error, events, commands, _ = _diagnostic_pipeline("mp3", "python_error")
    assert error.code == "worker_failed" and error.tool == "worker"
    assert error.exception_type == "ValueError" and error.exit_code is None
    assert commands and events[-1]["state"] == "processing_progress"


def test_mock_probe_failure_diagnostic():
    error, events, commands, _ = _diagnostic_pipeline("mp3", "probe")
    assert error.code == "probe_failed" and error.stage == "probe" and error.exit_code == 2
    assert str(error) == "Source inspection failed"
    assert error.tool == "ffprobe" and "private-value" not in error.diagnostic
    assert commands == [] and events == []


def test_mock_output_validation_failure():
    error, events, commands, content = _diagnostic_pipeline("mp4", "output_missing")
    assert error.code == "output_validation_failed" and error.stage == "finalize"
    assert str(error) == "Output validation failed"
    assert commands and content is None and events[-1]["state"] == "processing_progress"


def test_worker_main_emits_bounded_failure_event():
    original_stdin = converter.sys.stdin
    original_run = converter.run_conversion
    converter.sys.stdin = SimpleNamespace(buffer=io.BytesIO(b"{}"))
    converter.run_conversion = lambda task: (_ for _ in ()).throw(converter.WorkerFailure("download_failed", "download", "yt-dlp", cause=RuntimeError("Rejected https://example.test/video?token=private-value")))
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            try:
                converter.main()
                raise AssertionError("Worker failure exited successfully")
            except SystemExit as error:
                assert error.code == 1
    finally:
        converter.sys.stdin = original_stdin
        converter.run_conversion = original_run
    event = json.loads(captured.getvalue())
    assert event["state"] == "failed" and event["stage"] == "download"
    assert event["exception_type"] == "RuntimeError"
    assert "[url]" in event["diagnostic"] and "private-value" not in event["diagnostic"]


def test_diagnostic_redaction_and_length_limit():
    diagnostic = safe_diagnostic("Authorization: Bearer private-token\nCookie: session=private-value\n/volume1/glitter/ytconvert/data/temp/file.mp4")
    assert "private-token" not in diagnostic and "private-value" not in diagnostic
    assert "/volume1/" not in diagnostic
    assert len(safe_diagnostic("x" * 5000)) <= 1200
