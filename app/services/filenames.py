import re
import unicodedata

from app.core.security import video_id_is_valid


MAX_DOWNLOAD_FILENAME_BYTES = 200
OUTPUT_EXTENSIONS = frozenset({"mp4", "webm", "mp3", "m4a", "opus"})
INVALID_CHARACTERS = frozenset('/\\:*?"<>|')
WINDOWS_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10)), *(f"COM{number}" for number in "¹²³"), *(f"LPT{number}" for number in "¹²³")})
BIDI_CONTROLS = frozenset({"\u061c", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\ufeff"})


def _usable_character(character: str) -> bool:
    return character not in INVALID_CHARACTERS and character not in BIDI_CONTROLS and unicodedata.category(character) not in {"Cc", "Cs", "Zl", "Zp"} and not character.isspace() and character != "."


def download_filename(title: str | None, media_id: str | None, output_format: str) -> str:
    if output_format not in OUTPUT_EXTENSIONS:
        raise ValueError("Unsupported output format")
    extension = f".{output_format}"
    source = unicodedata.normalize("NFC", title) if isinstance(title, str) else ""
    if any(_usable_character(character) for character in source):
        stem = "".join("_" if character in INVALID_CHARACTERS or character in BIDI_CONTROLS or unicodedata.category(character) in {"Cc", "Cs", "Zl", "Zp"} else character for character in source)
        stem = re.sub(r"_+", "_", stem)
        stem = re.sub(r"\.{2,}", ".", stem).strip().strip(".").strip()
    else:
        stem = ""
    if not stem:
        stem = media_id if isinstance(media_id, str) and video_id_is_valid(media_id) else "result"
    if stem.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    limit = MAX_DOWNLOAD_FILENAME_BYTES - len(extension.encode("utf-8"))
    truncated = ""
    used = 0
    for character in stem:
        size = len(character.encode("utf-8"))
        if used + size > limit:
            break
        truncated += character
        used += size
    truncated = truncated.strip().strip(".").strip()
    if not truncated:
        truncated = media_id if isinstance(media_id, str) and video_id_is_valid(media_id) else "result"
    return f"{truncated}{extension}"
