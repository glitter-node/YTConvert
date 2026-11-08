import os
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / "data/config/env/ytconvert/.env"


def project_path(value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("Path must remain inside the project root")
    return resolved


def _env_file_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key.strip().isidentifier():
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_debug: bool
    app_host: str
    app_port: int
    app_timezone: str
    app_public_url: str
    allowed_hosts: tuple[str, ...]
    internal_ip_ranges: tuple[IPv4Network | IPv6Network, ...]
    ffmpeg_path: Path
    media_max_concurrent_jobs: int
    media_output_retention_hours: int
    temp_dir: Path
    output_dir: Path
    jobs_dir: Path
    ffprobe_path: Path
    media_worker_enabled: bool
    media_max_queued_jobs: int
    media_max_duration_seconds: int
    media_max_download_bytes: int
    media_max_output_bytes: int
    media_job_timeout_seconds: int
    media_session_ttl_hours: int
    ip_blocklist_file: Path
    enable_openapi: bool


def get_settings() -> Settings:
    values = {**_env_file_values(), **os.environ}

    def value(key: str, default: str) -> str:
        return values.get(key, default)

    port = int(value("APP_PORT", "8010"))
    concurrent = int(value("MEDIA_MAX_CONCURRENT_JOBS", "1"))
    retention = int(value("MEDIA_OUTPUT_RETENTION_HOURS", "24"))
    if not 1 <= port <= 65535 or concurrent < 1 or retention < 1:
        raise ValueError("Invalid numeric application setting")
    timezone = value("APP_TIMEZONE", "Asia/Seoul")
    ZoneInfo(timezone)
    public_url = value("APP_PUBLIC_URL", "https://ytconvert.glitter.kr")
    parsed = urlparse(public_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid public URL")
    hosts = tuple(host.strip().lower() for host in value("APP_ALLOWED_HOSTS", "ytconvert.glitter.kr,127.0.0.1,localhost").split(",") if host.strip())
    if not hosts or any("/" in host or "*" in host or ":" in host for host in hosts):
        raise ValueError("Invalid allowed hosts")
    raw_ranges = value("INTERNAL_IP_RANGES", "").strip()
    try:
        internal_ip_ranges = tuple(ip_network(item.strip(), strict=False) for item in raw_ranges.split(",")) if raw_ranges else ()
    except ValueError as exc:
        raise ValueError("Invalid INTERNAL_IP_RANGES: expected comma-separated IP addresses or CIDRs") from exc
    ffmpeg = Path(value("FFMPEG_PATH", "/opt/bin/ffmpeg"))
    ffprobe = Path(value("FFPROBE_PATH", "/opt/bin/ffprobe"))
    if not ffmpeg.is_absolute() or not ffprobe.is_absolute():
        raise ValueError("Media executable paths must be absolute")
    limits = {
        "media_max_queued_jobs": int(value("MEDIA_MAX_QUEUED_JOBS", "10")),
        "media_max_duration_seconds": int(value("MEDIA_MAX_DURATION_SECONDS", "3600")),
        "media_max_download_bytes": int(value("MEDIA_MAX_DOWNLOAD_BYTES", "1073741824")),
        "media_max_output_bytes": int(value("MEDIA_MAX_OUTPUT_BYTES", "1073741824")),
        "media_job_timeout_seconds": int(value("MEDIA_JOB_TIMEOUT_SECONDS", "1800")),
        "media_session_ttl_hours": int(value("MEDIA_SESSION_TTL_HOURS", "168")),
    }
    if any(item < 1 for item in limits.values()) or concurrent != 1:
        raise ValueError("Invalid media worker settings")
    return Settings(
        app_name=value("APP_NAME", "YTConvert"),
        app_env=value("APP_ENV", "development"),
        app_debug=value("APP_DEBUG", "false").lower() == "true",
        app_host=value("APP_HOST", "127.0.0.1"),
        app_port=port,
        app_timezone=timezone,
        app_public_url=public_url.rstrip("/"),
        allowed_hosts=hosts,
        internal_ip_ranges=internal_ip_ranges,
        ffmpeg_path=ffmpeg,
        media_max_concurrent_jobs=concurrent,
        media_output_retention_hours=retention,
        temp_dir=project_path("data/temp"),
        output_dir=project_path("data/output"),
        jobs_dir=project_path("data/jobs"),
        ffprobe_path=ffprobe,
        media_worker_enabled=value("MEDIA_WORKER_ENABLED", "false").lower() == "true",
        ip_blocklist_file=project_path(value("APP_IP_BLOCKLIST_FILE", "data/config/blocked_ips.json")),
        enable_openapi=value("APP_ENABLE_OPENAPI", "false").lower() == "true" and value("APP_ENV", "development").lower() == "development",
        **limits,
    )
