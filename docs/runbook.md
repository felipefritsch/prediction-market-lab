# Local and server runbook

## Qualification before collecting a matchday

1. Install locked Python 3.12 dependencies and run `sh scripts/check.sh`.
2. Run public `sports` and bounded `discover`. Save raw responses. Exact league abbreviations/series IDs, outcome IDs, fee schedules, delays and sports payload joins must be checked against live responses.
3. Use `review-template`; retain complete desired groups and fill fixture/category/competition/family outcome, regulation-time flag, reviewer, aware review time and rule hash. Set `sports_fixture_id` only after checking a received payload. Do not approve guessed title matches.
4. Build the catalogue and select a watchlist. Review saved exclusions. The selector requires confirmed order availability and complete identifier relationships.
5. Run a short capture and inspect raw messages/health. Only then run 72 hours plus an actual football matchday. No live venue account is needed for public recording.

The catalogue scan is explicitly bounded and may be incomplete. Its outputs are candidates, not all current fixtures. Repeat discovery in new files and merge via repeated `catalog-build --discovery` arguments. Reviews bind exact rule text, not permanently trusted contract titles.

## Restart and rolling selection

The collector locks one archive and owns its SQLite writes. On restart it repairs a torn journal tail, preserves committed segments and starts every stream through a fresh snapshot. Retry/backoff is bounded; raw schema failures are visible. Ctrl-C drains queued records and flushes. Unexpected termination may lose only the incomplete final journal record; source gaps are marked on reconnect.

Each session has a fixed reviewed watchlist. Refresh the universe as fixtures roll, save the new selection, and start a subsequent session. Do not overwrite historic sessions or filter the research sample after seeing returns. Eight weeks means repeated declared selection and recording, not one frozen match list for eight weeks.

`deploy/` contains systemd examples for a dedicated unprivileged `pml` user and a daily restoration check. They are templates, not installed services. `compose.yaml` runs a bounded 72-hour collector and restarts only on failure. Provision volume ownership for container UID 10001. The services must be installed/configured deliberately; no server was deployed as part of local implementation.

## Health and disk

`pml health data/live` shows last receipt by source, reconnect count, queue depth/high-water mark, processing lag, book quality/reasons, raw bytes and a 30-day extrapolation. Source failures do not count as successful source coverage. `no_book_data` fails collector qualification even if HTTP requests worked.

The configured archive cap stops capture rather than silently downsampling. Inspect remaining disk capacity before starting; the application cannot protect unrelated processes from exhausting the disk. Investigate sustained queue backlog, stale feed messages, normalization errors or repeated resynchronizations before expanding the watchlist.

After a recorded window, run `pml pilot-report <run>`. It reports span and quality-weighted asset-time. A 72-hour span with gaps, a synthetic tape or no matchday does **not** pass the pilot.

## Backup and restore

```sh
pml backup data/live /path/to/new-backup-directory
pml restore /path/to/new-backup-directory /path/to/new-restored-capture
pml backup-check data/live
```

Backups copy only committed raw manifests/segments, selected supporting files and an online SQLite backup. A live pending journal is excluded: RPO is the flush interval plus backlog, not zero. The journal itself is recovered on local restart. Restoration validates checksums, row counts and SQLite integrity. Backups of live capture can contain slightly different catalogue/raw cutoffs; replay's raw metadata observations are authoritative.

The daily service tests local restore only. Configure a separately retained offsite copy to protect against machine loss. Do not call a local temporary restoration test an offsite backup.

`pml backup` covers the capture only. Forecasts, evidence and the spend ledger cannot be re-collected, so copy them separately while watchers run, to a new directory on another machine or synced storage, after each forecast slot:

```sh
pml backup-files /path/to/new-research-backup data/evidence/evidence.json data/evidence/spend.jsonl data/evidence/state.json data/forecasts.jsonl
pml restore /path/to/new-research-backup /path/to/new-restored-files
```

Each file is read once, so its copy and checksum agree even while a watcher appends. `restore` verifies every checksum.

### Automatic research backups

Set `PML_BACKUP_DIR` in `.env` to an absolute path on another machine or synced storage. There is no default: commands that need it stop with an error when it is unset or relative, and they never create the directory, so an unmounted volume cannot turn into a local folder.

- `forecast-watch --backup-after-slot` (on in [`scripts/pilot/forecasts.sh`](../scripts/pilot/forecasts.sh)) refuses to start without the variable. After each batch of due slots it copies `forecasts.jsonl`, `evidence.json`, `spend.jsonl` and `state.json` to a new folder `$PML_BACKUP_DIR/<UTC time, e.g. 20261017T054012Z>/`, restores it into a temporary directory and only then writes `verified.json`. A failed backup prints `BACKUP FAILED: ...` to the watcher log and forecasting carries on.
- `pml backup-files --timestamped FILE...` does the same on demand.
- `pml backup-status [--max-age-hours 12]` re-checks the newest folder with a `verified.json` against its checksums, falls back to older ones if it fails, and prints the age. It exits 1 with a warning when no verified backup is newer than the limit, so it can run from cron or before a restart. A folder without `verified.json` never counts.

`spend.jsonl` is appended by the evidence watcher, so a copy can very rarely end inside a line being written. The next slot's copy has the complete line.

## Cost forecast

Monthly allocations: infrastructure/storage/backups $30, optional models $10, reserve $10. These are budgets, not vendor quotations. No billable service is provisioned by this repo.

```sh
pml budget --raw-bytes <measured-bytes> --elapsed-seconds <measured-duration> --server-usd <your-quote> --storage-gb-usd <your-quote>
```

Use both quiet and matchday rates. The estimate assumes a constant rate and one backup copy; add derived data, egress and taxes to your operational forecast. Reduce the next declared recording universe or retention only after preserving the selection audit; never delete unfavorable results to meet a budget.

## Evidence and terminal

`pml ingest-rss --url <configured-https-feed> --fixture <reviewed-id> --out data/evidence-new.json` stores source links and first-seen times. Keep revisions in your combined evidence list; do not overwrite old items. RSS failures cannot interrupt the market collector. Model-use and redistribution flags default false. Mailbox ingestion is a future Graph adapter; no EWS dependency exists.

For an observed dashboard workspace, create `workspace.json` with relative `run`, `research`, `capture` paths. Put paper run folders under `paper/` if using its portfolio inspector. The terminal binds to loopback by default. It is not an authenticated Internet service.

## Release/recruiting checklist

- Re-run offline tests, type/lint/format checks and notebooks.
- Verify the selected run manifests and restore a backup.
- Keep observed/derived/synthetic/simulated labels visible.
- Write research findings only after inspecting held-out results, exclusions and event uncertainty.
- Publish only permitted data; live captures and credentials are excluded from Git.
- Record the short walkthrough after the live pilot if describing observed market behavior.

## Evidence and forecast watchers

`evidence-watch` (Exa searches, API-Football live events) and `forecast-watch` (frozen pre-match forecasts) run as separate processes beside the collector. Keys live in `.env` (`EXA_API_KEY`, `API_FOOTBALL_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`); pass `--env-file .env`. Both resume from their own files after a restart: fired search slots and spend from `state.json`/`spend.jsonl`, frozen slots from `forecasts.jsonl`. Check `state.json` for errors and football links, and `tail spend.jsonl` for costs. A refused call (`BudgetExceeded`) stops that kind of call, not the process. Never edit `forecasts.jsonl`; the hash chain would break.

## Pilot scripts

[`scripts/pilot/`](../scripts/pilot/) holds the restart loops for a pilot: [`capture.sh`](../scripts/pilot/capture.sh) (collector), [`evidence.sh`](../scripts/pilot/evidence.sh) and [`forecasts.sh`](../scripts/pilot/forecasts.sh). Each restarts its process after a crash until the pilot ends. They read their settings from environment variables, listed in [`.env.example`](../.env.example) under "Pilot scripts". `PML_PILOT_DIR`, `PML_PILOT_END` (UTC) and `PML_WATCHLIST` are required, and a script stops at once if one is missing. Keys stay in `.env`, which the watchers read through `uv run --env-file`. `forecasts.sh` also needs `PML_BACKUP_DIR` there ([automatic research backups](#automatic-research-backups)).

```sh
export PML_PILOT_DIR=data/pilot-2026-10 PML_PILOT_END=2026-10-18T22:35:44Z PML_WATCHLIST=data/live-check/watchlist.json PML_FORECAST_LOG_DIR=data/forecast-log
nohup caffeinate -i sh scripts/pilot/capture.sh >> "$PML_PILOT_DIR/run.log" 2>&1 &
nohup sh scripts/pilot/evidence.sh >> "$PML_PILOT_DIR/evidence/run.log" 2>&1 &
nohup sh scripts/pilot/forecasts.sh >> "$PML_PILOT_DIR/forecasts/run.log" 2>&1 &
```

Every restart runs `uv run --locked --no-editable`, which rebuilds the package from the working tree as it is at that moment, uncommitted edits included, into the shared `.venv`. While a pilot runs, keep the main checkout clean and on a checked commit, and develop and test in a separate `git worktree`, which has its own `.venv`.

To move one watcher onto new code, check out the commit, wait until no slot is due, and stop only its Python process (`pkill -TERM -f "pml forecast-watch"`). Its loop restarts it 60 s later on the checked-out code. Confirm by comparing `shasum -a 256 .venv/lib/python3.12/site-packages/prediction_market_lab/forecast.py` with `git show HEAD:src/prediction_market_lab/forecast.py | shasum -a 256`.
