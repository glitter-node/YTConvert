import json
import subprocess
from pathlib import Path

from app.core.config import Settings, project_path


def ffmpeg_command(settings: Settings, source: Path, destination: Path, output_format: str, video_height: int | None = None, audio_bitrate_kbps: int | None = None, include_metadata: bool = True, source_video_codec: str | None = None, source_audio_codec: str | None = None, source_height: int | None = None) -> list[str]:
    source = project_path(source)
    destination = project_path(destination)
    if not source.is_relative_to(settings.temp_dir) or not destination.is_relative_to(settings.output_dir):
        raise ValueError("Media paths must use the designated directories")
    if output_format not in {"mp4", "webm", "mp3", "m4a", "opus"} or destination.suffix != f".{output_format}":
        raise ValueError("Unsupported output format")
    if video_height is not None and not 1 <= video_height <= 4320:
        raise ValueError("Unsupported video resolution")
    if audio_bitrate_kbps is not None and audio_bitrate_kbps not in {128, 192, 256, 320}:
        raise ValueError("Unsupported audio bitrate")
    command = [str(settings.ffmpeg_path), "-nostdin", "-hide_banner", "-y", "-threads", "2", "-protocol_whitelist", "file,pipe", "-i", str(source), "-map_metadata", "0" if include_metadata else "-1"]
    if output_format in {"mp3", "m4a", "opus"}:
        audio_encoder = {"mp3": "libmp3lame", "m4a": "aac", "opus": "libopus"}[output_format]
        can_copy = audio_bitrate_kbps is None and ((output_format == "m4a" and source_audio_codec == "aac") or (output_format == "opus" and source_audio_codec == "opus"))
        command += ["-vn", "-map", "0:a:0", "-c:a", "copy" if can_copy else audio_encoder]
    else:
        command += ["-map", "0:v:0", "-map", "0:a:0"]
        needs_scale = video_height is not None and source_height is not None and source_height > video_height
        if needs_scale:
            command += ["-vf", f"scale=-2:{video_height}"]
        if output_format == "mp4":
            command += ["-c:v", "copy" if source_video_codec == "h264" and not needs_scale else "libx264", "-c:a", "copy" if source_audio_codec == "aac" else "aac", "-movflags", "+faststart"]
        else:
            command += ["-c:v", "copy" if source_video_codec in {"vp8", "vp9"} and not needs_scale else "libvpx-vp9", "-c:a", "copy" if source_audio_codec in {"opus", "vorbis"} else "libopus"]
    if audio_bitrate_kbps is not None:
        command += ["-b:a", f"{audio_bitrate_kbps}k"]
    return command + ["-threads", "2", "-filter_threads", "2", "-progress", "pipe:1", "-nostats", str(destination)]


def probe_media(ffprobe_path: Path, source: Path, timeout: int = 5) -> dict:
    result = subprocess.run([str(ffprobe_path), "-v", "error", "-protocol_whitelist", "file", "-show_entries", "stream=codec_type,codec_name,width,height,duration,pix_fmt:format=format_name,duration", "-show_streams", "-show_format", "-of", "json", str(source)], capture_output=True, text=True, timeout=timeout, check=True)
    info = json.loads(result.stdout)
    video = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), {})
    audio = next((item for item in info.get("streams", []) if item.get("codec_type") == "audio"), {})
    container = info.get("format", {})
    return {"video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name"), "height": video.get("height"), "width": video.get("width"), "duration": video.get("duration") or audio.get("duration") or container.get("duration"), "pixel_format": video.get("pix_fmt"), "format_name": container.get("format_name")}


def probe_video_frames(ffprobe_path: Path, source: Path, timeout: int = 30) -> int | None:
    result = subprocess.run([str(ffprobe_path), "-v", "error", "-protocol_whitelist", "file", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,nb_frames", "-of", "json", str(source)], capture_output=True, text=True, timeout=timeout, check=True)
    info = json.loads(result.stdout)
    video = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), info.get("streams", [{}])[0] if info.get("streams") else {})
    frame_value = video.get("nb_read_frames", video.get("nb_frames"))
    try:
        video_frames = int(frame_value) if frame_value not in (None, "N/A", "") else None
    except (TypeError, ValueError):
        video_frames = None
    return video_frames
