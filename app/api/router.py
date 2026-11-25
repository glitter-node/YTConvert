from fastapi import APIRouter

from app.api import analyze, download, formats, health, jobs


router = APIRouter(prefix="/api")
router.include_router(health.router)
router.include_router(analyze.router)
router.include_router(formats.router)
router.include_router(jobs.router)
router.include_router(download.router)
