import re


FAILURE_MESSAGES = {
    "download_failed": "Download failed",
    "audio_extraction_failed": "Audio extraction failed",
    "merge_failed": "Audio merge failed",
    "probe_failed": "Source inspection failed",
    "ffmpeg_failed": "FFmpeg conversion failed",
    "conversion_timeout": "Conversion timed out",
    "output_validation_failed": "Output validation failed",
    "output_finalization_failed": "Output finalization failed",
    "worker_failed": "Conversion failed",
}


def safe_diagnostic(value: object) -> str:
    source = str(value)[-4096:]
    source = re.sub(r"(?i)\b(?:https?|ftp)://[^\s\]\[<>\"']+", "[url]", source)
    source = re.sub(r"(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]*", "[credential]", source)
    source = re.sub(r"(?i)\bauthorization\s*[:=]\s*bearer\s+[^\s,;]+", "[credential]", source)
    source = re.sub(r"(?i)\b(?:authorization|cookie|password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+", "[credential]", source)
    source = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "[credential]", source)
    source = re.sub(r"(?<![A-Za-z0-9])/(?:[^\s/:]+/)*[^\s/:]+", "[path]", source)
    source = re.sub(r"\b[A-Za-z0-9_-]{48,}\b", "[value]", source)
    source = re.sub(r"[\x00-\x1f\x7f]+", " ", source)
    return source.strip()[-1200:]


def failure_message(code: object) -> str:
    return FAILURE_MESSAGES.get(code, FAILURE_MESSAGES["worker_failed"])
