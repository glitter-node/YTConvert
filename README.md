# YTConvert

YTConvert is a self-hosted software package for YouTube media metadata analysis, source format inspection, and optional media conversion. Each operator installs and manages their own instance; this project does not operate a public conversion service for them.

The initial MVP provides health and status endpoints, YouTube metadata analysis, source format discovery, a persistent conversion job API, session-based job access, Server-Sent Events (SSE), and a separate conversion worker.

Media downloading and conversion are disabled by default by `MEDIA_WORKER_ENABLED=false` in `app/core/config.py` and `.env.example`. Metadata analysis remains available and makes outbound yt-dlp requests without downloading a media file. The job creation API returns HTTP 503 after its client-network check passes until the operator enables the worker and runtime prerequisites are satisfied.

YTConvert is an independent project and is not affiliated with, endorsed by, or authorized by YouTube or Google.

## License and third-party software

Original YTConvert code owned by Glitter.kr is distributed under the [MIT License](LICENSE), with `Copyright (c) 2026 Glitter.kr`. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the inspected dependencies, their separate licenses, and unresolved provenance checks. The MIT notice does not replace third-party licenses or cover downloaded media, operator-supplied FFmpeg binaries, externally supplied assets, or code that Glitter.kr does not own or have authority to license.

MIT permits use, modification, distribution, sublicensing, and sale subject to its notice condition. The application's default access controls and the operator guidance below describe this installation's behavior and risks; they do not add noncommercial or private-use restrictions to the MIT license. Public distribution of source code does not mean Glitter.kr runs a public conversion platform. Each operator installs and manages their own service and access controls.

## Rights, platform terms, and operator review

Software availability and technical capability do not establish legal permission for a particular use. Each independent operator must decide which users and networks may reach their instance and verify the rights and obligations relevant to the media and jurisdiction involved.

YouTube's Terms of Service restrict accessing, downloading, reproducing, modifying, and distributing content except under stated permissions or applicable law. They also address automated access and interference with service features, including copying restrictions. The terms and their application should be reviewed for each deployment and use case.

Operators and users must review applicable copyright and licensing restrictions, platform terms, privacy obligations for metadata and job records, and any rules concerning technological protection measures. Whether a particular yt-dlp operation implicates such a measure or an exception requires fact-specific, jurisdiction-specific review; this project does not decide that question.

Ownership of a video or permission from its copyright holder does not automatically establish compliance with YouTube's platform terms. Public availability of a video does not imply authorization to download, convert, or redistribute it.

YTConvert does not grant permission to download, convert, reproduce, redistribute, or otherwise use third-party content.

Self-hosting, restricting access by network or session, or operating without commercial intent does not automatically eliminate copyright or contractual obligations.

The media conversion worker is disabled by default. Operators should keep it disabled unless they have independently reviewed the applicable legal, contractual, security, and operational requirements.

Distributing the source code is distinct from operating a service. Source availability, yt-dlp support, a successful metadata lookup, an available output format, or a checked rights box does not authorize downloading or conversion. Operators independently configure access controls, retention, and deployment security.

Operators considering internet-reachable conversion access should obtain appropriate legal guidance where necessary.

References: [YouTube Terms of Service](https://www.youtube.com/t/terms), [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp#readme), and, as one jurisdiction-specific example, the [U.S. Copyright Office's Section 1201 overview](https://www.copyright.gov/1201/). These references are starting points for human review, not a determination of compliance.

## Local setup

Use Python 3.11 or a compatible Python version supported by the project dependencies.

From the project root, create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

If the Python installation does not provide the standard `venv` module, use `virtualenv` instead:

```bash
python3 -m virtualenv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The application reads `data/config/env/ytconvert/.env`. Real secrets must stay out of Git. Environment variables take precedence. Refer to `.env.example` for configuration examples; its public URL and allowed hosts describe one installation and must be reviewed for each operator.

Set `INTERNAL_IP_RANGES` to comma-separated client IP addresses or CIDRs before enabling job creation, for example `127.0.0.1/32,192.168.0.0/16,10.0.0.0/8,::1/128`. An absent or empty value denies all conversion job creation; an invalid entry prevents application startup. This setting does not restrict metadata analysis or access to existing jobs. The `/api/status` worker capability reports readiness, not whether the requesting client is allowed to create a job.

No database, Redis, or conversion worker is required for metadata analysis.

## Analyze a video

`POST /api/analyze` accepts one HTTPS YouTube watch, short link, Shorts, or live URL with an 11-character video ID.

Share parameters such as `t`, `si`, `feature`, and `list` are ignored after a single video ID is validated.

The server passes only the canonical `https://www.youtube.com/watch?v=ID` URL to yt-dlp, so a playlist is never analyzed as a whole.

`GET /api/formats/ID` returns the cached source streams and output choices.

Arbitrary URLs, credentials, and custom ports are rejected.

Metadata extraction does not download media files.

Analysis results are cached in memory for 10 minutes, up to 100 videos. Simultaneous requests for the same ID share one extraction.

The supported output labels are MP4, WebM, MP3, M4A, and Opus.

Source streams remain separate from output choices. Storyboards and streams without audio or video codecs are excluded.

Video quality choices follow observed source heights, including heights such as 240p or 1440p when present.

Audio conversion targets are not claimed source streams.

Each output option reports `available` and `unavailable_reason` based on FFmpeg encoder support, independently of worker activation.

Users can inspect and select supported formats while the worker is disabled. Job creation remains blocked until the worker is ready.

The outbound yt-dlp guard requires HTTPS and an approved YouTube service host.

It does not reject an approved hostname because DNS resolves to a private or loopback address. It does not provide network isolation or fully validate library-managed redirects.

The user-supplied URL must still normalize to one canonical YouTube video ID.

Metadata analysis is not equivalent to permission to download or convert the analyzed content.

## Conversion jobs

`POST /api/jobs` accepts a server-verified YouTube media ID and one advertised output option.

It returns HTTP 201 with a random UUID when processing is enabled, or HTTP 503 with a readiness reason otherwise.

For a parsed `POST /api/jobs` request, the API checks the client IP against `INTERNAL_IP_RANGES` before media analysis or job creation. A client outside the configured ranges receives HTTP 403 (`network_restricted`) with `Conversion is restricted to authorized networks`; malformed JSON can receive 422 during body parsing before this check. The check uses Uvicorn's `request.client`, so the reverse proxy must overwrite client supplied forwarding headers and pass the real client address. Direct access to Uvicorn must be limited to the trusted loopback proxy; otherwise an untrusted caller could impersonate a trusted proxy. This network restriction does not change the other API endpoints.

The following endpoints manage conversion jobs:

- `GET /api/jobs`
- `GET /api/jobs/{id}`
- `GET /api/jobs/{id}/events`
- `POST /api/jobs/{id}/cancel`
- `GET /api/download/{id}`

`DELETE /api/jobs/{id}` remains as a compatibility alias for cancellation.

A random same-site, HTTP-only session cookie scopes access to jobs and downloads created in that session. It is not an account system or permission to create jobs; the client-network check applies separately to `POST /api/jobs`. Knowing a job ID alone is insufficient.

Jobs and their state are stored as JSON under `data/jobs`. Media files are stored under per-job directories in `data/temp` and `data/output`.

### Job states and persistence

States are `queued`, `downloading`, `processing`, `completed`, `failed`, and `cancelled`.

Each state change is saved to `data/jobs/{job_id}.json`.

On restart, unfinished records become failed with `Interrupted by service restart`, while terminal records are restored unchanged.

`GET /api/jobs` lists the most recent 20 unexpired records belonging to the request's `ytconvert_session` cookie, including completed, failed, and cancelled jobs.

A request without that cookie returns `{"jobs":[]}` even when records exist on disk.

The manual E2E client has its own cookie jar and cannot list browser jobs, or vice versa.

Losing the cookie prevents access to existing records because only its hash is stored.

`MEDIA_SESSION_TTL_HOURS` (default 168) limits API access from job creation. It does not delete JSON records.

`MEDIA_OUTPUT_RETENTION_HOURS` (default 24) removes expired files under `data/output`, not job records.

Cleanup runs on startup and hourly when the worker is enabled.

A retained completed record may therefore refer to a file that is no longer downloadable.

For conversion jobs, the API process queues and monitors work. Download and FFmpeg processing run in a separate child process that inherits the root identity of Uvicorn. The API process itself performs metadata analysis through yt-dlp.

### Download filenames

New job records store the server-analyzed video title as `source_title`.

Internal files remain `data/output/{job_id}/result.EXT`.

The download response suggests a sanitized title-based filename through `Content-Disposition`, such as `Beautiful Music - Live Performance.mp4`.

Records created before `source_title` was added fall back to the video ID.

The browser uses the direct download link and honors the response filename.

The matching session cookie authorizes access to that session's completed downloads.

### Processing and progress

Identical active jobs for the same session and output are rejected.

SSE sends the current state immediately on connection and reconnection, followed by progress changes and a terminal event.

The worker verifies the downloaded file with ffprobe, prevents video upscaling, and encodes audio when a specific output bitrate is selected.

FFmpeg and ffprobe read local files only.

A temporary activation can be tested with `tests/manual_e2e.py` after the operator approves the test and supplies a short video they are authorized to download and convert.

The script retains job records and output files under `data/`.

### Failure diagnostics

New failures write a bounded, redacted JSON log entry named `Media job failed` with the job ID, stage, tool, exit code, timeout flag, exception type, and diagnostic text.

yt-dlp download and source merge, ffprobe inspection, FFmpeg conversion, and output validation have separate safe job error messages.

The job JSON and SSE retain only the safe message.

FFmpeg stderr is captured in an unlinked temporary file under the job's temp directory, and only its last 4 KiB are inspected.

The log diagnostic is limited to 1,200 characters after URL, credential, and path redaction.

Existing generic failure records do not contain enough information to recover their original cause.

Download progress contributes 0–50% and conversion contributes 50–99.5% to overall progress.

Only validated completion sets 100%. A failed job keeps its last observed progress.

## Worker activation and runtime security

The worker remains disabled until the configuration flag and runtime readiness conditions are satisfied:

- `MEDIA_WORKER_ENABLED=true`
- A root API process
- Readable media tools
- At least one supported output encoder set
- Required project directory access

The application cannot verify legal permission or an operator's security review. Enabling the flag is the operator's decision, not evidence those reviews are complete.

`MEDIA_WORKER_NETWORK_ISOLATED` is no longer used.

No network isolation is implemented or verified by this application.

HTTP client IP blocking protects inbound requests only and does not restrict yt-dlp or FFmpeg egress.

Running yt-dlp and FFmpeg as root substantially increases the impact of a media parser exploit or SSRF.

File path checks and resource limits do not confine a compromised root process.

The current worker architecture inherits the API process identity. Worker activation should not be treated as production-safe solely because the readiness checks pass.

### FFmpeg encoder requirements

| Output | Required encoders |
|---|---|
| MP4 | `libx264`, `aac` |
| WebM | `libvpx-vp9`, `libopus` |
| MP3 | `libmp3lame` |
| M4A | `aac` |
| Opus | `libopus` |

Output availability depends on the FFmpeg encoders installed in the deployment environment. For example, WebM is unavailable without `libvpx-vp9`. Encoder discovery can mark other outputs available while the worker is disabled; actual job creation still requires the worker and an allowed client network.

Job creation remains disabled by default.

The worker's technical readiness does not establish legal authorization to download or convert any particular media.

## HTTP security

`TrustedHostMiddleware` and request validation accept `APP_ALLOWED_HOSTS`.

The configured defaults are `ytconvert.glitter.kr`, `127.0.0.1`, and `localhost`. Deployments should configure `APP_ALLOWED_HOSTS` for their own hostnames.

The reverse proxy must preserve the public Host header.

Sensitive paths are hidden with HTTP 404 responses.

`APP_IP_BLOCKLIST_FILE` defaults to `data/config/blocked_ips.json`, an optional JSON array of IPv4 or IPv6 addresses and CIDRs.

Changes are loaded on requests. An invalid update retains the last valid list, and an invalid initial file denies requests.

The API blocklist middleware reads `request.client` and does not parse forwarded IP headers itself. A separate legacy protection path for `robots.txt` and `sitemap.xml` uses the trusted-proxy resolver in `app/core/ip_resolver.py`; that path is not used for API access control.

Uvicorn must trust forwarded headers only from the loopback reverse proxy.

API and error responses use `Cache-Control: no-store`.

Unversioned JavaScript and CSS use a short cache, while filenames containing a content hash can use a long immutable cache.

The CSP permits same-origin JavaScript, CSS, Fetch, SSE, and downloads, plus the allowlisted YouTube thumbnail hosts.

It disallows inline scripts, inline styles, and eval.

Cross-origin thumbnails require `Cross-Origin-Embedder-Policy: unsafe-none`. This is weaker than cross-origin isolation.

OpenAPI routes are disabled by default and can be enabled only in development with `APP_ENABLE_OPENAPI=true`.

## Deployment considerations

YTConvert separates metadata analysis from media processing.

Metadata analysis can operate without enabling the conversion worker. This is the recommended default configuration for deployments that do not require media processing.

Before enabling conversion, operators should independently evaluate:

- Copyright and licensing permissions for the intended content
- YouTube's Terms of Service and applicable contractual restrictions
- Whether the intended deployment and user access model are permitted
- Outbound network restrictions and SSRF exposure
- Media parser and transcoder vulnerabilities
- Process privileges and filesystem permissions
- Resource consumption, storage retention, and abuse prevention

Restricting access to authenticated users is not a substitute for obtaining required permissions or complying with platform terms.

The current implementation does not provide outbound network isolation or an unprivileged conversion worker.

Internet-reachable conversion service operation requires separate legal and operational review and stronger isolation than this project currently implements. Public distribution of the software does not imply that an operator should expose its conversion endpoint to the public.

## Verification

Run the following commands from the project root after installing dependencies:

```bash
.venv/bin/python -m tests.run
.venv/bin/python -m compileall -q app tests
node --check frontend/js/app.js
.venv/bin/python -m pip check
```

These commands do not start the service.

The project includes a direct test runner. See [deployment guidance](docs/deployment.md) for the manual Uvicorn command and service checks.

## Release status

**v0.1.0 — Initial MVP Release**

This initial MVP provides the core media analysis and conversion workflow, including source format discovery, output selection, persistent job management, SSE progress reporting, cancellation, and session-authorized downloads.

Media conversion is disabled by default and requires explicit operator activation.

The project remains in an early development stage. APIs, configuration, security controls, and operational requirements may change before a stable release.

The initial MVP designation does not imply production readiness, platform authorization, or legal clearance for operating a public media conversion service.

## Source release packaging

No source-archive build script or release manifest is present in this workspace, and no existing v0.1.0 artifact was changed. Before creating a new source archive, include root `LICENSE`, `THIRD_PARTY_NOTICES.md`, and the documentation with the original project files; inspect the archive contents to confirm their presence. Exclude the active `.env`, `venv/`, job records, media files, and host-specific secrets. If a release bundles Python wheels, FFmpeg, ffprobe, fonts, images, or other third-party materials, include their own required license texts and notices and review source and build obligations for the exact bundled versions. The root MIT license is not a substitute for those materials.
