#!/bin/sh
# Pre-match forecasts at T-24h, T-6h and T-1h per fixture; restarts after a crash, skips frozen slots.
# After each slot it backs up the research files to $PML_BACKUP_DIR, which must be set (e.g. in .env).
# Variables: see .env.example ("Pilot scripts") and docs/runbook.md.
set -u
cd "$(dirname "$0")/../.."
: "${PML_PILOT_DIR:?set PML_PILOT_DIR, e.g. data/pilot-2026-10}"
: "${PML_PILOT_END:?set PML_PILOT_END as UTC, e.g. 2026-10-18T22:35:44Z}"
: "${PML_WATCHLIST:?set PML_WATCHLIST to the reviewed watchlist.json}"
until uv run --locked --no-editable --env-file "${PML_ENV_FILE:-.env}" pml forecast-watch --watchlist "$PML_WATCHLIST" --evidence "$PML_PILOT_DIR/evidence/evidence.json" --out "$PML_PILOT_DIR/forecasts/forecasts.jsonl" --spend-ledger "$PML_PILOT_DIR/evidence/spend.jsonl" --budget-usd "${PML_BUDGET_USD:-20}" --until "$PML_PILOT_END" ${PML_FORECAST_LOG_DIR:+--publish-dir "$PML_FORECAST_LOG_DIR"} --backup-after-slot; do
	echo "$(date -u +%FT%TZ) forecast watcher exited with $?, restarting in 60 s"
	sleep 60
done
echo "$(date -u +%FT%TZ) done"
