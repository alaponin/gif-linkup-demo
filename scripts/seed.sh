#!/usr/bin/env bash
# Seed Progressa demo data for KP2: regenerate the Gambia-grounded CSVs
# (Progressa-named; the source country is never named in artefacts) and restart
# the mock providers so they reload. Institution names + BB ids stay identical
# across packs (join keys for the cumulative Progressa solution).
set -euo pipefail
. "$(dirname "$0")/lib.sh"

log "regenerating seed CSVs (deterministic)"
python3 "$PACK_DIR/scripts/gen_seed_data.py" "$PACK_DIR/apps/data"

log "restarting mock providers (the restart IS the reload: apps read the host-mounted CSVs at startup)"
"${COMPOSE[@]}" restart app-pnia app-plr app-pemis 2>/dev/null || \
  "${COMPOSE[@]}" up -d app-pnia app-plr app-pemis

for app in app-pnia app-plr app-pemis; do
  retry 12 5 "$app healthy" docker exec "$app" curl -sf http://localhost:8000/v1/health
  docker exec "$app" curl -sf http://localhost:8000/v1/health | jq .
done
log "seeded — counts above. Mismatch NINs (in PNIA, not PLR) are listed in apps/data/README.md"
