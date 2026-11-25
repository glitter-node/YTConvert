"""
data.config._conf_ Docstring
"""
import os
from datetime import datetime
from ipaddress import ip_network

import pytz

CGK_URL = "https://captcha.glitter.kr"
CHK_URL = "https://cheer.glitter.kr"
E44_URL = "https://404.glitter.kr"
DGK_URL = "https://deny.glitter.kr"
GAT_URL = "https://gate.glitter.kr"
GBZ_URL = "https://glitter-bz.glitter.kr"
GIM_URL = "https://glitter-im.glitter.kr"
GKR_URL = "https://glitter.kr"
GMY_URL = "https://glitter.my"
GTW_URL = "https://glitter.tw"
MGK_URL = "https://m.glitter.kr"
NGK_URL = "https://new.glitter.kr"
MSG_URL = "https://msg.glitter.kr"
PGK_URL = "https://policy.glitter.kr"
VGK_URL = "https://vlog.glitter.kr"
VGG_URL = "https://vlog.glitter.kr/gate"
WGK_URL = "https://whitepaper.glitter.kr"
YGK_URL = "https://ytconvert.glitter.kr"

YGK_ENV = "/volume1/glitter/ytconvert/data/config/env/ytconvert/.env"

YGK_SEC_TXT = "/volume1/hwi/ytconvert.glitter.kr_security.txt"
YGK_TXT = "/volume1/hwi/ytconvert.glitter.kr_robots.txt"
YGK_XML = "/volume1/hwi/ytconvert.glitter.kr_sitemap.xml"

BLOCK_IP = "/volume1/hwi/config/blocked.json"

TRUSTED_PROXY_NETS = [
    ip_network("127.0.0.1/32"),
    ip_network("::1/128"),
]

INTERNAL_IP_RANGES = [
    ip_network("115.23.70.228/32"),    
    ip_network("127.0.0.0/8"),
    ip_network("10.11.12.0/24"),    
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
    "Googlebot", "Bingbot", "Daumoa", "DuckDuckBot",
    "Mediapartners-Google", "Naverbot", "OAI-SearchBot", "YandexBot", "Yeti"
]

tz_seoul = pytz.timezone("Asia/Seoul")
now = datetime.now(tz_seoul)

def get_plugin_state_change_time():
    return datetime.now(tz_seoul).isoformat()

class Settings:
    APP_IS_DEBUG = True

settings = Settings()
cache_plugin_state = {}

ck_url = os.getenv("CGK_URL", CGK_URL)
dk_url = os.getenv("DGK_URL", DGK_URL)
gk_url = os.getenv("GKR_URL", GKR_URL)
nk_url = os.getenv("NGK_URL", NGK_URL)
er_url = os.getenv("E44_URL", E44_URL)
gb_url = os.getenv("GBZ_URL", GBZ_URL)
gi_url = os.getenv("GIM_URL", GIM_URL)
gm_url = os.getenv("GMY_URL", GMY_URL)
gt_url = os.getenv("GTW_URL", GTW_URL)
mg_url = os.getenv("MGK_URL", MGK_URL)
dg_url = os.getenv("DGK_URL", DGK_URL)
pg_url = os.getenv("PGK_URL", PGK_URL)
vg_url = os.getenv("VGK_URL", VGK_URL)
vgg_url = os.getenv("VGG_URL", VGG_URL)
ygk_url = os.getenv("YGK_URL", YGK_URL)

gg_tel = os.getenv("GG_TEL", "tel:+8201036125558")
gg_mail = os.getenv("GG_MAIL", "admin@glitter.kr")
gg_mail_to = os.getenv("GG_MAIL_TO", "mailto:admin@glitter.kr")
kakao_js_key = os.getenv("KAKAO_JS_KEY", "KAKAO_JS_KEY")

ABM_ROT = "album"
APP_ROT = "app"
BIK_ROT = "bike"
BLG_ROT = "blog"
CAM_ROT = "cam"
CF_TITLE = "Glitter YTConvert"
CFG_ROT = "config"
CSS_ROT = "css"
DAT_ROT = "data"
FAV_ROT = "favicon"
GLI_ROT = "glitter"
GPX_ROT = "gpx"
HWI_ROT = "hwi"
GIG_ROT = "gimg"
IMG_ROT = "img"
MDA_ROT = "Media"
PLO_ROT = "profiles"
STT_ROT = "static"
TPL_ROT = "templates"
USR_ROT = "user"
VOL_ROT = "volume1"
WEB_ROT = "web"
YGK_ROT = "ytconvert"

CFG_PY_ = "_conf_.py"
FAV_ICO = "favicon.ico"
FAV_SVG = "glitter_circle-512.svg"
CMK_PNG = "glitter_circle.png"
WMK_PNG = "glitter_mark.png"

APP_VDI = f"/{VOL_ROT}/{GLI_ROT}/{YGK_ROT}/{APP_ROT}"
WEB_VDI = f"{APP_VDI}/{WEB_ROT}"
DAT_VDI = f"/{VOL_ROT}/{GLI_ROT}/{YGK_ROT}/{DAT_ROT}"
HWI_VDI = f"/{VOL_ROT}/{HWI_ROT}"

CSS_VDI = f"{HWI_VDI}/{CSS_ROT}"
CFG_VDI = f"{DAT_VDI}/{CFG_ROT}"
FAV_VDI = f"{APP_VDI}/{STT_ROT}/{GIG_ROT}/{FAV_ROT}"
IMG_VDI = f"{HWI_VDI}/{GIG_ROT}"
MDA_VDI = f"/{VOL_ROT}/{MDA_ROT}"
STT_VDI = f"{WEB_VDI}/{STT_ROT}"
TPL_VDI = f"{WEB_VDI}/{TPL_ROT}"
USR_VDI = f"{DAT_VDI}/{USR_ROT}"

CFG_VRO = f"{CFG_VDI}/{CFG_PY_}"
FAV_VRO = f"{HWI_VDI}/{GIG_ROT}/{FAV_ROT}/{FAV_ICO}"
SVG_VRO = f"{HWI_VDI}/{GIG_ROT}/{FAV_ROT}/{FAV_SVG}"
CMK_VRO = f"{HWI_VDI}/{GIG_ROT}/{CMK_PNG}"
WMK_VRO = f"{HWI_VDI}/{GIG_ROT}/{WMK_PNG}"

ALBUM_FS_ROOT = f"/{DAT_ROT}/{ABM_ROT}"

TEMPLATES_CONTEXT = {
    "site_name": "Glitter YTConvert",
    "author": "Glitter Gim",
    "hwi_dir": HWI_ROT,
    "cmk_vro": CMK_VRO,
    "fav_vro": FAV_VRO,
    "wmk_vro": WMK_VRO,
    "er_url": er_url,
    "cfg_vdi": CFG_VDI,
    "css_vdi": CSS_VDI,
    "fav_vdi": FAV_VDI,
    "gb_url": gb_url,    
    "gg_tel"    : gg_tel,    
    "gg_mail_to": gg_mail_to,
    "gg_mail": gg_mail,
    "gk_url": gk_url,
    "pg_url"    : pg_url,    
    "kakao_js_key": kakao_js_key,
    "vg_url"    : vg_url,        
    "vgg_url"   : vgg_url,
    "ygk_url"   : ygk_url,     
}
