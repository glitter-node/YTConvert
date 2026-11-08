from fastapi import APIRouter, Request

from app.schemas.api import AnalyzeRequest, ErrorResponse, MediaInfo


router = APIRouter()


@router.post("/analyze", response_model=MediaInfo, responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 451: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 504: {"model": ErrorResponse}})
async def analyze(payload: AnalyzeRequest, request: Request) -> MediaInfo:
    from app.core.security import normalize_youtube_url

    media_id, _ = normalize_youtube_url(payload.url)
    snapshot = await request.app.state.metadata_service.get(media_id)
    return snapshot.media
