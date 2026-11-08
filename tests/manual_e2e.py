import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import normalize_youtube_url
from app.main import create_app


def request_job(client: TestClient, media_id: str, option: dict) -> dict:
    payload = {"media_id": media_id, "format": option["format"], "confirm_rights": True}
    if option.get("resolution"):
        payload["resolution"] = option["resolution"]
    if option.get("audio_bitrate_kbps"):
        payload["audio_bitrate_kbps"] = option["audio_bitrate_kbps"]
    response = client.post("/api/jobs", json=payload)
    if response.status_code != 201:
        raise RuntimeError(f"Job creation failed: {response.status_code} {response.json()}")
    return response.json()


def wait_events(client: TestClient, job_id: str) -> list[dict]:
    response = client.get(f"/api/jobs/{job_id}/events")
    if response.status_code != 200:
        raise RuntimeError(f"SSE request failed: {response.status_code}")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    if not events or events[-1]["state"] not in {"completed", "failed", "cancelled"}:
        raise RuntimeError("SSE did not reach a terminal state")
    return events


def verify_download(client: TestClient, settings, job_id: str, output_format: str, directory: Path) -> dict:
    destination = directory / f"{job_id}.{output_format}"
    expected_types = {"mp4": "video/mp4", "mp3": "audio/mpeg"}
    with client.stream("GET", f"/api/download/{job_id}") as response:
        if response.status_code != 200:
            raise RuntimeError(f"Download failed: {response.status_code}")
        if response.headers.get("Cache-Control") != "private, no-store":
            raise RuntimeError("Download cache policy is invalid")
        if not response.headers.get("Content-Type", "").startswith(expected_types[output_format]):
            raise RuntimeError("Download content type is invalid")
        if f"result.{output_format}" not in response.headers.get("Content-Disposition", ""):
            raise RuntimeError("Download filename is invalid")
        content_length = int(response.headers.get("Content-Length", "0"))
        with destination.open("xb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    if content_length != destination.stat().st_size or not 0 < content_length <= settings.media_max_output_bytes:
        raise RuntimeError("Downloaded file size is invalid")
    result = subprocess.run([str(settings.ffprobe_path), "-v", "error", "-protocol_whitelist", "file", "-show_streams", "-of", "json", str(destination)], capture_output=True, text=True, timeout=15, check=True)
    streams = json.loads(result.stdout).get("streams", [])
    if not any(item.get("codec_type") == "audio" for item in streams):
        raise RuntimeError("Audio stream is missing")
    if output_format == "mp4" and not any(item.get("codec_type") == "video" for item in streams):
        raise RuntimeError("Video stream is missing")
    return {"bytes": content_length, "streams": [item.get("codec_type") for item in streams]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--confirm-rights", action="store_true")
    args = parser.parse_args()
    if not args.confirm_rights:
        raise SystemExit("Confirm processing rights with --confirm-rights")
    if os.geteuid() != 0:
        raise SystemExit("The media worker requires a root process")
    media_id, _ = normalize_youtube_url(args.url)
    os.environ["MEDIA_WORKER_ENABLED"] = "true"
    settings = get_settings()
    app = create_app()
    report = {"media_id": media_id, "jobs": [], "error_checks": {}}
    with tempfile.TemporaryDirectory(dir=settings.temp_dir) as directory:
        with TestClient(app, base_url="https://ytconvert.glitter.kr") as client:
            status = client.get("/api/status").json()
            if status["capabilities"]["conversion_worker"] != "available":
                raise RuntimeError(f"Worker is unavailable: {status['capabilities']['conversion_reason']}")
            analysis = client.post("/api/analyze", json={"url": args.url.strip()})
            if analysis.status_code != 200:
                raise RuntimeError(f"Analysis failed: {analysis.status_code} {analysis.json()}")
            media = analysis.json()
            if media["media_id"] != media_id or not media.get("title"):
                raise RuntimeError("Analysis response is incomplete")
            formats_response = client.get(f"/api/formats/{media_id}")
            if formats_response.status_code != 200:
                raise RuntimeError(f"Format discovery failed: {formats_response.status_code}")
            options = formats_response.json()["options"]
            for output_format in ("mp4", "mp3"):
                choices = [item for item in options if item["format"] == output_format and item["available"]]
                if not choices:
                    raise RuntimeError(f"{output_format.upper()} output is unavailable")
                job = request_job(client, media_id, choices[0])
                events = wait_events(client, job["job_id"])
                if events[-1]["state"] != "completed":
                    raise RuntimeError(f"{output_format.upper()} job ended as {events[-1]['state']}")
                file_info = verify_download(client, settings, job["job_id"], output_format, Path(directory))
                report["jobs"].append({"job_id": job["job_id"], "format": output_format, "states": [event["state"] for event in events], **file_info})
            cancelled_job = request_job(client, media_id, next(item for item in options if item["format"] == "mp4" and item["available"]))
            cancelled = client.post(f"/api/jobs/{cancelled_job['job_id']}/cancel")
            report["cancel_state"] = cancelled.json().get("state")
            if cancelled.status_code != 200 or report["cancel_state"] != "cancelled":
                raise RuntimeError(f"Cancellation did not complete: {report['cancel_state']}")
            report["error_checks"]["invalid_url"] = client.post("/api/analyze", json={"url": "http://127.0.0.1/private"}).status_code
            report["error_checks"]["unknown_job"] = client.get(f"/api/jobs/{uuid4()}").status_code
            report["error_checks"]["cancelled_download"] = client.get(f"/api/download/{cancelled_job['job_id']}").status_code
            if report["error_checks"] != {"invalid_url": 422, "unknown_job": 404, "cancelled_download": 409}:
                raise RuntimeError("Error response checks failed")
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
