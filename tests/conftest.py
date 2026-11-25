from dataclasses import replace
from ipaddress import ip_network

import pytest

import app.main as main_module
import app.services.jobs as jobs_module
from app.core.config import get_settings


@pytest.fixture
def isolated_disabled_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_WORKER_ENABLED", "false")
    monkeypatch.setenv("APP_ALLOWED_HOSTS", "ytconvert.glitter.kr,127.0.0.1,localhost")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://ytconvert.glitter.kr")
    monkeypatch.setattr(jobs_module, "prepare_storage", lambda settings: None)
    monkeypatch.setattr(jobs_module, "load_records", lambda settings: [])

    def create(ranges=""):
        monkeypatch.setenv("INTERNAL_IP_RANGES", ranges)
        settings = replace(get_settings(), jobs_dir=tmp_path, ip_blocklist_file=tmp_path / "blocked.json")
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)
        return main_module.create_app()

    return create
