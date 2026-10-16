#!/bin/sh
# Pilot collector: restarts into the same archive after a crash until PML_PILOT_END.
# Variables: see .env.example ("Pilot scripts") and docs/runbook.md.
set -u
cd "$(dirname "$0")/../.."
: "${PML_PILOT_DIR:?set PML_PILOT_DIR, e.g. data/pilot-2026-10}"
: "${PML_PILOT_END:?set PML_PILOT_END as UTC, e.g. 2026-10-18T22:35:44Z}"
: "${PML_WATCHLIST:?set PML_WATCHLIST to the reviewed watchlist.json}"
# BSD date (macOS) first, then GNU date.
END=$(date -u -j -f %Y-%m-%dT%H:%M:%SZ "$PML_PILOT_END" +%s 2>/dev/null || date -u -d "$PML_PILOT_END" +%s) || exit 1
while :; do
	LEFT=$(( END - $(date +%s) ))
	[ "$LEFT" -le 60 ] && break
	echo "$(date -u +%FT%TZ) start, $LEFT s left"
	uv run --locked --no-editable pml --config "${PML_CONFIG:-config/default.toml}" collect --watchlist "$PML_WATCHLIST" --out "$PML_PILOT_DIR/capture" --seconds "$LEFT" && break
	echo "$(date -u +%FT%TZ) collector exited with $?, restarting in 30 s"
	sleep 30
done
echo "$(date -u +%FT%TZ) done"
