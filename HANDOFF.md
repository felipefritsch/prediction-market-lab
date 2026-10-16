# Handoff

State as of Friday 16 October 2026, 17:00 UTC. Detailed run notes and commands: `data/handover/HANDOVER.md` (local, not committed).

## What this project does

A read-only research lab for Polymarket football markets (EPL regulation-time home/draw/away). Two tracks share one recorder:

1. **Microstructure:** record public order books, trades and scores; replay them causally; study liquidity, information shocks and paper execution; explain sharp price moves from evidence retrieved before them.
2. **Forecasting vs the market:** freeze pre-match forecasts from a diverse ensemble (Elo, goal model, six LLMs from six vendors) 24 h, 6 h and 1 h before kickoff, each with the Polymarket order book at that moment, and score them after results ([docs/forecasting.md](docs/forecasting.md)).

No trading. No wallet, keys to a venue, or order endpoint exist.

## Current state

- **72-hour pilot running** on this Mac, 15 Oct 22:35 → 18 Oct 22:35 UTC, six EPL fixtures of 17–18 Oct (36 assets). Three detached processes, each restarting itself after a crash: recorder (`data/pilot-2026-10/capture/`), evidence watcher (Exa searches, API-Football live events), forecast watcher.
- **Done so far:** 5 of 6 T-24h forecasts frozen, hashes on the `forecast-log` branch; ~400k records; $1 of the $20 spend cap.
- **After the run:** verify, backup check, replay, paper, research (with `--summarizer openai`), pilot report, `forecast-evaluate` once football-data.co.uk has the results; then update `docs/delivery-status.md`. Exact commands are in the local handover.
- Code on `main` is green (`sh scripts/check.sh`: ruff, mypy, 79 tests) and pushed.

## Decisions and why

- **Polymarket only for market data; Exa, API-Football and LLMs in separate processes** — paid or flaky sources can never block market capture.
- **Every evidence item keeps its first-retrieval time** — so explanations and forecasts only see what was known then. Web search cannot be replayed as of a past date, so evidence must be collected live.
- **Diverse members, equal pre-registered weights** — per Aitchison et al. (2026), decorrelated members matter more than the single best; weights are not fitted until a few hundred frozen forecasts exist. A second pool weighs the statistical and LLM families equally so six correlated LLMs cannot dominate.
- **Market-aware LLM variants are scored but never pooled** — the pools must stay independent of the benchmark they are compared with. Odds sentences are stripped from evidence for market-blind members.
- **Benchmark is the market midpoint at the same moment; paper fills walk the recorded ask ladder, net of the 5% taker-fee formula** — Mantic showed midpoint "edges" shrink sharply at bid/ask prices.
- **Hash-chained forecast log plus pushed hashes** — forecasts can be shown to predate kickoff.
- **One shared, append-only spend ledger with a hard cap** — any paid call that could exceed it is refused.
- **Always `uv run --locked --no-editable`** — editable installs break under `~/Documents` on macOS (hidden `.pth` file).

## Open problems

- **Power and sleep:** the Mac sleeps when the lid is closed on battery (≈97 min of gaps on Friday morning). Needs the charger connected through Sunday.
- **Network:** Polymarket resolves only via VPN here; API-Football's dashboard rejects the VPN (its API works). A server where both are reachable would remove both issues.
- **First forecast has no evidence:** Everton–Chelsea T-24h raced its own search; fixed in `fe5cf0a`, record kept as frozen.
- **Sample size:** six matches cannot show an edge; ~220 matches are needed to detect a 0.01 RPS difference. Backtest: Elo 0.2018 vs closing odds 0.1947.
- **Not yet validated live:** Polymarket's sports feed has shown no EPL scores; API-Football links to our fixtures only appear at kickoff (exact team names, checked offline).
- **Known weaknesses:** lexical odds filter; LLMs cluster tightly; basic statistical members; OpenRouter model names can change underneath; closing prices come from minute-level REST snapshots.
- **Roadmap** (more leagues, fitted weights, stronger members, full paper broker, server, evaluator agent): [docs/forecasting.md](docs/forecasting.md#improving-beyond-the-limitations).
