from fastapi import APIRouter, Path, Request

from app.schemas.api import ErrorResponse, FormatsResponse
from app.services.encoders import unsupported_reason


router = APIRouter()


@router.get("/formats/{media_id}", response_model=FormatsResponse, responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 451: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 504: {"model": ErrorResponse}})
async def formats(request: Request, media_id: str = Path(pattern=r"^[A-Za-z0-9_-]{11}$")) -> FormatsResponse:
    snapshot = await request.app.state.metadata_service.get(media_id)
    manager = request.app.state.job_manager
    options = []
    for option in snapshot.formats.options:
        reason = manager.encoder_reason or unsupported_reason(option.format.value, manager.encoders)
        options.append(option.model_copy(update={"available": reason is None, "unavailable_reason": reason}))
    return snapshot.formats.model_copy(update={"options": options})
