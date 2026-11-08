
from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import Any, MutableMapping

Network = IPv4Network | IPv6Network

PROJECT_ROOT: Path
DATA_ROOT: Path
CONFIG_ROOT: Path
PUBLIC_ROOT: Path

YGK_URL: str
YGK_ENV: str

YGK_SEC_TXT: str
YGK_TXT: str
YGK_XML: str

BLOCK_IP: str

TRUSTED_PROXY_NETS: list[Network]
INTERNAL_IP_RANGES: list[Network]
PRIVATE_OR_LOCAL_NETS: list[Network]

TRUSTED_BOTS: list[str]

tz_seoul: Any
now: datetime

def get_plugin_state_change_time() -> str: ...

class Settings:
    APP_IS_DEBUG: bool

settings: Settings
cache_plugin_state: MutableMapping[str, Any]

CFG_VDI: str
DAT_VDI: str
STT_VDI: str
CFG_VRO: str

FAV_VRO: str
SVG_VRO: str

