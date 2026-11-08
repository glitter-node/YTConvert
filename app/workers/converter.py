import json
import math
import os
import re
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from app.core.config import project_path
from app.core.diagnostics import failure_message, safe_diagnostic
from app.core.security import canonical_youtube_url, video_id_is_valid
from app.schemas.api import OutputFormat
from app.services.formats import build_formats_response, select_streams
from app.services.metadata import _safe_network_target, _yt_dlp_module
from app.services.storage import job_directory, job_file
from app.services.transcoder import ffmpeg_command, probe_media


FORMAT_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def _cpu_limit_seconds(timeout: int) -> int:
    return timeout * 4


def _progress_seconds(line: str) -> float | None:
    key, separator, raw = line.partition("=")
    if not separator or key not in {"out_time_ms", "out_time_us"}:
        return None
    try:
        microseconds = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(microseconds) or microseconds < 0:
        return None
    return microseconds / 1000000


def _selected_source_matches(task: dict, info: dict) -> bool:
    if not isinstance(info, dict) or info.get("id") != task["media_id"]:
        return False
    requested = info.get("requested_formats")
    raw = requested if isinstance(requested, list) else [info]
    expected_ids = {task["video_format_id"]}
    if task.get("audio_format_id"):
        expected_ids.add(task["audio_format_id"])
    if len(raw) != len(expected_ids):
        return False
    formats = build_formats_response(task["media_id"], raw)
    height = int(task["resolution"][:-1]) if task.get("resolution") else None
    selected = select_streams(formats.formats, OutputFormat(task["format"]), height)
    if not selected:
        return False
    selected_ids = {selected[0].format_id}
    if selected[1]:
        selected_ids.add(selected[1].format_id)
    if selected_ids != expected_ids:
        return False
    bitrate = task.get("audio_bitrate_kbps")
    if bitrate is not None and (selected[0].audio_bitrate is None or selected[0].audio_bitrate < bitrate):
        return False
    return True


class WorkerFailure(Exception):
    def __init__(self, code: str, stage: str, tool: str, cause: Exception | None = None, exit_code: int | None = None, timed_out: bool = False, diagnostic: str = ""):
        super().__init__(failure_message(code))
        self.code = code
        self.stage = stage
        self.tool = tool
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.exception_type = type(cause).__name__ if cause is not None else None
        self.diagnostic = safe_diagnostic(diagnostic or cause or "")
        self.ffmpeg_pid = None
        self.ffmpeg_cpu_seconds = None
        self.cpu_limit_seconds = None

    def event(self) -> dict:
        return {"code": self.code, "stage": self.stage, "tool": self.tool, "exit_code": self.exit_code, "timed_out": self.timed_out, "exception_type": self.exception_type, "diagnostic": self.diagnostic, "ffmpeg_pid": self.ffmpeg_pid, "ffmpeg_cpu_seconds": self.ffmpeg_cpu_seconds, "cpu_limit_seconds": self.cpu_limit_seconds}


def _emit(state: str, **fields) -> None:
    print(json.dumps({"state": state, **fields}, separators=(",", ":")), flush=True)


def _validate_task(task: dict) -> UUID:
    job_id = UUID(task["job_id"])
    if not video_id_is_valid(task["media_id"]) or task["format"] not in {"mp4", "webm", "mp3", "m4a", "opus"}:
        raise ValueError("Invalid conversion task")
    if not FORMAT_ID.fullmatch(task["video_format_id"]) or task.get("audio_format_id") and not FORMAT_ID.fullmatch(task["audio_format_id"]):
        raise ValueError("Invalid stream ID")
    if task.get("resolution") is not None and not re.fullmatch(r"[1-9][0-9]{0,3}p", task["resolution"]):
        raise ValueError("Invalid resolution")
    return job_id


def _limits(task: dict) -> None:
    if os.geteuid() != 0:
        raise PermissionError("Media worker requires root privileges")
    os.umask(0o077)
    timeout = int(task["timeout_seconds"])
    size = max(int(task["max_download_bytes"]), int(task["max_output_bytes"]))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cpu_limit = _cpu_limit_seconds(timeout)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_AS, (1610612736, 1610612736))


def run_conversion(task: dict) -> None:
    job_id = _validate_task(task)
    _limits(task)
    settings = SimpleNamespace(ffmpeg_path=Path(task["ffmpeg_path"]), temp_dir=project_path("data/temp"), output_dir=project_path("data/output"))
    temp_directory = job_directory("temp", job_id)
    output_directory = job_directory("output", job_id)
    temp_directory.mkdir(mode=0o700)
    output_directory.mkdir(mode=0o700)
    yt_dlp = _yt_dlp_module()
    source_validated = False

    class GuardedYoutubeDL(yt_dlp.YoutubeDL):
        def urlopen(self, request):
            target = request if isinstance(request, str) else request.url
            if not _safe_network_target(target):
                raise ValueError("Unsafe network target")
            return super().urlopen(request)

        def process_info(self, info):
            nonlocal source_validated
            if not _selected_source_matches(task, info):
                raise ValueError("Selected source streams changed or are unavailable")
            source_validated = True
            return super().process_info(info)

    def progress(event: dict) -> None:
        if event.get("status") != "downloading":
            return
        downloaded = int(event.get("downloaded_bytes") or 0)
        if downloaded > int(task["max_download_bytes"]):
            raise ValueError("Download size limit exceeded")
        total = int(event.get("total_bytes") or event.get("total_bytes_estimate") or 0)
        if total > 0:
            _emit("downloading", progress=min(100, downloaded * 100 / total))

    class DownloadLogger:
        def __init__(self):
            self.error_text = ""

        def debug(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            self.error_text = safe_diagnostic(message)

    download_logger = DownloadLogger()

    selected = task["video_format_id"]
    if task.get("audio_format_id"):
        selected += "+" + task["audio_format_id"]
    options = {
        "format": selected,
        "outtmpl": str(temp_directory / "source.%(ext)s"),
        "merge_output_format": "mkv" if task.get("audio_format_id") else None,
        "ffmpeg_location": task["ffmpeg_path"],
        "noplaylist": True,
        "cachedir": False,
        "max_filesize": int(task["max_download_bytes"]),
        "socket_timeout": 8,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "js_runtimes": {},
        "remote_components": [],
        "quiet": True,
        "no_warnings": True,
        "logger": download_logger,
        "progress_hooks": [progress],
    }
    stage = "download"

    def postprocessor(event: dict) -> None:
        nonlocal stage
        if event.get("status") == "started" and "Merger" in str(event.get("postprocessor", "")):
            stage = "merge"

    options["postprocessor_hooks"] = [postprocessor]
    try:
        with GuardedYoutubeDL(options) as downloader:
            result = downloader.download([canonical_youtube_url(task["media_id"])])
    except Exception as error:
        code = "merge_failed" if stage == "merge" else "download_failed"
        diagnostic = " | ".join(part for part in (download_logger.error_text, safe_diagnostic(error)) if part)
        raise WorkerFailure(code, stage, "yt-dlp", cause=error, diagnostic=diagnostic) from None
    if result != 0:
        code = "merge_failed" if stage == "merge" else "download_failed"
        raise WorkerFailure(code, stage, "yt-dlp", exit_code=result, diagnostic=download_logger.error_text or "yt-dlp returned a nonzero status")
    if not source_validated:
        raise WorkerFailure("download_failed", "download", "yt-dlp", diagnostic="Source stream selection was not verified")
    candidates = [path for path in temp_directory.iterdir() if path.is_file() and not path.is_symlink() and path.suffix not in {".part", ".ytdl"}]
    if len(candidates) != 1:
        raise WorkerFailure("download_failed", "download", "filesystem", diagnostic="Unexpected source files")
    source = candidates[0]
    if source.stat().st_size > int(task["max_download_bytes"]):
        raise WorkerFailure("download_failed", "download", "filesystem", diagnostic="Download size limit exceeded")
    try:
        details = probe_media(Path(task["ffprobe_path"]), source)
    except subprocess.TimeoutExpired as error:
        raise WorkerFailure("conversion_timeout", "probe", "ffprobe", cause=error, timed_out=True) from None
    except subprocess.CalledProcessError as error:
        raise WorkerFailure("probe_failed", "probe", "ffprobe", cause=error, exit_code=error.returncode, diagnostic=error.stderr or error) from None
    except Exception as error:
        raise WorkerFailure("probe_failed", "probe", "ffprobe", cause=error) from None
    if task["format"] in {"mp4", "webm"} and not details["video_codec"]:
        raise WorkerFailure("probe_failed", "probe", "ffprobe", diagnostic="Video stream is missing")
    if not details["audio_codec"]:
        raise WorkerFailure("probe_failed", "probe", "ffprobe", diagnostic="Audio stream is missing")
    _emit("processing")
    filename = f"result.{task['format']}"
    try:
        destination = job_file("output", job_id, filename)
    except (OSError, ValueError) as error:
        raise WorkerFailure("output_finalization_failed", "finalize", "filesystem", cause=error) from None
    height = int(task["resolution"][:-1]) if task.get("resolution") else None
    if height is not None and (not isinstance(details["height"], int) or details["height"] < height):
        raise WorkerFailure("probe_failed", "probe", "ffprobe", diagnostic="Source resolution is below the requested output")
    try:
        command = ffmpeg_command(settings, source, destination, task["format"], video_height=height, audio_bitrate_kbps=task.get("audio_bitrate_kbps"), include_metadata=task["include_metadata"], source_video_codec=details["video_codec"], source_audio_codec=details["audio_codec"], source_height=details["height"])
    except (OSError, ValueError) as error:
        raise WorkerFailure("ffmpeg_failed", "transcode", "ffmpeg", cause=error) from None
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace", dir=temp_directory) as stderr_file:
        child_usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            process = subprocess.Popen(command, cwd=temp_directory, stdout=subprocess.PIPE, stderr=stderr_file, text=True, start_new_session=False)
        except Exception as error:
            error_code = "audio_extraction_failed" if task["format"] in {"mp3", "m4a", "opus"} else "ffmpeg_failed"
            raise WorkerFailure(error_code, "transcode", "ffmpeg", cause=error) from None
        try:
            for line in process.stdout:
                if task.get("duration_seconds"):
                    elapsed = _progress_seconds(line)
                    if elapsed is not None:
                        _emit("processing_progress", progress=min(99, elapsed * 100 / task["duration_seconds"]))
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                raise WorkerFailure("conversion_timeout", "transcode", "ffmpeg", cause=error, timed_out=True) from None
            if code != 0:
                stderr_file.seek(0, 2)
                stderr_file.seek(max(0, stderr_file.tell() - 4096))
                error_code = "audio_extraction_failed" if task["format"] in {"mp3", "m4a", "opus"} else "ffmpeg_failed"
                failure = WorkerFailure(error_code, "transcode", "ffmpeg", exit_code=code, diagnostic=stderr_file.read())
                failure.ffmpeg_pid = getattr(process, "pid", None)
                child_usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
                failure.ffmpeg_cpu_seconds = round(max(0, child_usage_after.ru_utime + child_usage_after.ru_stime - child_usage_before.ru_utime - child_usage_before.ru_stime), 2)
                failure.cpu_limit_seconds = _cpu_limit_seconds(int(task["timeout_seconds"]))
                raise failure
        except WorkerFailure:
            raise
        except Exception as error:
            raise WorkerFailure("worker_failed", "transcode", "worker", cause=error) from None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if not destination.is_file() or destination.is_symlink() or destination.stat().st_size > int(task["max_output_bytes"]):
        raise WorkerFailure("output_validation_failed", "finalize", "filesystem", diagnostic="Output file is missing, unsafe, or too large")
    try:
        output_details = probe_media(Path(task["ffprobe_path"]), destination, 10)
    except subprocess.TimeoutExpired as error:
        raise WorkerFailure("conversion_timeout", "finalize", "ffprobe", cause=error, timed_out=True) from None
    except subprocess.CalledProcessError as error:
        raise WorkerFailure("output_validation_failed", "finalize", "ffprobe", cause=error, exit_code=error.returncode, diagnostic=error.stderr or error) from None
    except Exception as error:
        raise WorkerFailure("output_validation_failed", "finalize", "ffprobe", cause=error) from None
    if task["format"] in {"mp4", "webm"}:
        if not output_details.get("video_codec") or not output_details.get("audio_codec"):
            raise WorkerFailure("output_validation_failed", "finalize", "ffprobe", diagnostic="Output video or audio stream is missing")
        format_name = str(output_details.get("format_name") or "").lower().split(",")
        expected_containers = {"mp4", "mov"} if task["format"] == "mp4" else {"webm", "matroska"}
        if not expected_containers.intersection(format_name):
            raise WorkerFailure("output_validation_failed", "finalize", "ffprobe", diagnostic="Output container does not match the requested format")
        try:
            duration = float(output_details.get("duration"))
        except (TypeError, ValueError):
            duration = 0
        if not math.isfinite(duration) or duration <= 0:
            raise WorkerFailure("output_validation_failed", "finalize", "ffprobe", diagnostic="Output duration is missing or invalid")
        if height is not None and output_details.get("height") != height:
            raise WorkerFailure("output_validation_failed", "finalize", "ffprobe", diagnostic="Output resolution does not match the requested quality")
    elif not output_details.get("audio_codec"):
        raise WorkerFailure("output_validation_failed", "finalize", "ffprobe", diagnostic="Output audio stream is missing")
    _emit("completed", output_filename=filename)


def main() -> None:
    try:
        task = json.loads(sys.stdin.buffer.read(8192))
        run_conversion(task)
    except WorkerFailure as error:
        _emit("failed", **error.event())
        raise SystemExit(1) from None
    except Exception as error:
        _emit("failed", **WorkerFailure("worker_failed", "setup", "worker", cause=error).event())
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
