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
