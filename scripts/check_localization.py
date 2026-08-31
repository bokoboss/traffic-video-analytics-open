from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COPY = ROOT / "apps/frontend/src/copy.ts"
CHECKED_TEXT = [
    COPY,
    ROOT / "apps/frontend/src/App.tsx",
    ROOT / "docs/ux/ux_writing_glossary.md",
]

MOJIBAKE_MARKERS = [
    "\ufffd",
    "เธ",
    "เน€",
    "โ€",
    "ร—",
    "๏ฟฝ",
    "\\xE0",
    "\\u0E",
]

APPROVED_IDENTICAL = {
    "product",
    "phf",
}


def main() -> int:
    errors: list[str] = []
    source = COPY.read_text(encoding="utf-8")
    en = _extract_catalog(source, "en")
    th = _extract_catalog(source, "th")
    allowlist = set(_extract_allowlist(source))

    if set(en) != set(th):
        errors.append(f"catalog keys differ: en-only={sorted(set(en) - set(th))}, th-only={sorted(set(th) - set(en))}")

    for key, value in th.items():
        if not value.strip():
            errors.append(f"th.{key} is empty")
        if key not in APPROVED_IDENTICAL and value == en.get(key):
            errors.append(f"th.{key} is identical to English without approval")
        if key not in APPROVED_IDENTICAL and not _contains_thai(value):
            terms = _remove_allowlisted_terms(value, allowlist)
            if re.search(r"[A-Za-z]{3,}", terms):
                errors.append(f"th.{key} appears untranslated: {value!r}")
        if "???" in value:
            errors.append(f"th.{key} contains question-mark placeholder")

    for path in CHECKED_TEXT:
        text = path.read_text(encoding="utf-8")
        for marker in MOJIBAKE_MARKERS:
            if marker in text:
                errors.append(f"{path.relative_to(ROOT)} contains mojibake marker {marker!r}")

    if errors:
        for error in errors:
            print(f"localization error: {error}")
        return 1
    print("Localization check passed.")
    return 0


def _extract_catalog(source: str, name: str) -> dict[str, str]:
    match = re.search(rf"const {name}[^=]*= \{{(?P<body>.*?)\}} as const;", source, re.S)
    if not match:
        raise SystemExit(f"Could not find catalog {name}")
    body = match.group("body")
    return {key: value for key, value in re.findall(r"^\s*(\w+):\s*\"((?:\\.|[^\"])*)\"", body, re.M)}


def _extract_allowlist(source: str) -> list[str]:
    match = re.search(r"approvedThaiEnglishTerms = \[(?P<body>.*?)\] as const;", source, re.S)
    if not match:
        return []
    return re.findall(r"\"([^\"]+)\"", match.group("body"))


def _contains_thai(value: str) -> bool:
    return any("\u0e00" <= char <= "\u0e7f" for char in value)


def _remove_allowlisted_terms(value: str, allowlist: set[str]) -> str:
    cleaned = value
    for term in sorted(allowlist, key=len, reverse=True):
        cleaned = cleaned.replace(term, "")
    return cleaned


if __name__ == "__main__":
    sys.exit(main())
