"""
Supported ASR/translation languages.

Usage:
    from languages import LANGUAGES, LANG_LABELS, LANG_NAME, lang_code_to_label, lang_label_to_code, parse_direction
"""

# List of (code, display_label) pairs — order determines dropdown order.
LANGUAGES: list[tuple[str, str]] = [
    ("zh",  "zh (中文)"),
    ("en",  "en (English)"),
    ("yue", "yue (廣東話)"),
    ("ja",  "ja (日本語)"),
    ("ko",  "ko (한국어)"),
    ("ar",  "ar (Arabic)"),
    ("de",  "de (Deutsch)"),
    ("fr",  "fr (Français)"),
    ("es",  "es (Español)"),
    ("pt",  "pt (Português)"),
    ("id",  "id (Indonesia)"),
    ("it",  "it (Italiano)"),
    ("ru",  "ru (Русский)"),
    ("th",  "th (ไทย)"),
    ("vi",  "vi (Tiếng Việt)"),
    ("tr",  "tr (Türkçe)"),
    ("hi",  "hi (हिन्दी)"),
    ("ms",  "ms (Malay)"),
    ("nl",  "nl (Nederlands)"),
    ("sv",  "sv (Svenska)"),
    ("da",  "da (Dansk)"),
    ("fi",  "fi (Suomi)"),
    ("pl",  "pl (Polski)"),
    ("cs",  "cs (Čeština)"),
    ("fil", "fil (Filipino)"),
    ("fa",  "fa (فارسی)"),
    ("el",  "el (Ελληνικά)"),
    ("hu",  "hu (Magyar)"),
    ("mk",  "mk (Македонски)"),
    ("ro",  "ro (Română)"),
]

# Flat list of display labels (for dropdowns).
LANG_LABELS: list[str] = [label for _, label in LANGUAGES]

# code → human name (text inside parentheses)
LANG_NAME: dict[str, str] = {
    code: label.split("(", 1)[1].rstrip(")")
    for code, label in LANGUAGES
}


def lang_code_to_label(code: str) -> str:
    """'en' → 'en (English)'"""
    for c, label in LANGUAGES:
        if c == code:
            return label
    return code


def lang_label_to_code(label: str) -> str:
    """'en (English)' → 'en'"""
    for c, lbl in LANGUAGES:
        if lbl == label:
            return c
    return label.split(" ")[0]  # fallback: first token is the code


def parse_direction(direction: str) -> tuple[str, str]:
    """'en→zh' → ('en', 'zh').  Falls back to ('en', 'zh') on error."""
    parts = direction.split("→", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "en", "zh"


def swap_direction(direction: str) -> str:
    """'en→zh' → 'zh→en'"""
    src, tgt = parse_direction(direction)
    return f"{tgt}→{src}"
