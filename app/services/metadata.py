import asyncio
import sqlite3
import math
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit

from app.core.security import THUMBNAIL_HOSTS, YOUTUBE_HOSTS, canonical_youtube_url, safe_external_link
from app.schemas.api import FormatsResponse, MediaInfo
from app.services.formats import build_formats_response


@dataclass(frozen=True)
class SourceMedia:
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    audio_bitrate_kbps: int | None


@dataclass(frozen=True)
class AnalysisSnapshot:
    media: MediaInfo
    formats: FormatsResponse


class MediaAnalysisError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class _SilentLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def _yt_dlp_module():
    if not hasattr(sqlite3, "sqlite_version"):
        sqlite3.sqlite_version = "0.0.0"
    import yt_dlp
    return yt_dlp


def _safe_network_target(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.port is not None or parsed.username or parsed.password:
        return False
    allowed = host in YOUTUBE_HOSTS or host in THUMBNAIL_HOSTS or any(host == domain or host.endswith(f".{domain}") for domain in ("googlevideo.com", "googleapis.com", "gstatic.com"))
    return allowed


def _read_text(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _read_count(value: object) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value < 10000000000:
        return int(value)
    return None


def _snapshot_from_info(media_id: str, info: object) -> AnalysisSnapshot:
    if not isinstance(info, dict) or info.get("id") != media_id or info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        raise MediaAnalysisError(502, "invalid_source", "The source did not resolve to one video")
    raw_formats = info.get("formats")
    if not isinstance(raw_formats, list) or len(raw_formats) > 500:
        raise MediaAnalysisError(502, "invalid_source", "The source format list is unavailable or too large")
    formats = build_formats_response(media_id, raw_formats)
    media = MediaInfo(
        media_id=media_id,
        title=_read_text(info.get("title"), 500),
        description=_read_text(info.get("description"), 4000),
        channel=_read_text(info.get("channel"), 250),
        channel_url=safe_external_link(info.get("channel_url"), YOUTUBE_HOSTS),
        duration=_read_count(info.get("duration")),
        thumbnail=safe_external_link(info.get("thumbnail"), THUMBNAIL_HOSTS),
        webpage_url=canonical_youtube_url(media_id),
        upload_date=_read_text(info.get("upload_date"), 8),
        view_count=_read_count(info.get("view_count")),
        live_status=_read_text(info.get("live_status"), 40),
        availability=_read_text(info.get("availability"), 40),
        format_count=len(formats.formats),
    )
    return AnalysisSnapshot(media=media, formats=formats)


def _classify_download_error(error: Exception) -> MediaAnalysisError:
    message = str(error).lower()
    if "private" in message:
        return MediaAnalysisError(403, "private_video", "This video is private")
    if "age" in message and ("restrict" in message or "confirm" in message):
        return MediaAnalysisError(403, "age_restricted", "This video is age restricted")
    if "geo" in message or "country" in message or "region" in message:
        return MediaAnalysisError(451, "region_restricted", "This video is unavailable in this region")
    if "deleted" in message or "removed" in message or "unavailable" in message:
        return MediaAnalysisError(404, "video_unavailable", "This video is unavailable")
    return MediaAnalysisError(502, "extraction_failed", "Video metadata could not be retrieved")


def extract_youtube(media_id: str) -> AnalysisSnapshot:
    yt_dlp = _yt_dlp_module()

    class GuardedYoutubeDL(yt_dlp.YoutubeDL):
        def urlopen(self, request):
            target = request if isinstance(request, str) else request.url
            if not _safe_network_target(target):
                raise MediaAnalysisError(502, "unsafe_source", "The source requested an unsafe network location")
            return super().urlopen(request)

    options = {
        "skip_download": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 8,
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
        "ignore_no_formats_error": True,
        "js_runtimes": {},
        "remote_components": [],
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
    }
    try:
        with GuardedYoutubeDL(options) as downloader:
            info = downloader.extract_info(canonical_youtube_url(media_id), download=False, ie_key="Youtube")
    except MediaAnalysisError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        raise _classify_download_error(exc) from None
    except Exception:
        raise MediaAnalysisError(502, "extraction_failed", "Video metadata could not be retrieved") from None
    return _snapshot_from_info(media_id, info)


class MetadataService:
    def __init__(self, extractor: Callable[[str], AnalysisSnapshot] = extract_youtube, ttl_seconds: int = 600, max_entries: int = 100, concurrent: int = 2, timeout_seconds: int = 30):
        self.extractor = extractor
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.timeout_seconds = timeout_seconds
        self._cache: OrderedDict[str, tuple[float, AnalysisSnapshot]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(concurrent)

    async def _extract(self, media_id: str) -> AnalysisSnapshot:
        try:
            async with self._semaphore:
                result = await asyncio.to_thread(self.extractor, media_id)
            async with self._lock:
                now = monotonic()
                self._cache[media_id] = (now + self.ttl_seconds, result)
                self._cache.move_to_end(media_id)
                while len(self._cache) > self.max_entries:
                    self._cache.popitem(last=False)
            return result
        finally:
            async with self._lock:
                self._inflight.pop(media_id, None)

    async def get(self, media_id: str) -> AnalysisSnapshot:
        async with self._lock:
            now = monotonic()
            for key, (expires, _) in list(self._cache.items()):
                if expires <= now:
                    del self._cache[key]
            cached = self._cache.get(media_id)
            if cached:
                self._cache.move_to_end(media_id)
                return cached[1]
            task = self._inflight.get(media_id)
            if task is None:
                if len(self._inflight) >= 10:
                    raise MediaAnalysisError(503, "analysis_busy", "Too many analyses are in progress")
                task = asyncio.create_task(self._extract(media_id))
                task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
                self._inflight[media_id] = task
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            raise MediaAnalysisError(504, "extraction_timeout", "Video analysis timed out") from None
