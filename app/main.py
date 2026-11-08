import os
import secrets
from contextlib import asynccontextmanager
from ipaddress import ip_address, ip_network
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

import data.config._conf_ as const
from app.api.router import router
from app.core.config import PROJECT_ROOT, get_settings, project_path
from app.core.http_policy import CSP, IPBlocklist, cache_policy, sensitive_path
from app.core.ip_resolver import get_client_ip
from app.core.logging import configure_logging
from app.core.security import RateLimiter, host_is_allowed
from app.schemas.api import ErrorDetail, ErrorResponse
from app.services.jobs import JobError, JobManager
from app.services.metadata import MediaAnalysisError, MetadataService

if os.path.exists(const.YGK_ENV):
    load_dotenv(const.YGK_ENV, override=False)


def is_internal_ip(ip_str: str) -> bool:
    try:
        ip_obj = ip_address(ip_str)
        return any(ip_obj in net for net in const.INTERNAL_IP_RANGES)
    except ValueError:
        return False


def is_trusted_bot(user_agent: str) -> bool:
    return bool(user_agent) and any(bot.lower() in user_agent.lower() for bot in const.TRUSTED_BOTS)


async def verify_request(request: Request) -> bool:
    client_ip = get_client_ip(request)
    if not client_ip:
        raise HTTPException(status_code=400, detail="Client IP unavailable")
    if is_internal_ip(client_ip):
        return True
    ua = request.headers.get("user-agent", "")
    return is_trusted_bot(ua)


def create_app() -> FastAPI:
    settings = get_settings()
    logger = configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.temp_dir.is_dir() or not settings.output_dir.is_dir():
            raise RuntimeError("Media directories are missing")
        logger.info("Application startup")
        app.state.job_manager.start()
        yield
        await app.state.job_manager.stop()
        logger.info("Application shutdown")

    app = FastAPI(title=settings.app_name, debug=False, lifespan=lifespan, openapi_url="/openapi.json" if settings.enable_openapi else None, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts), www_redirect=False)
    app.state.settings = settings
    app.state.metadata_service = MetadataService()
    app.state.analysis_rate_limiter = RateLimiter()
    app.state.job_manager = JobManager(settings)
    app.state.job_rate_limiter = RateLimiter(limit=10)
    app.state.ip_blocklist = IPBlocklist(settings.ip_blocklist_file, logger)

    def error_response(request: Request, status_code: int, code: str, message: str, details=None) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                details=details,
            ),
            request_id=request.state.request_id,
        )
        return JSONResponse(status_code=status_code, content=body.model_dump())

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid4().hex
        if settings.enable_openapi and request.url.path in {"/docs", "/redoc"}:
            request.state.csp_nonce = secrets.token_urlsafe(16)
        start = perf_counter()
        host = request.headers.get("host", "")
        if not host_is_allowed(host, settings.allowed_hosts):
            response = error_response(request, 400, "invalid_host", "Invalid host header")
        elif sensitive_path(request.url.path):
            response = error_response(request, 404, "not_found", "Not found")
        elif app.state.ip_blocklist.blocks(request.client.host if request.client else "unknown"):
            response = error_response(request, 403, "ip_blocked", "Access denied")
        elif request.url.path in {"/api/analyze"} or request.url.path.startswith("/api/formats/"):
            client_address = request.client.host if request.client else "unknown"
            if not app.state.analysis_rate_limiter.allow(client_address):
                response = error_response(request, 429, "rate_limited", "Too many analysis requests")
            else:
                try:
                    response = await call_next(request)
                except Exception:
                    logger.error("Unhandled request error", extra={"request_id": request.state.request_id})
                    response = error_response(request, 500, "internal_error", "Internal server error")
        elif request.url.path == "/api/jobs" and request.method == "POST" and not app.state.job_rate_limiter.allow(request.client.host if request.client else "unknown"):
            response = error_response(request, 429, "rate_limited", "Too many job requests")
        else:
            try:
                response = await call_next(request)
            except Exception:
                logger.error("Unhandled request error", extra={"request_id": request.state.request_id})
                response = error_response(request, 500, "internal_error", "Internal server error")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "accelerometer=(),camera=(),geolocation=(),gyroscope=(),magnetometer=(),microphone=(),payment=(),usb=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
        csp = CSP
        if hasattr(request.state, "csp_nonce"):
            csp = csp.replace("script-src 'self'", f"script-src 'self' 'nonce-{request.state.csp_nonce}' https://cdn.jsdelivr.net")
            csp = csp.replace("style-src 'self'", "style-src 'self' https://cdn.jsdelivr.net")
            csp = csp.replace("img-src 'self'", "img-src 'self' https://fastapi.tiangolo.com")
        response.headers["Content-Security-Policy"] = csp
        if request.url.path.endswith("/events") and request.url.path.startswith("/api/jobs/"):
            response.headers["Cache-Control"] = "no-store, no-transform"
        elif not (request.url.path.startswith("/api/download/") and "Cache-Control" in response.headers):
            response.headers["Cache-Control"] = cache_policy(request.url.path, response.status_code)
        logger.info(
            "Request completed",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "path": getattr(request.scope.get("route"), "path", "<unmatched>"),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - start) * 1000, 2),
            },
        )
        return response

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        file_path = const.FAV_VRO
        if file_path and os.path.exists(file_path):
            return FileResponse(file_path, media_type="image/x-icon")
        raise HTTPException(status_code=404)

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon_svg():
        file_path = const.SVG_VRO
        if file_path and os.path.exists(file_path):
            return FileResponse(file_path, media_type="image/svg+xml")
        raise HTTPException(status_code=404)        

    @app.get("/robots.txt", include_in_schema=False)
    async def ygk_robots_txt(request: Request):
        if not await verify_request(request):
            raise HTTPException(status_code=404)
        file_path = const.YGK_TXT
        if os.path.exists(file_path):
            return FileResponse(file_path, media_type="text/plain; charset=utf-8")
        raise HTTPException(status_code=404)

    @app.get("/sitemap.xml", include_in_schema=False)
    async def ygk_sitemap_xml(request: Request):
        if not await verify_request(request):
            raise HTTPException(status_code=404)
        file_path = const.YGK_XML
        if os.path.exists(file_path):
            return FileResponse(file_path, media_type="application/xml")
        raise HTTPException(status_code=404)

    @app.get("/.well-known/security.txt", include_in_schema=False)
    async def ygk_security_txt(request: Request):
        file_path = const.YGK_SEC_TXT
        if os.path.exists(file_path):
            return FileResponse(file_path, media_type="text/plain; charset=utf-8")
        raise HTTPException(status_code=404)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = "not_implemented" if exc.status_code == 501 else "http_error"
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
        return error_response(request, 422, "validation_error", "Invalid request data", details)

    @app.exception_handler(MediaAnalysisError)
    async def analysis_error(request: Request, exc: MediaAnalysisError):
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(JobError)
    async def job_error(request: Request, exc: JobError):
        return error_response(request, exc.status_code, exc.code, exc.message)

    app.include_router(router)

    if settings.enable_openapi:
        @app.get("/docs", include_in_schema=False)
        def swagger_docs(request: Request):
            response = get_swagger_ui_html(
                openapi_url="/openapi.json",
                title=f"{settings.app_name} API",
            )
            body = bytes(response.body).decode("utf-8").replace(
                "<script",
                f'<script nonce="{request.state.csp_nonce}"',
            )
            return HTMLResponse(body)

        @app.get("/redoc", include_in_schema=False)
        def redoc_docs(request: Request):
            response = get_redoc_html(openapi_url="/openapi.json", title=f"{settings.app_name} API", with_google_fonts=False)
            body = bytes(response.body).decode("utf-8").replace(
                "<script",
                f'<script nonce="{request.state.csp_nonce}"',
            )
            return HTMLResponse(body)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(project_path("frontend/index.html"))

    app.mount("/css", StaticFiles(directory=PROJECT_ROOT / "frontend/css"), name="css")
    app.mount("/js", StaticFiles(directory=PROJECT_ROOT / "frontend/js"), name="js")
    app.mount("/assets", StaticFiles(directory=PROJECT_ROOT / "frontend/assets"), name="assets")
    return app
