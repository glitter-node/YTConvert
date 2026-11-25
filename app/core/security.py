import re
from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
THUMBNAIL_HOSTS = {"i.ytimg.com", "img.youtube.com", "yt3.ggpht.com"}


def video_id_is_valid(value: str) -> bool:
    return bool(VIDEO_ID.fullmatch(value))


def canonical_youtube_url(video_id: str) -> str:
    if not video_id_is_valid(video_id):
        raise ValueError("Invalid YouTube video ID")
    return f"https://www.youtube.com/watch?v={video_id}"


def normalize_youtube_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid YouTube URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid YouTube URL") from exc
    if parsed.scheme != "https" or parsed.hostname not in YOUTUBE_HOSTS or parsed.username or parsed.password or port is not None or parsed.fragment:
        raise ValueError("Unsupported YouTube URL")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("Invalid YouTube URL parameters") from exc
    if parsed.hostname == "youtu.be":
        parts = parsed.path.split("/")
        video_id = parts[1] if len(parts) == 2 else ""
        if "v" in query:
            raise ValueError("Ambiguous YouTube video ID")
    elif parsed.path == "/watch":
        if len(query.get("v", [])) != 1:
            raise ValueError("Only one video may be analyzed")
        video_id = query["v"][0]
    else:
        parts = parsed.path.split("/")
        video_id = parts[2] if len(parts) == 3 and parts[1] in {"shorts", "live"} and "v" not in query else ""
    if not video_id_is_valid(video_id):
        raise ValueError("Invalid YouTube video ID")
    return video_id, canonical_youtube_url(video_id)


def safe_external_link(value: object, hosts: set[str]) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or port is not None:
        return None
    return value


class RateLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = monotonic()
        with self._lock:
            calls = self._calls[key]
            while calls and calls[0] <= now - self.window_seconds:
                calls.popleft()
            if len(calls) >= self.limit:
                return False
            calls.append(now)
            if len(self._calls) > 10000:
                self._calls = defaultdict(deque, {item: times for item, times in self._calls.items() if times and times[-1] > now - self.window_seconds})
            return True


def host_is_allowed(host_header: str, allowed_hosts: tuple[str, ...]) -> bool:
    if not host_header or any(character in host_header for character in "/\\@, \t\r\n"):
        return False
    try:
        parsed = urlsplit(f"//{host_header}")
        port = parsed.port
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return False
    return bool(parsed.hostname and parsed.hostname.lower() in allowed_hosts and (port is None or 1 <= port <= 65535))
