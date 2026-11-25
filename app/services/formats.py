import re

from app.schemas.api import FormatOption, FormatsResponse, OutputFormat, SourceFormat


AUDIO_BITRATES = (128, 192, 256, 320)
SOURCE_EXTENSIONS = frozenset({"mp4", "webm", "m4a", "mp3", "opus", "mkv"})
VIDEO_CODECS = ("avc1", "h264", "av01", "av1", "vp09", "vp9", "vp08", "vp8")
AUDIO_CODECS = ("mp4a", "aac", "opus", "vorbis", "mp3")
FORMAT_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def available_resolutions(source) -> tuple[str, ...]:
    if source.height is None or source.height <= 0:
        return ()
    return (f"{source.height}p",)


def available_audio_bitrates(source) -> tuple[int, ...]:
    if source.audio_bitrate_kbps is None or source.audio_bitrate_kbps <= 0:
        return ()
    return tuple(rate for rate in AUDIO_BITRATES if rate <= source.audio_bitrate_kbps)


def _text(value: object, limit: int = 80) -> str | None:
    return value[:limit] if isinstance(value, str) and value and value != "none" else None


def _positive_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 10000000000:
        return float(value)
    return None


def _positive_integer(value: object) -> int | None:
    number = _positive_number(value)
    return int(number) if number is not None else None


def _usable(item: SourceFormat) -> bool:
    return bool(FORMAT_ID.fullmatch(item.format_id) and item.extension in SOURCE_EXTENSIONS)


def _codec_supported(codec: str | None, names: tuple[str, ...]) -> bool:
    return bool(codec and (codec.lower() in names or any(codec.lower().startswith(f"{name}.") for name in names)))


def _video_usable(item: SourceFormat) -> bool:
    return bool(_usable(item) and item.has_video and item.height is not None and 0 < item.height <= 4320 and _codec_supported(item.video_codec, VIDEO_CODECS))


def _audio_usable(item: SourceFormat) -> bool:
    return bool(_usable(item) and item.has_audio and _codec_supported(item.audio_codec, AUDIO_CODECS))


def _video_preference(item: SourceFormat, output_format: OutputFormat) -> tuple[int, int, float]:
    codec = (item.video_codec or "").lower()
    if output_format == OutputFormat.mp4:
        codec_rank = 4 if codec == "h264" or codec.startswith("avc1.") else 3 if codec in {"vp9", "vp8"} or codec.startswith("vp09.") or codec.startswith("vp08.") else 2 if codec == "av1" or codec.startswith("av01.") else 1
    else:
        codec_rank = 4 if codec == "vp9" or codec.startswith("vp09.") else 3 if codec == "vp8" or codec.startswith("vp08.") else 2 if codec == "h264" or codec.startswith("avc1.") else 1
    container_rank = int(item.extension == output_format.value)
    return codec_rank, container_rank, item.video_bitrate or 0


def select_streams(streams: list[SourceFormat], output_format: OutputFormat, height: int | None = None) -> tuple[SourceFormat, SourceFormat | None] | None:
    audio = sorted((item for item in streams if _audio_usable(item)), key=lambda item: (item.audio_bitrate or 0, not item.has_video, item.extension in {"m4a", "webm"}), reverse=True)
    if output_format in {OutputFormat.mp3, OutputFormat.m4a, OutputFormat.opus}:
        return (audio[0], None) if audio else None
    if height is None:
        return None
    videos = sorted((item for item in streams if _video_usable(item) and item.height == height and (not item.has_audio or _audio_usable(item))), key=lambda item: (_video_preference(item, output_format), item.has_audio), reverse=True)
    audio_only = [item for item in audio if not item.has_video]
    for video in videos:
        if video.has_audio:
            return video, None
        if audio_only:
            return video, audio_only[0]
    return None


def build_formats_response(media_id: str, raw_formats: list[object]) -> FormatsResponse:
    source_formats: list[SourceFormat] = []
    for raw in raw_formats:
        if not isinstance(raw, dict) or not isinstance(raw.get("format_id"), str):
            continue
        format_id = raw["format_id"]
        if format_id in {"sb0", "sb1", "sb2", "sb3"} or format_id.startswith("sb") or raw.get("ext") == "mhtml":
            continue
        video_codec = _text(raw.get("vcodec"))
        audio_codec = _text(raw.get("acodec"))
        if not video_codec and not audio_codec:
            continue
        source_formats.append(SourceFormat(
            format_id=format_id[:80],
            extension=_text(raw.get("ext")),
            container=_text(raw.get("container")),
            video_codec=video_codec,
            audio_codec=audio_codec,
            width=_positive_integer(raw.get("width")),
            height=_positive_integer(raw.get("height")),
            fps=_positive_number(raw.get("fps")),
            video_bitrate=_positive_number(raw.get("vbr")),
            audio_bitrate=_positive_number(raw.get("abr")),
            total_bitrate=_positive_number(raw.get("tbr")),
            filesize=_positive_integer(raw.get("filesize")),
            filesize_approx=_positive_integer(raw.get("filesize_approx")),
            dynamic_range=_text(raw.get("dynamic_range")),
            has_video=video_codec is not None,
            has_audio=audio_codec is not None,
        ))
    options: list[FormatOption] = []
    audio_sources = [item for item in source_formats if _audio_usable(item)]
    heights = sorted({item.height for item in source_formats if _video_usable(item)})
    for output_format in (OutputFormat.mp4, OutputFormat.webm):
        for height in heights:
            selected = select_streams(source_formats, output_format, height)
            if selected:
                options.append(FormatOption(format=output_format, resolution=f"{height}p", quality_note="Output conversion cannot improve source quality", requires_audio_merge=selected[1] is not None))
    if audio_sources:
        max_bitrate = max((item.audio_bitrate or 0) for item in audio_sources)
        bitrates = [value for value in AUDIO_BITRATES if value <= max_bitrate]
        for output_format in (OutputFormat.mp3, OutputFormat.m4a, OutputFormat.opus):
            if bitrates:
                for bitrate in bitrates:
                    options.append(FormatOption(format=output_format, audio_bitrate_kbps=bitrate, quality_note="Conversion output; source audio bitrate is at least this value"))
            else:
                note = "Source audio bitrate is below available output presets" if max_bitrate > 0 else "Source audio bitrate is unknown"
                options.append(FormatOption(format=output_format, quality_note=note))
    return FormatsResponse(media_id=media_id, formats=source_formats, options=options)
