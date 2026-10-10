#!/usr/bin/env bash
# Creates .env from .env.example, replacing every __GENERATE__ placeholder with
# a fresh random secret and expanding ${VAR} references. Safe to re-run: an
# existing .env is never overwritten unless --force is given.
set -euo pipefail
umask 077  # .env must never be world-readable, not even briefly

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE="$ROOT/.env.example"
TARGET="$ROOT/.env"

if [[ -f "$TARGET" && "${1:-}" != "--force" ]]; then
  echo ".env already exists (use --force to regenerate secrets)."
  exit 0
fi

python3 - "$EXAMPLE" "$TARGET" <<'PY'
import re, secrets, string, sys
src, dst = sys.argv[1], sys.argv[2]
alphabet = string.ascii_letters + string.digits
values = {}
lines_out = []
for raw in open(src, encoding="utf-8").read().splitlines():
    line = raw
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", raw)
    if m:
        key, val = m.group(1), m.group(2)
        if val == "__GENERATE__":
            length = 20 if key == "SEED_PASSWORD" else 40
            val = "".join(secrets.choice(alphabet) for _ in range(length))
        val = re.sub(r"\$\{([A-Z0-9_]+)\}", lambda mm: values.get(mm.group(1), ""), val)
        values[key] = val.strip('"')
        line = f"{key}={val}"
    lines_out.append(line)
open(dst, "w", encoding="utf-8").write("\n".join(lines_out) + "\n")
PY
chmod 600 "$TARGET"
echo "Created $TARGET with freshly generated secrets."
