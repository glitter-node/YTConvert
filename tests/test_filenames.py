import json
import secrets
import shutil
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi.testclient import TestClient

from app.core.config import PROJECT_ROOT, get_settings
from app.main import create_app
from app.services.filenames import MAX_DOWNLOAD_FILENAME_BYTES, download_filename
from app.services.storage import job_directory
from tests.test_jobs import _manager, _request
from tests.test_metadata import VIDEO_ID, sample_snapshot


def test_download_filename_sanitizes_english_korean_and_unicode():
    assert download_filename("Beautiful Music - Live Performance", VIDEO_ID, "mp4") == "Beautiful Music - Live Performance.mp4"
    assert download_filename("Beautiful Music - Live Performance", VIDEO_ID, "mp3") == "Beautiful Music - Live Performance.mp3"
    assert download_filename("아름다운 음악", VIDEO_ID, "mp3") == "아름다운 음악.mp3"
    assert download_filename("Cafe\u0301 🪐", VIDEO_ID, "mp4") == "Café 🪐.mp4"


def test_download_filename_blocks_paths_controls_and_reserved_names():
    filename = download_filename('  ../a\\b:c*d?e"f<g>h|i\n\x00. ', VIDEO_ID, "mp4")
    assert filename.endswith(".mp4") and ".." not in filename
    assert not any(character in filename for character in '/\\:*?"<>|\n\x00')
    assert filename == Path(filename).name
    for title in ("CON", "con.txt", "PRN", "AUX", "NUL", "COM9", "LPT1", "COM¹", "LPT³"):
        assert download_filename(title, VIDEO_ID, "mp3").startswith("_")
    assert download_filename("/.\\:*?", VIDEO_ID, "mp4") == f"{VIDEO_ID}.mp4"
    assert download_filename(None, VIDEO_ID, "mp3") == f"{VIDEO_ID}.mp3"
    assert download_filename(None, "invalid", "mp3") == "result.mp3"


def test_download_filename_limits_utf8_without_splitting_characters():
    filename = download_filename("한" * 200, VIDEO_ID, "mp3")
    assert len(filename.encode("utf-8")) <= MAX_DOWNLOAD_FILENAME_BYTES
    assert filename == "한" * 65 + ".mp3"
    assert filename.encode("utf-8").decode("utf-8") == filename
    try:
        download_filename("Video", VIDEO_ID, "exe")
        raise AssertionError("Unknown extension accepted")
    except ValueError:
        pass


def test_download_headers_survive_restart_with_legacy_fallback():
    with tempfile.TemporaryDirectory(dir=get_settings().temp_dir) as base:
        manager = _manager(Path(base))
        token = secrets.token_urlsafe(32)
        outputs = []
        cases = (
            ("Beautiful Music - Live Performance", "mp4", "result.mp4", "Beautiful Music - Live Performance.mp4"),
            ("Beautiful Music - Live Performance", "mp3", "result.mp3", "Beautiful Music - Live Performance.mp3"),
            ("아름다운 음악", "mp3", "result.mp3", "아름다운 음악.mp3"),
            (None, "mp4", "result.mp4", f"{VIDEO_ID}.mp4"),
        )
        try:
            for title, output_format, internal_name, expected_name in cases:
                snapshot = sample_snapshot()
                snapshot.media.title = title
                request = _request(format=output_format, resolution="1080p" if output_format == "mp4" else None, audio_bitrate_kbps=128 if output_format == "mp3" else None)
                record = manager.create(request, snapshot, token)
                job_id = UUID(record["job_id"])
                assert record["source_title"] == title
                if title is None:
                    record.pop("source_title")
                manager._update(str(job_id), state="downloading")
                manager._update(str(job_id), state="processing")
                directory = job_directory("output", job_id)
                directory.mkdir()
                outputs.append(directory)
                (directory / internal_name).write_bytes(b"mock-output")
                manager._update(str(job_id), state="completed", progress=100, output_filename=internal_name, output_bytes=11)
                stored = json.loads((Path(base) / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
                assert stored.get("source_title") == title

            assert len({directory.name for directory in outputs}) == len(cases)
            recovered = _manager(Path(base))
            app = create_app()
            app.state.job_manager = recovered
            with TestClient(app, base_url="https://ytconvert.glitter.kr") as client:
                for directory, (_, output_format, internal_name, expected_name) in zip(outputs, cases):
                    job_id = directory.name
                    assert client.get(f"/api/download/{job_id}").status_code == 404
                    client.cookies.set("ytconvert_session", token)
                    response = client.get(f"/api/download/{job_id}")
                    assert response.status_code == 200 and response.content == b"mock-output"
                    encoded = quote(expected_name)
                    expected_header = f"attachment; filename*=utf-8''{encoded}" if encoded != expected_name else f'attachment; filename="{expected_name}"'
                    assert response.headers["Content-Disposition"] == expected_header
                    assert response.headers["Cache-Control"] == "private, no-store"
                    assert response.headers["Content-Length"] == "11"
                    assert (directory / internal_name).is_file()
                    assert response.headers["Content-Type"].startswith("video/mp4" if output_format == "mp4" else "audio/mpeg")
                    client.cookies.clear()
                client.cookies.set("ytconvert_session", secrets.token_urlsafe(32))
                assert client.get(f"/api/download/{outputs[0].name}").status_code == 404
        finally:
            for directory in outputs:
                if directory.exists():
                    shutil.rmtree(directory)


def test_frontend_uses_server_download_name():
    class DownloadLinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.attributes = None

        def handle_starttag(self, tag, attrs):
            if tag == "a" and ("id", "download-link") in attrs:
                self.attributes = dict(attrs)

    parser = DownloadLinkParser()
    parser.feed((PROJECT_ROOT / "frontend/index.html").read_text(encoding="utf-8"))
    script = (PROJECT_ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
    assert parser.attributes is not None and "download" not in parser.attributes
    assert "link.href = job.download_url" in script
    assert "createObjectURL" not in script
