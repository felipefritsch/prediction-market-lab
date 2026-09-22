# Local verification record

Verified on 21 September 2026 on macOS with Python 3.12.14 and uv 0.11.2. This record establishes local software behavior. It does not establish live-feed compatibility, market-data completeness or empirical profitability.

## Checks completed

| Check | Result |
|---|---|
| Locked, non-editable installation | Successful from `uv.lock`; 116 resolved packages |
| `sh scripts/check.sh` | Successful: Ruff lint, formatting, mypy and pytest |
| Type checking | No issues in 19 application modules |
| Test suite | **52 passed**; final run completed in 7.20 seconds |
| Full offline demo | Eight synthetic events, 40 outcome assets, 11 paper scenarios and 23 incident dossiers |
| Raw archive verification | All **9,608 records** verified against manifests and checksums |
| Backup restoration | **9,608 records restored**, with 44 files in the backup; SQLite integrity checked |
| Notebook execution | All three companions executed top to bottom in fresh Python kernels |
| Terminal integration | All five views and the replay cursor exercised with Streamlit AppTest |
| Browser review | Universe, replay, microstructure, incidents and paper views inspected; three executed notebook figures rendered and inspected |

The environment used a project-specific temporary uv cache. Notebook kernels and loopback preview servers needed local-port permission beyond the restricted shell sandbox. No paid API, trading account or message provider was used.

## Reproduce

From the repository root after installing Python 3.12 and uv:

```sh
uv sync --locked --no-editable
sh scripts/check.sh
uv run --locked --no-editable pml demo --out data/verification
uv run --locked --no-editable pml verify data/verification/capture
uv run --locked --no-editable python scripts/run_notebooks.py --workspace data/verification --out artifacts/verification-notebooks
uv run --locked --no-editable pml dashboard --workspace data/verification
```

Choose a new output directory for each demo. Existing committed run directories are intentionally not overwritten. The delivered demo is under `data/demo`; executed notebooks are under `artifacts/notebooks`. Both directories are ignored by Git and can be regenerated. The notebook generator is `scripts/create_notebooks.py`; it applies the same lint and formatting rules used by CI.

The tests cover replacement-level book updates, atomic deltas, unknown schemas, recovery, feed silence, partial journals, checksums, source-aware trade identities, future information, revisions, grouped/purged labels, complete contract families, score corrections, provider failures, execution delay, fees, cash/inventory reconciliation, settlement and conservative passive queue scenarios. The repeatability test compares numerical Parquet outputs from the same capture.

The full demo uses deliberately fictitious inputs. Its 98.3% usable recorded asset-time is a synthetic failure/recovery fixture result, **not a live availability measurement**. Its eight events are too few to support robust empirical model comparisons; the output suppresses held-out uncertainty intervals when fewer than five events are available.

## Not verified or completed

- Live Polymarket discovery: the public Gamma host failed DNS resolution, including an authorized retry outside the restricted sandbox. SDK/source documentation checks and synthetic schema fixtures cannot replace recorded live-response tests.
- Actual EPL/UCL identifiers, reviewed live 1X2 families and sports-stream payload joins.
- A 72-hour pilot plus a matchday, measured live storage/cost forecast, and eight weeks of prospective data.
- Remote GitHub Actions, Docker deployment or systemd service operation. Their configuration is provided; successful local tests do not establish those deployments.
- Observed-data research conclusions or executable trading profitability.
- A recorded recruiting video. A written walkthrough is provided in `walkthrough.md`.

See `delivery-status.md` for the remaining acceptance gates and deliberately deferred integrations.
