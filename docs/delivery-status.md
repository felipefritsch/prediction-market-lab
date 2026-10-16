# Delivery and acceptance status

This document distinguishes implemented software from empirical qualification. Test results and exact commands are recorded separately in `validation.md`.

| Gate | Delivered capability | Remaining qualification |
|---|---|---|
| A: independent foundation | Fresh repo, locked Python package, read-only adapter, reviewed mapping workflow, bounded recorder, immutable archive, restart/restore tests | Live EPL/UCL IDs and sports schema check; 72-hour pilot **plus matchday**; measured storage/cost forecast |
| B: replay and research | Causal replay, quality states, shared features, five terminal views, first two studies, source-linked dossiers, deterministic demo | Research findings on prospective observed books; data sufficiency and live coverage |
| C: paper and V1 research | Fully funded ledger, risk controls, latency/queue scenarios, third study, notebooks/memos, accounting tests | Eight weeks of prospective recording; observed-data sensitivity and reviewer walkthrough |

No API credentials or account setup are needed for the intended public recorder. Direct public Gamma requests nevertheless failed DNS in the implementation environment, including the authorized retry. Documentation and pinned-source verification do not substitute for live payload tests. No real instrument IDs, match events or empirical findings are fabricated.

### Direct public-data check — 2 October 2026

A bounded unauthenticated check failed DNS resolution for Gamma, Data API, CLOB, market WebSocket and sports WebSocket hosts, both inside and outside the shell sandbox. An unrelated HTTPS control (`example.com`) returned HTTP 200 outside the sandbox. The configured resolver returned `NXDOMAIN` for `gamma-api.polymarket.com`. This establishes a domain-specific resolution failure in this environment; it does **not** establish its cause or an HTTP geoblock decision. No live subscription or order-book payload was validated.

The reports are retained under ignored `data/connection-checks/2026-10-02-sandbox.json` and `data/connection-checks/2026-10-02-direct.json`. Repeat the check with `scripts/probe_public_data.py` using a new output filename. No VPN, resolver changes, wallet credentials, authenticated requests or orders were used.

### Live public-data check — 14 October 2026 (UTC)

From the same machine, Polymarket hosts resolved only through a consumer VPN; this check used one. It was unauthenticated and read-only: no wallet, credentials or orders.

- The probe (`data/connection-checks/2026-10-13-vpn.json`) returned HTTP 200 from Gamma, CLOB `/time` and `/book`, Data API `/v2/trades` and `/v2/oi`. The market WebSocket delivered `book` and `price_change` frames; the sports WebSocket delivered score frames.
- `pml sports`, `discover --sport epl`, `review-template`, `catalog-build` and `select` ran unchanged against live payloads. Selection kept six complete EPL 1X2 families for 17–18 October (36 assets).
- A one-hour capture (01:14–02:14 UTC) recorded 22,156 records with no errors or reconnects; all 36 books ended usable. `verify` and `backup-check` confirmed 22,194 archived records. Replay, three paper strategies and research ran on the capture. Projected raw storage was about 3 GB per 30 days for this pre-match watchlist.
- **The rule reviews behind this watchlist were machine-assisted** (template wording checked programmatically, two families read in full) and are labelled pending researcher sign-off. The capture is a connectivity and pipeline check, not a qualified pilot.

Observed limits from that hour: the sports stream carried other leagues only, so the score-to-fixture join remains untested until a matchday; the market stream also delivered `new_market` messages for unrelated markets; canonical trade rows include public wallet and profile fields. Passive strategies received zero fills because public trade notifications lack stable identities, as designed.

### Live evidence and forecasting — 16 October 2026

- **72-hour pilot running** (15 Oct 22:35 UTC – 18 Oct 22:35 UTC) on the six reviewed EPL fixtures of 17–18 October; Felipe signed off the rule reviews on 16 October (rules unchanged since the watchlist was built). Gaps so far: a one-minute VPN switch and a 17-minute home-internet outage, both recorded.
- **Evidence watcher:** timed Exa searches per fixture and live API-Football events, $20 shared spend cap.
- **Forecast watcher:** Elo, goal model and six language models from six vendors, frozen 24 h and 1 h before kickoff with a market snapshot. Protocol, expectations and limitations: [forecasting.md](forecasting.md).
- **Backtest:** statistical members are worse than bookmaker closing odds (Elo RPS 0.2018 vs 0.1947 over 1,520 matches). The weekend's six matches validate the pipeline; they cannot establish an edge.

## Explicit V1 boundaries

- Selection is refreshed between fixed-watchlist capture sessions; no unattended auto-approval of new rule mappings.
- REST snapshots are archived for reconciliation, not used to assert complete stream continuity.
- Canonical trade rows without unique identities preserve ambiguity rather than silently deduplicating trades/legs.
- Public notifications without verified identity/aggressor semantics cannot fill passive simulated orders.
- Event-study local comparison windows are descriptive; richer causal matching and independent event timestamps remain research work.
- Optional summarizer protocol and failure fallback exist; an OpenAI summarizer is available behind a spend cap (16 October 2026).
- RSS/manual sources work independently; mailbox ingestion/Graph, automatic email delivery and commercial sports feeds are later integrations.
- Docker/systemd/CI files are provided; local tests do not establish a successful remote CI/deployment run.
- There is a written walkthrough script; recording/publishing a personal recruiting video is a separate deliverable.

## Next extensions after observed V1 evidence

1. Settlement-compatible executable YES/NO and 1X2 consistency, including fees, quantities, latency and negative-risk conversions.
2. Calibration by horizon/liquidity/favorite region; disputes, voids, resolution lag and capital lock-up.
3. Reviewed Polymarket/Kalshi mappings and lead/lag tests with timestamp uncertainty.
4. Inventory policy, adverse-selection and reward economics justified by observed execution studies.
5. Independent sports-event feed only after measuring the value/limits of current evidence.
