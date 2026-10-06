"""Shared privacy policy for the portable builder and ZIP verifier."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Iterable, Pattern


PRIVATE_MARKERS = ("refs" + "/codex", "refs" + "\\codex")
_STATIC_PRIVATE_MARKERS = frozenset(marker.casefold() for marker in PRIVATE_MARKERS)
TEXT_SUFFIXES = {
    ".bat", ".cmd", ".css", ".csv", ".html", ".ini", ".js", ".json", ".md",
    ".py", ".pyi", ".rst", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
BINARY_SUFFIXES = {".bin", ".dll", ".dylib", ".engine", ".exe", ".onnx", ".pyd", ".pt", ".so"}
HIGH_CONFIDENCE_PATTERNS = (
    ("private_key_header", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("sk_live", re.compile(rb"\bsk-live-[A-Za-z0-9]{16,}\b")),
    ("slack_xoxb", re.compile(rb"\bxoxb-[0-9A-Za-z-]{16,}\b")),
    ("github_ghp", re.compile(rb"\bghp_[A-Za-z0-9]{36}\b")),
    ("github_pat", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
)
SECRET_PATTERNS = tuple(pattern for _, pattern in HIGH_CONFIDENCE_PATTERNS)
GENERIC_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*[A-Za-z0-9_-]{16,}"),
)
_LOCAL_PROVENANCE_PATH_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]"),
    re.compile(r"(?<!:)(?:\\\\[^\\/\s]+[\\/][^\\/\s]+|//[^/\s]+/[^/\s]+)"),
    re.compile(r"(?i)(?<![A-Za-z0-9_:/])/(?:home|users|private|tmp)(?:/|$)"),
)
_TEXT_PROBE_LIMIT = 1024 * 1024


def _encoded_forms(value: str) -> tuple[bytes, bytes]:
    folded = value.casefold()
    return folded.encode("utf-8"), folded.encode("utf-16le")


def _private_path_pattern() -> re.Pattern[str]:
    current_user_names = {
        value.strip()
        for value in (os.environ.get("USERNAME"), os.environ.get("USER"))
        if value and value.strip()
    }
    current_user_names.update(
        Path(value).name
        for value in (os.environ.get("USERPROFILE"), os.environ.get("HOME"))
        if value
    )
    if not current_user_names:
        return re.compile(r"(?!)")
    names = "|".join(re.escape(value) for value in sorted(current_user_names))
    return re.compile(
        rf"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/](?:{names})[\\/]|/(?:Users|home)/(?:{names})/)"
    )


def current_private_markers() -> list[str]:
    """Return identity strings for diagnostics; callers must not globally ban them."""

    values = {
        value.strip()
        for value in (os.environ.get("USERNAME"), os.environ.get("USER"))
        if value and value.strip()
    }
    values.update(
        Path(value).name
        for value in (os.environ.get("USERPROFILE"), os.environ.get("HOME"))
        if value
    )
    return sorted(values)


def current_private_roots() -> list[str]:
    """Return exact current profile roots, including slash aliases."""

    values: set[str] = set()
    for value in (os.environ.get("USERPROFILE"), os.environ.get("HOME")):
        if not value or not value.strip():
            continue
        root = value.strip()
        values.update({root, root.replace("\\", "/"), root.replace("/", "\\")})
    return sorted(values)


def packaged_provenance_absolute_path_violations(value: object) -> list[str]:
    """Find local absolute filesystem paths in parsed package provenance values."""
    violations: list[str] = []

    def is_local_absolute_path(text: str) -> bool:
        return any(pattern.search(text) for pattern in _LOCAL_PROVENANCE_PATH_PATTERNS)

    def visit(item: object, field_path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                segment = (
                    key_text
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key_text)
                    else "<key>"
                )
                if is_local_absolute_path(key_text):
                    violations.append(
                        f"packaged_provenance_absolute_path:{field_path or '<root>'}.<key>"
                    )
                else:
                    visit(child, f"{field_path}.{segment}" if field_path else segment)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{field_path}[{index}]")
        elif isinstance(item, str) and is_local_absolute_path(item):
            violations.append(
                f"packaged_provenance_absolute_path:{field_path or '<root>'}"
            )

    visit(value, "")
    return list(dict.fromkeys(violations))


def payload_kind(path: str, payload: bytes) -> str:
    """Classify payloads without treating arbitrary native bytes as text."""

    suffix = Path(path).suffix.casefold()
    if suffix in BINARY_SUFFIXES:
        return "binary"
    probe = payload[:_TEXT_PROBE_LIMIT]
    if b"\x00" not in probe:
        try:
            probe.decode("utf-8")
            return "text_like"
        except UnicodeDecodeError:
            pass
    if suffix in TEXT_SUFFIXES and len(probe) % 2 == 0:
        try:
            text = probe.decode("utf-16le")
        except UnicodeDecodeError:
            pass
        else:
            printable = sum(char.isprintable() or char in "\r\n\t" for char in text)
            if text and printable / len(text) >= 0.85:
                return "text_like"
    return "binary"


def payload_scope(path: str) -> str:
    """Return provenance class used by low-confidence credential heuristics."""

    normalized = path.replace("\\", "/").casefold()
    name = Path(path).name.casefold()
    if normalized.startswith("app/") or normalized.startswith("frontend/dist/"):
        return "tva_owned"
    if name in {
        "native_dll_closure.json",
        "package_manifest.json",
        "package_provenance.json",
        "portable_runtime_canonical.json",
        "third_party_licenses.json",
    } or normalized.endswith(".sha256"):
        return "tva_generated"
    return "immutable_third_party"


def _contains_encoded(payload: bytes, value: str, *, boundary: bool = False) -> bool:
    lowered = payload.lower()
    for encoded in _encoded_forms(value):
        start = lowered.find(encoded)
        while start >= 0:
            end = start + len(encoded)
            if not boundary or end == len(lowered) or lowered[end] not in b"abcdefghijklmnopqrstuvwxyz0123456789_.-":
                return True
            start = lowered.find(encoded, start + 1)
    return False


def _text_forms(payload: bytes, kind: str) -> tuple[str, ...]:
    if kind != "text_like":
        return ()
    forms: list[str] = []
    try:
        forms.append(payload.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    if b"\x00" in payload:
        try:
            forms.append(payload.decode("utf-16le"))
        except UnicodeDecodeError:
            pass
    return tuple(forms)


def _safe_match(value: str | bytes, kind: str) -> dict[str, object]:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return {
        "kind": kind,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "length": len(value),
        "context": "<redacted>",
    }


def _isolated_binary_marker(payload: bytes, start: int, end: int) -> bool:
    def printable(value: int) -> bool:
        return 32 <= value <= 126 or value in {9, 10, 13}

    before = payload[start - 1] if start else None
    after = payload[end] if end < len(payload) else None
    return (before is None or not printable(before)) and (after is None or not printable(after))


def payload_hits(
    payload: bytes,
    *,
    private_markers: Iterable[str],
    private_roots: Iterable[str] = (),
    current_roots: Iterable[str] = (),
    private_emails: Iterable[str] = (),
    private_path_pattern: Pattern[str] | None = None,
    secret_patterns: Iterable[re.Pattern[bytes]] = SECRET_PATTERNS,
    path: str = "",
) -> list[dict[str, object]]:
    """Return hard hits and non-blocking notices under the shared policy."""

    kind = payload_kind(path, payload)
    scope = payload_scope(path)
    text_forms = _text_forms(payload, kind)
    hits: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()

    def add(
        classification: str,
        policy_class: str,
        *,
        severity: str = "hard",
        marker_key: str = "",
        match: dict[str, object] | None = None,
    ) -> None:
        key = (classification, policy_class, severity, marker_key.casefold(), str(match))
        if key in seen:
            return
        seen.add(key)
        item: dict[str, object] = {
            "classification": classification,
            "policy_class": policy_class,
            "severity": severity,
        }
        if match is not None:
            item["match"] = match
        hits.append(item)

    path_matched = any(
        private_path_pattern is not None and private_path_pattern.search(text) for text in text_forms
    )
    if path_matched:
        add("private_path", "ACTUAL_PRIVATE_PATH")

    for marker in private_markers:
        if _contains_encoded(payload, marker, boundary=False):
            policy_class = (
                "STATIC_PRIVATE_MARKER"
                if marker.casefold() in _STATIC_PRIVATE_MARKERS
                else "EXPLICIT_FORBIDDEN_MARKER"
            )
            add("private_marker", policy_class, marker_key=marker)

    for root in private_roots:
        if _contains_encoded(payload, root, boundary=True):
            add("private_marker", "EXPLICIT_FORBIDDEN_ROOT", marker_key=root.replace("\\", "/"))

    for root in current_roots:
        if not path_matched and _contains_encoded(payload, root, boundary=True):
            add("private_marker", "CURRENT_PRIVATE_ROOT", marker_key=root.replace("\\", "/"))

    for email in private_emails:
        if _contains_encoded(payload, email, boundary=True):
            add("private_email", "EXPLICIT_FORBIDDEN_EMAIL", marker_key=email)

    matched_high_confidence: set[tuple[str, str]] = set()
    configured_patterns = tuple(secret_patterns)
    patterns = tuple(
        (
            next(
                (name for name, known in HIGH_CONFIDENCE_PATTERNS if known.pattern == pattern.pattern),
                f"custom_signature_{index}",
            ),
            pattern,
        )
        for index, pattern in enumerate(configured_patterns)
    )
    for name, pattern in patterns:
        for match in pattern.finditer(payload):
            safe_match = _safe_match(match.group(0), name)
            isolated_vendor_string = (
                name == "private_key_header"
                and kind == "binary"
                and scope == "immutable_third_party"
                and _isolated_binary_marker(payload, match.start(), match.end())
            )
            severity = "notice" if isolated_vendor_string else "hard"
            policy_class = "THIRD_PARTY_TEST_STRING" if isolated_vendor_string else "HIGH_CONFIDENCE_CREDENTIAL"
            add("credential_marker", policy_class, severity=severity, match=safe_match)
            matched_high_confidence.add((name, str(safe_match["sha256"])))

    for text in text_forms:
        for name, pattern in patterns:
            text_pattern = re.compile(pattern.pattern.decode("ascii"), pattern.flags)
            for match in text_pattern.finditer(text):
                safe_match = _safe_match(match.group(0), name)
                add("credential_marker", "HIGH_CONFIDENCE_CREDENTIAL", match=safe_match)
                matched_high_confidence.add((name, str(safe_match["sha256"])))

    if scope == "immutable_third_party":
        for prefix in ("ghp_", "github_pat_"):
            if prefix.encode() in payload.lower() and not any(
                name.startswith("github_") for name, _ in matched_high_confidence
            ):
                add(
                    "credential_marker",
                    "LOW_CONFIDENCE_TOKEN_PREFIX",
                    severity="notice",
                    match=_safe_match(prefix, prefix.rstrip("_")),
                )

    if kind == "text_like":
        for text in text_forms:
            for pattern in GENERIC_SECRET_PATTERNS:
                for match in pattern.finditer(text):
                    safe_match = _safe_match(match.group(0), "generic_credential_heuristic")
                    severity = "hard" if scope in {"tva_owned", "tva_generated"} else "notice"
                    add("credential_marker", "GENERIC_CREDENTIAL_HEURISTIC", severity=severity, match=safe_match)

    return hits


def scan_payloads(
    payloads: Iterable[tuple[str, bytes]],
    *,
    private_markers: Iterable[str],
    private_roots: Iterable[str] = (),
    current_roots: Iterable[str] = (),
    private_emails: Iterable[str] = (),
    private_path_pattern: Pattern[str] | None = None,
    secret_patterns: Iterable[re.Pattern[bytes]] = SECRET_PATTERNS,
) -> tuple[list[str], list[dict[str, object]], list[dict[str, object]]]:
    violations: list[str] = []
    hard_hits: list[dict[str, object]] = []
    notices: list[dict[str, object]] = []
    for name, payload in payloads:
        for hit in payload_hits(
            payload,
            private_markers=private_markers,
            private_roots=private_roots,
            current_roots=current_roots,
            private_emails=private_emails,
            private_path_pattern=private_path_pattern,
            secret_patterns=secret_patterns,
            path=name,
        ):
            record = {"path": name, **hit}
            if hit["severity"] == "hard":
                hard_hits.append(record)
                classification = str(hit["classification"])
                violation_name = "absolute_path" if classification == "private_path" else classification
                violations.append(f"{violation_name}:{name}")
            else:
                notices.append(record)
    return list(dict.fromkeys(violations)), hard_hits, notices
