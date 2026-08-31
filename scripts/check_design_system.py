"""Validate the seed UX/UI design contract."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

required = [
    ROOT / "PRODUCT.md",
    ROOT / "DESIGN.md",
    ROOT / "design" / "tokens.json",
    ROOT / "design" / "tokens.css",
    ROOT / "docs" / "ux" / "ux_ui_bible.md",
    ROOT / "docs" / "ux" / "screen_inventory.md",
    ROOT / "docs" / "ux" / "design_review_checklist.md",
]

missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
if missing:
    print("Missing design files:")
    for path in missing:
        print(f"- {path}")
    sys.exit(1)

tokens = json.loads((ROOT / "design" / "tokens.json").read_text(encoding="utf-8"))

for key in ("color", "space", "radius", "size", "type", "motion"):
    if key not in tokens:
        raise SystemExit(f"Missing token group: {key}")

product = (ROOT / "PRODUCT.md").read_text(encoding="utf-8")
design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
for reference, document in (("DESIGN.md", product), ("docs/ux/", design)):
    if reference not in document:
        raise SystemExit(f"Public design documentation does not reference {reference}")

print("Design system OK.")
