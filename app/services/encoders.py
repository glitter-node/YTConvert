import re
import subprocess

from app.core.config import Settings


REQUIRED_ENCODERS = {
    "mp4": frozenset({"libx264", "aac"}),
    "webm": frozenset({"libvpx-vp9", "libopus"}),
    "mp3": frozenset({"libmp3lame"}),
    "m4a": frozenset({"aac"}),
    "opus": frozenset({"libopus"}),
}


def detect_encoders(settings: Settings) -> frozenset[str]:
    result = subprocess.run([str(settings.ffmpeg_path), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5, check=True)
    return frozenset(match.group(1) for line in result.stdout.splitlines() if (match := re.match(r"^\s*[VAS][.A-Z]{5}\s+(\S+)(?:\s|$)", line)))


def missing_encoders(output_format: str, encoders: frozenset[str]) -> frozenset[str]:
    return REQUIRED_ENCODERS[output_format] - encoders


def unsupported_reason(output_format: str, encoders: frozenset[str]) -> str | None:
    missing = missing_encoders(output_format, encoders)
    return f"Required FFmpeg encoder unavailable: {', '.join(sorted(missing))}" if missing else None
