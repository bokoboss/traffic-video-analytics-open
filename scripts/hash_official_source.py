"""Print the SHA-256 digest of an official source file."""

from hashlib import sha256
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("path", type=Path)
args = parser.parse_args()

if not args.path.is_file():
    raise SystemExit(f"File not found: {args.path}")

digest = sha256()
with args.path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)

print(digest.hexdigest())
