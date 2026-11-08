from fastapi import APIRouter, Request
from app.services.encoders import REQUIRED_ENCODERS, unsupported_reason

from app.schemas.api import HealthResponse, StatusResponse


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/status", response_model=StatusResponse)
def status(request: Request) -> StatusResponse:
    settings = request.app.state.settings
    manager = request.app.state.job_manager
    capabilities = {"api": "available", "metadata_analysis": "available", "format_discovery": "available", "conversion_worker": "available" if manager.ready_reason is None else "unavailable", "conversion_reason": manager.ready_reason or "ready"}
    capabilities.update({f"conversion_{name}": "available" if manager.ready_reason is None and unsupported_reason(name, manager.encoders) is None else "unavailable" for name in REQUIRED_ENCODERS})
    return StatusResponse(
        service=settings.app_name,
        status="operational",
        environment=settings.app_env,
        capabilities=capabilities,
    )
