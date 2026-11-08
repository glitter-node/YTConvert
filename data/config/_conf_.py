
import os
from datetime import datetime
from ipaddress import ip_network
from pathlib import Path

import pytz

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"
CONFIG_ROOT = DATA_ROOT / "config"
PUBLIC_ROOT = PROJECT_ROOT / "frontend"

YGK_URL = os.getenv("YGK_URL", "http://localhost:8010")
YGK_ENV = os.getenv(
    "YGK_ENV",
    str(CONFIG_ROOT / "env" / "ytconvert" / ".env"),
)

YGK_SEC_TXT = os.getenv(
    "YGK_SEC_TXT",
    str(PUBLIC_ROOT / "security.txt"),
)
YGK_TXT = os.getenv(
    "YGK_TXT",
    str(PUBLIC_ROOT / "robots.txt"),
)
YGK_XML = os.getenv(
    "YGK_XML",
    str(PUBLIC_ROOT / "sitemap.xml"),
)

BLOCK_IP = os.getenv(
    "BLOCK_IP",
    str(CONFIG_ROOT / "blocked_ips.json"),
)

TRUSTED_PROXY_NETS = [
    ip_network("127.0.0.1/32"),
    ip_network("::1/128"),
]

INTERNAL_IP_RANGES = [
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
]

PRIVATE_OR_LOCAL_NETS = [
    ip_network("0.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("127.0.0.0/8"),
    ip_network("169.254.0.0/16"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("224.0.0.0/4"),
    ip_network("240.0.0.0/4"),
    ip_network("::/128"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
    ip_network("ff00::/8"),
]

TRUSTED_BOTS = [
    "Googlebot",
    "Bingbot",
    "Daumoa",
    "DuckDuckBot",
    "Mediapartners-Google",
    "Naverbot",
    "OAI-SearchBot",
    "YandexBot",
    "Yeti",
]

tz_seoul = pytz.timezone("Asia/Seoul")
now = datetime.now(tz_seoul)


def get_plugin_state_change_time():
    return datetime.now(tz_seoul).isoformat()


class Settings:
    APP_IS_DEBUG = os.getenv("APP_ENV", "production") == "development"


settings = Settings()
cache_plugin_state = {}

CFG_VDI = str(CONFIG_ROOT)
DAT_VDI = str(DATA_ROOT)
STT_VDI = str(PUBLIC_ROOT)
CFG_VRO = str(Path(__file__).resolve())

FAV_VRO = os.getenv(
    "FAV_VRO",
    str(PUBLIC_ROOT / "favicon.ico"),
)
SVG_VRO = os.getenv(
    "SVG_VRO",
    str(PUBLIC_ROOT / "favicon.svg"),
)

