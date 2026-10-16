#!/bin/sh
# Evidence watcher for the pilot: restarts after a crash until the pilot ends; state and spend persist.
# Variables: see .env.example ("Pilot scripts") and docs/runbook.md.
set -u
cd "$(dirname "$0")/../.."
: "${PML_PILOT_DIR:?set PML_PILOT_DIR, e.g. data/pilot-2026-10}"
: "${PML_PILOT_END:?set PML_PILOT_END as UTC, e.g. 2026-10-18T22:35:44Z}"
: "${PML_WATCHLIST:?set PML_WATCHLIST to the reviewed watchlist.json}"
until uv run --locked --no-editable --env-file "${PML_ENV_FILE:-.env}" pml evidence-watch --watchlist "$PML_WATCHLIST" --out "$PML_PILOT_DIR/evidence" --until "$PML_PILOT_END" --budget-usd "${PML_BUDGET_USD:-20}" ${PML_MAPPING:+--mapping "$PML_MAPPING"}; do
	echo "$(date -u +%FT%TZ) watcher exited with $?, restarting in 60 s"
	sleep 60
done
echo "$(date -u +%FT%TZ) done"
