import asyncio
import json
import secrets
from ipaddress import IPv6Address, ip_address
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.schemas.api import CreateJobRequest, ErrorResponse, JobListResponse, JobResponse
from app.services.jobs import JobError


router = APIRouter()
SESSION_COOKIE = "ytconvert_session"


def _session(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token and len(token) == 43 and all(char.isalnum() or char in "-_" for char in token):
        return token
    return None


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed = {request.app.state.settings.app_public_url, str(request.base_url).rstrip("/")}
    if origin and origin not in allowed:
        raise JobError(403, "invalid_origin", "Invalid request origin")


def _check_conversion_network(request: Request) -> None:
    try:
        address = ip_address(request.client.host) if request.client else None
    except ValueError:
        address = None
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address is None or not any(address in network for network in request.app.state.settings.internal_ip_ranges):
        raise JobError(403, "network_restricted", "Conversion is restricted to authorized networks")


@router.post("/jobs", response_model=JobResponse, dependencies=[Depends(_check_conversion_network)], responses={403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
async def create_job(payload: CreateJobRequest, request: Request):
    _check_origin(request)
    manager = request.app.state.job_manager
    if manager.ready_reason:
        raise JobError(503, "worker_unavailable", manager.ready_reason)
    snapshot = await request.app.state.metadata_service.get(payload.media_id)
    token = _session(request) or secrets.token_urlsafe(32)
    record = manager.create(payload, snapshot, token)
    response = JSONResponse(status_code=201, content=manager.public(record).model_dump(mode="json"))
    response.set_cookie(SESSION_COOKIE, token, max_age=manager.settings.media_session_ttl_hours * 3600, secure=request.url.scheme == "https", httponly=True, samesite="strict", path="/")
    return response


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(request: Request) -> JobListResponse:
    manager = request.app.state.job_manager
    return JobListResponse(jobs=[manager.public(record) for record in manager.list(_session(request))])


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, request: Request) -> JobResponse:
    manager = request.app.state.job_manager
    return manager.public(manager.get(job_id, _session(request)))


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: UUID, request: Request):
    manager = request.app.state.job_manager
    token = _session(request)
    manager.get(job_id, token)
    previous = request.headers.get("last-event-id", "0")
    last_version = int(previous) if previous.isdigit() and len(previous) <= 16 else 0

    async def events():
        nonlocal last_version
        first = True
        while not await request.is_disconnected():
            record = manager.get(job_id, token)
            version = record.get("version", 0)
            if first or version > last_version:
                payload = manager.public(record).model_dump(mode="json")
                yield f"id: {version}\nevent: job\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                last_version = version
                first = False
            if record["state"] in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: UUID, request: Request) -> JobResponse:
    _check_origin(request)
    manager = request.app.state.job_manager
    return manager.public(await manager.cancel(job_id, _session(request)))


@router.delete("/jobs/{job_id}", response_model=JobResponse)
async def cancel_job_legacy(job_id: UUID, request: Request) -> JobResponse:
    return await cancel_job(job_id, request)
