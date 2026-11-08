from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.security import normalize_youtube_url, video_id_is_valid


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str


class HealthResponse(BaseModel):
    status: str


class StatusResponse(BaseModel):
    service: str
    status: str
    environment: str
    capabilities: dict[str, str]


class AnalyzeRequest(BaseModel):
    url: str = Field(max_length=2048)

    @field_validator("url")
    @classmethod
    def youtube_url_only(cls, value: str) -> str:
        return normalize_youtube_url(value)[1]


class MediaInfo(BaseModel):
    media_id: str
    source: str = "youtube"
    title: str | None = None
    description: str | None = None
    channel: str | None = None
    channel_url: str | None = None
    duration: int | None = None
    thumbnail: str | None = None
    webpage_url: str
    upload_date: str | None = None
    view_count: int | None = None
    live_status: str | None = None
    availability: str | None = None
    format_count: int = 0


class OutputFormat(str, Enum):
    mp4 = "mp4"
    webm = "webm"
    mp3 = "mp3"
    m4a = "m4a"
    opus = "opus"


class FormatOption(BaseModel):
    format: OutputFormat
    resolution: str | None = None
    audio_bitrate_kbps: int | None = None
    quality_note: str
    requires_audio_merge: bool = False
    available: bool = True
    unavailable_reason: str | None = None


class SourceFormat(BaseModel):
    format_id: str
    extension: str | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_bitrate: float | None = None
    audio_bitrate: float | None = None
    total_bitrate: float | None = None
    filesize: int | None = None
    filesize_approx: int | None = None
    dynamic_range: str | None = None
    has_video: bool
    has_audio: bool


class FormatsResponse(BaseModel):
    media_id: str
    source: str = "youtube"
    formats: list[SourceFormat]
    options: list[FormatOption]


class CreateJobRequest(BaseModel):
    media_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    format: OutputFormat
    resolution: str | None = None
    audio_bitrate_kbps: int | None = None
    include_subtitles: bool = False
    include_metadata: bool = True
    confirm_rights: bool = False

    @field_validator("resolution")
    @classmethod
    def valid_resolution(cls, value: str | None) -> str | None:
        if value is not None and (not value.endswith("p") or not value[:-1].isdigit() or not 1 <= int(value[:-1]) <= 4320):
            raise ValueError("Unsupported resolution")
        return value

    @field_validator("audio_bitrate_kbps")
    @classmethod
    def valid_bitrate(cls, value: int | None) -> int | None:
        if value is not None and value not in {128, 192, 256, 320}:
            raise ValueError("Unsupported audio bitrate")
        return value


class JobResponse(BaseModel):
    job_id: UUID
    state: str
    progress: float = Field(ge=0, le=100)
    download_url: str | None = None
    media_id: str | None = None
    format: OutputFormat | None = None
    resolution: str | None = None
    audio_bitrate_kbps: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    output_filename: str | None = None
    output_bytes: int | None = None
    download_progress: float = Field(default=0, ge=0, le=100)
    conversion_progress: float = Field(default=0, ge=0, le=100)


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
