import ipaddress
import json
import logging
import re
from pathlib import Path
from threading import Lock
from urllib.parse import unquote


SENSITIVE_SUFFIXES = {".backup", ".bak", ".bk", ".db", ".key", ".kdbx", ".log", ".old", ".orig", ".p12", ".pem", ".pfx", ".py", ".pyc", ".pyo", ".save", ".sqlite", ".sql", ".swp", ".tmp"}
SENSITIVE_SEGMENTS = {".git", ".gitattributes", ".gitignore", ".hg", ".svn", ".ssh", ".aws", ".vscode", "app", "data", "venv", "tests", "__pycache__", "agents.md", "dockerfile", "pyproject.toml", "requirements.txt"}
VERSIONED_ASSET = re.compile(r"\.[a-f0-9]{8,}\.(?:css|js|ico|png|jpg|jpeg|svg|webp)\Z")
CSP = (
    "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'; "
    "script-src 'self'; script-src-attr 'none'; style-src 'self'; style-src-attr 'none'; "
    "img-src 'self' data: https://i.ytimg.com https://img.youtube.com https://yt3.ggpht.com; "
    "font-src 'self' data:; connect-src 'self'; media-src 'self'; frame-src 'none'; "
    "worker-src 'none'; manifest-src 'self'"
)


def sensitive_path(path: str) -> bool:
    decoded = path
    for _ in range(3):
        decoded = unquote(decoded)
    segments = [segment.lower() for segment in decoded.replace("\\", "/").split("/") if segment]
    for segment in segments:
        if segment in SENSITIVE_SEGMENTS or segment.startswith((".env", "config.", "settings.", "secrets.", "credentials.")) or segment in {".ds_store", ".htaccess", ".htpasswd"}:
            return True
        if segment.endswith("~") or any(segment.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
            return True
    return False


def cache_policy(path: str, status_code: int) -> str:
    if status_code >= 400 or path.startswith("/api/") or path in {"/openapi.json", "/docs", "/redoc"}:
        return "no-store"
    if path.startswith(("/css/", "/js/", "/assets/")):
        return "public, max-age=31536000, immutable" if VERSIONED_ASSET.search(path) else "public, max-age=300"
    return "no-store"


class IPBlocklist:
    def __init__(self, path: Path, logger: logging.Logger):
        self.path = path
        self.logger = logger
        self._lock = Lock()
        self._signature = None
        self._failed_signature = None
        self._networks = ()
        self._invalid = False

    def _refresh(self) -> None:
        try:
            if self.path.is_symlink():
                raise ValueError("Blocklist path is a symbolic link")
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signature == self._signature or signature == self._failed_signature:
                return
            if stat.st_size > 1048576:
                raise ValueError("Blocklist is too large")
            content = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(content, list) or len(content) > 10000 or any(not isinstance(item, str) for item in content):
                raise ValueError("Blocklist must be a list of IP addresses or CIDRs")
            networks = tuple(ipaddress.ip_network(item.strip(), strict=False) for item in content)
            self._networks = networks
            self._signature = signature
            self._failed_signature = None
            self._invalid = False
            self.logger.info("IP blocklist loaded", extra={"entries": len(networks)})
        except FileNotFoundError:
            if self._signature is not None:
                self.logger.warning("IP blocklist is unavailable; retaining the last valid list")
                self._signature = None
            return
        except (OSError, ValueError, UnicodeError, TypeError) as exc:
            failed = locals().get("signature", "invalid-path")
            if failed != self._failed_signature:
                self.logger.warning("IP blocklist reload failed", extra={"reason": type(exc).__name__})
                self._failed_signature = failed
            if self._signature is None:
                self._invalid = True

    def blocks(self, address: str) -> bool:
        with self._lock:
            self._refresh()
            if self._invalid:
                return True
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                return False
            return any(ip in network for network in self._networks)
