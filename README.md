# Prediction Market Lab

**How do prediction markets incorporate information, and which apparent opportunities survive liquidity, fees and execution constraints?**

A Python research application for football prediction markets: public-data recording, causal replay, microstructure studies, evidence dossiers and fully funded paper execution. The primary universe is EPL and Champions League regulation-time 1X2, with a small macro/politics comparison sample.

This is a fresh independent implementation. No former-employer code, credentials, configuration or news archives are included. There is **no live-trading integration**.

## Run the demonstration

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Resolved packages are pinned in `uv.lock`; the official public SDK is explicitly pinned to `polymarket-client==0.10.0`.

```sh
uv sync --locked --no-editable
uv run --locked --no-editable pml demo
uv run --locked --no-editable pml dashboard --workspace data/demo
```

Open the local dashboard at `http://127.0.0.1:8501`. The standalone report is `data/demo/research/index.html`. The full demo creates eight synthetic events, eleven execution scenarios, three research memos, incident dossiers and a verified backup restoration. `pml demo --quick --out data/quick` runs one paper scenario. Existing run directories are never overwritten.

**The demo is synthetic.** Its prices and outcomes are not a calibrated simulation and establish no trading edge. Observed, synthetic, derived and simulated records carry separate provenance fields.

The application can be exercised offline after dependency installation. Live connectivity and EPL/UCL instrument mappings remain unverified in this environment: Polymarket's public Gamma host failed DNS resolution during the implementation check. The **72-hour pilot plus matchday** and **eight weeks of prospective recording** are outstanding empirical milestones. See [delivery status](docs/delivery-status.md).

## Inspect the complete chain

1. **Universe and coverage:** recorded assets, reviewed rules, versioned parameters and usable asset-time.
2. **Fixture replay:** a receipt-time cursor through prices, depth checkpoints and received evidence.
3. **Microstructure:** liquidity by event phase, chronological model validation and information-shock windows.
4. **Incidents:** measured moves, then-known evidence, uncertainty and separately labeled follow-up.
5. **Paper portfolio and health:** orders, rejections, cash/inventory, latency/queue scenarios and gaps.

```mermaid
flowchart TD
  A[Reviewed instrument catalogue] --> B[Public books, trades and metadata]
  B --> C[Fsynced journal and immutable compressed segments]
  E[Public or curated evidence] --> C
  C --> D[Validation and causal replay clock]
  D --> F[Partitioned Parquet and shared features]
  D --> P[Paper broker, risk checks and ledger]
  F --> R[Research studies and incident dossiers]
  P --> R
  F --> U[Streamlit terminal]
  R --> U
```

## Research companions

| Study | Reproducible work | Evidence boundary |
|---|---|---|
| [Liquidity and price discovery](notebooks/01_liquidity.ipynb) | Midpoint/weighted-midpoint baselines, regularized OFI forecasts, imbalance logistic regression; 10/60/300-second labels | Future labels never enter strategies; validation is grouped by event and purged |
| [Information and liquidity shocks](notebooks/02_information_shocks.ipynb) | Kickoff and **receipt** of score updates; spread/depth/repricing and local comparison windows | Descriptive association, not exact goal timing or causal attribution |
| [Execution and liquidity provision](notebooks/03_execution.ipynb) | Taker, static and inventory-skew strategies; sensitivity and full ledger | Passive fills are scenarios, not reconstructed queues or guaranteed bounds |

```sh
uv run --locked --no-editable python scripts/run_notebooks.py --workspace data/demo
```

Executed copies go to `artifacts/notebooks/`. Notebooks call the same calculations as the CLI and terminal. Read the [research protocol](docs/research-protocol.md) before interpreting results.

## Start prospective collection

Discovery is read-only. Use exact sport abbreviations from the current catalogue; do not guess IDs or infer contracts from titles.

```sh
uv run --locked --no-editable pml sports --out data/sports.json
uv run --locked --no-editable pml discover --sport <verified-abbreviation> --pages 2 --out data/discovery.json
uv run --locked --no-editable pml review-template --discovery data/discovery.json --out data/reviews.json
# Review complete home/draw/away YES+NO families and fill the review fields.
uv run --locked --no-editable pml catalog-build --discovery data/discovery.json --reviews data/reviews.json --out data/contracts.json
uv run --locked --no-editable pml select --contracts data/contracts.json --out data/watchlist.json
uv run --locked --no-editable pml --config config/default.toml collect --watchlist data/watchlist.json --out data/live --seconds 259200
```

Reviews bind identifiers to a hash of the rule text. Changed rules invalidate approval. Selection is capped at ten complete football families, six binary comparison conditions and 80 assets. Decisions are saved with the watchlist. Selection is fixed for each session: refresh the reviewed catalogue and start a new session as fixtures roll. Retain quiet and completed sessions in research history.

Collectors preserve REST reconciliation snapshots without claiming that concurrent REST/WebSocket responses establish lossless continuity. Reconnects require a fresh stream snapshot. Books are usable, recovering or degraded; a quiet quote differs from a silent feed.

## Analyze a recorded window

```sh
uv run --locked --no-editable pml replay --capture data/live --out data/runs/window-01 --from 2026-10-01T12:00:00Z --until 2026-10-01T14:00:00Z
uv run --locked --no-editable pml paper --capture data/live --out data/paper/window-01 --strategy inventory --latency-ms 250
uv run --locked --no-editable pml research --run data/runs/window-01 --paper-root data/paper --out data/research/window-01 --evidence data/evidence.json
```

Replay warms up from earlier observations, then retains the selected window. It defaults to a 250,000-record memory bound; select shorter windows or explicitly raise `--max-records`. Parquet is partitioned by receipt date. DuckDB uses independent in-memory connections against committed files. Each paper run owns its output directory.

Paper defaults: $10,000 virtual cash, $100 event risk, $1,000 portfolio risk, five-share orders and 250 ms additional latency. Takers also incur the captured venue delay. Missing fee/delay/tick/size metadata prevents cost-adjusted execution. All inventory must be purchased; rewards and rebates are zero.

## Operations and verification

```sh
sh scripts/check.sh
uv run --locked --no-editable pml verify data/live
uv run --locked --no-editable pml backup-check data/live
uv run --locked --no-editable pml health data/live
uv run --locked --no-editable pml pilot-report data/runs/window-01
```

CI exercises offline tests, a demo and notebooks. Docker Compose and systemd examples are provided but not automatically deployed. The [runbook](docs/runbook.md) covers restart behavior, offsite backups, matchday qualification and cost projections.

Further reading: [architecture](docs/architecture.md) · [data dictionary](docs/data-dictionary.md) · [source register](docs/sources.md) · [validation record](docs/validation.md) · [delivery status](docs/delivery-status.md) · [walkthrough](docs/walkthrough.md).

Future extensions: executable within-event consistency with explicit negative-risk conversions, calibration/resolution economics, reviewed Kalshi contract matching, richer inventory control and independent sports-event feeds. These are separate from the initial research claims.
