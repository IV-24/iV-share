#!/bin/bash
# Creates backend/.env with a generated API_ACCESS_SECRET, safely and
# idempotently.
#
# This exists because the obvious one-liner is subtly wrong:
#
#     cp backend/.env.example backend/.env
#     echo "API_ACCESS_SECRET=$(...)" >> backend/.env
#
# The example file already declares API_ACCESS_SECRET= (empty), so
# appending leaves the key defined twice. Everything resolves a duplicate
# to the last occurrence, so it mostly works — until something reads the
# first one instead, at which point you get a component that thinks no
# secret is configured while its neighbour is happily using one.
#
# Run again any time: an existing secret is kept, not regenerated.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f backend/.env ]; then
    cp backend/.env.example backend/.env
    echo "Created backend/.env from the example."
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" - <<'PYEOF'
import pathlib
import re
import secrets

path = pathlib.Path("backend/.env")
text = path.read_text()

lines = [l for l in text.splitlines() if l.strip().startswith("API_ACCESS_SECRET=")]
existing = next((l.split("=", 1)[1].strip().strip("\"'") for l in reversed(lines) if l.split("=", 1)[1].strip()), "")

if len(lines) > 1:
    # Collapse duplicates down to one, keeping the last value — the same
    # one python-dotenv would have been using all along.
    kept = False
    out = []
    for line in text.splitlines():
        if line.strip().startswith("API_ACCESS_SECRET="):
            if not kept:
                out.append(f"API_ACCESS_SECRET={existing or secrets.token_urlsafe(32)}")
                kept = True
            continue
        out.append(line)
    path.write_text("\n".join(out) + "\n")
    print(f"Collapsed {len(lines)} duplicate API_ACCESS_SECRET lines into one.")
elif existing:
    print("API_ACCESS_SECRET already set; leaving it alone.")
else:
    path.write_text(
        re.sub(
            r"^API_ACCESS_SECRET=.*$",
            "API_ACCESS_SECRET=" + secrets.token_urlsafe(32),
            text,
            count=1,
            flags=re.M,
        )
    )
    print("Generated API_ACCESS_SECRET.")
PYEOF

echo
echo "Next: add a provider key to backend/.env (GROQ_API_KEY, GEMINI_API_KEY, ...)"
echo "Then: ./run.sh"
