# Pre-match forecasting against the market

Second research track, added 16 October 2026. The microstructure track asks how information enters prices; this one asks **whether an ensemble of diverse forecasters prices EPL regulation-time 1X2 outcomes better than Polymarket before kickoff**, and whether any disagreement would survive spread and fees. Nothing is traded. Forecasts are frozen before kickoff, scored after results, and compared with the market price observed at the same moment.

Inspiration: Aitchison, Jeen, Shevlane and Day (Mantic), [Diversity is the Strength of the AI Crowd](https://arxiv.org/abs/2606.29661) (2026), and Mantic's [backtest against Polymarket](https://blog.mantic.com/p/backtesting-against-polymarket).

## What the sources establish, and what we take from them

- **Diversity paper.** On 113 binary Metaculus questions, five LLM forecasting pipelines (retrieval, then a probability; three samples per model, averaged) scored within a few points of each other alone. A weighted linear pool gained most by adding members whose forecasts were *decorrelated* from the frontier cluster (mean pairwise Jensen-Shannon divergence), not by adding the most accurate one. Leave-one-out value tracked decorrelation more than solo skill. The authors note the limits: one tournament, three samples, one aggregation operator, no confidence intervals.
  - *Taken:* members differ in kind; three samples per language model; weighted linear pool with clipping; the evaluator reports JS diversity and leave-one-out value, not only accuracy.
- **Mantic vs Polymarket.** Over 4,139 markets (February–August 2026), Mantic beat the Polymarket midpoint where little had traded and the edge faded with volume: no significant accuracy difference at $100k traded. Disagreement positions paid +15 points at the midpoint but +3.8 when entered at the bid/ask, before fees and sizing. A Mantic/market blend helped where both were similarly accurate.
  - *Taken:* the benchmark is the market at the same moment; paper entries are at the recorded ask, net of fees; market blending is a planned comparison. *Expected:* EPL match-result markets are among the most liquid on the venue, where Mantic found no edge.

## Members

| Member | Input | Why it is in the pool | Known weakness |
|---|---|---|---|
| `elo` | All Premier League and Championship results since 2014 (football-data.co.uk) | Cheap, deterministic, backtestable; decorrelated from text-based judgement | No lineups, injuries or schedule; promoted teams start from Championship ratings; one home advantage |
| `goals` | Same, last three seasons, one-year half-life | Different structure (attack/defence Poisson rates) | Independent Poisson without the Dixon-Coles low-score correction under-weights draws; no xG |
| `llm:openai/gpt-6.1-sol` | Fixture, kickoff, up to 15 evidence items retrieved before the forecast | Reads team news, injuries and line-ups | Same evidence for all LLMs; training priors overlap |
| `llm:anthropic/claude-opus-5.5` | same | Different vendor and training | same |
| `llm:google/gemini-4-argon` | same | same | same |
| `llm:x-ai/grok-4.7` | same | The paper found Grok decorrelated from the frontier cluster | same |
| `llm:deepseek/deepseek-v4-pro-0813` | same | Different lineage | same |
| `llm:moonshotai/kimi-k3` | same | The paper found Kimi close to the frontier cluster; kept to measure that | same |

Language models run through OpenRouter at default temperature, three samples each, averaged per model, structured JSON output validated before use. They are told not to use or guess odds, and the evidence they receive has every sentence mentioning betting, bookmakers, prediction markets or odds-like numbers removed (the count is stored per record).

**Market-aware variants** (`mkt:openai/gpt-6.1-sol`, `mkt:anthropic/claude-opus-5.5`) receive the full evidence plus the market midpoints at the same moment, as Mantic's forecaster does, and may disagree. They are scored individually and never enter either pool, so the pools stay independent of the benchmark they are compared with.

**Pools, both pre-registered before any result:** `ensemble` weighs every member equally (6 of 8 weights are language models); `ensemble_family` weighs the statistical and language-model families equally. Both are clipped to [0.02, 0.98] and renormalized. Weights are not fitted until enough frozen forecasts exist to fit them out of sample.

## Protocol

1. **Slots.** One forecast per fixture 24 hours, 6 hours and 1 hour before kickoff (`pml forecast-watch`). A missed slot can still fire up to six hours late, never after kickoff; the actual time is recorded.
2. **Market snapshot first.** Before any model runs, the five best bid and ask levels for all six YES/NO tokens are read from the public order book. Market probabilities are YES midpoints normalized to one (their raw sum is stored, typically about 1.005).
3. **Inputs as of the slot.** Results up to the day before; evidence retrieved before the forecast (each item carries its first-retrieval time), odds removed for market-blind members; the market only for market-aware members.
4. **Freezing.** Each record is appended to `forecasts.jsonl` with the SHA-256 of its content and of the previous line, so any later edit breaks the chain. The hash, fixture, slot and time are then committed and pushed to the `forecast-log` branch on GitHub, whose push time is an independent timestamp.
5. **Scoring** (`pml forecast-evaluate`, deterministic) once football-data.co.uk publishes results. Its full-time result is regulation time plus stoppage, matching the Polymarket rules.

## Evaluation

- **Accuracy:** ranked probability score (primary; respects home < draw < away ordering), three-way Brier, log loss.
- **Calibration:** one-vs-rest expected calibration error, ten bins.
- **Against the benchmark:** paired bootstrap (2,000 resamples, fixed seed) of each forecaster's RPS minus the market's, with a 95% interval. Bookmaker closing odds (margin removed proportionally) are a second benchmark when present.
- **Diversity:** mean pairwise Jensen-Shannon divergence between members and the market.
- **Member value:** increase in the equal-weight pool's RPS when each member is removed.
- **Paper trades:** for each outcome and side, buy up to $10 of YES (or NO) when the ensemble probability exceeds the best ask by at least three points, walking the recorded five-level ask ladder only while each level still clears the threshold; hold to settlement; taker fee `shares × rate × p × (1 − p)` with the captured rate (5%). Reported separately per slot.
- **Closing-line value:** with the recorder's output (`--capture`, `--watchlist`), each paper entry is compared with the last recorded midpoint before kickoff, and the market at kickoff is scored as a further benchmark. Prices moving towards an entry after it was made is a much faster signal than results.

## First results

Walk-forward backtest of the statistical members, 2022/23–2025/26 Premier League, 1,520 matches, against average bookmaker closing odds (`pml forecast-backtest`):

| Forecaster | RPS | Difference from closing odds (95% interval) |
|---|---|---|
| Bookmaker closing | 0.1947 | — |
| Elo | 0.2018 | +0.0071 (+0.0044 to +0.0097) |
| Goal model | 0.2046 | +0.0099 (+0.0072 to +0.0128) |
| Equal pool of both | 0.2023 | +0.0076 (+0.0051 to +0.0100) |

Both are clearly worse than closing odds, as public rating models usually are. Removing the goal model *improves* the pool slightly: on its own, it adds error rather than diversity. The language models cannot be backtested this way because their training data covers these seasons; they are evaluated only on matches after their forecasts were frozen.

## What to expect

- **This weekend (6 matches, 12 frozen forecasts) cannot show an edge either way.** The per-match RPS difference between a forecaster and the closing line has a standard deviation of about 0.053 in the backtest. Detecting a 0.01 difference with 80% power needs roughly 220 matches; a 0.005 difference needs roughly 900, more than two Premier League seasons. The weekend validates the pipeline, not the hypothesis.
- **Most likely outcome over time:** the ensemble is close to, but not better than, the market on liquid EPL markets; paper trades lose roughly the spread plus fees on average. Any edge, if it exists, is more likely in thinner markets (lower leagues, earlier slots, smaller volume), as Mantic found.
- **Language models may agree with each other more than with the statistical models.** That would show as low JS divergence within the LLM family and small leave-one-out values; `ensemble_family` exists to measure whether down-weighting them helps.

## Known limitations

- **Benchmark strength and liquidity.** Liquid football markets aggregate bookmaker information; the comparison is against one of the hardest benchmarks available.
- **Sample size and dependence.** Six matches; home/draw/away probabilities within a match are not independent samples; the bootstrap resamples matches.
- **Evidence can carry the market.** Previews retrieved by Exa often quote betting odds. Sentences mentioning betting or odds-like numbers are removed for market-blind members, but the filter is lexical: phrasing such as "Liverpool are heavy favourites" passes, and some harmless sentences (for example dates written 17/10) are dropped.
- **Model identity drift.** OpenRouter model names can point to updated weights or different hosting providers; the record stores the requested name and cost, not a weight hash.
- **Equal weights by fiat.** The pools are reasonable defaults, not optimized; fitting needs out-of-sample data the project does not have yet.
- **Statistical members are basic.** No lineups, injuries, rest days, xG or Dixon-Coles correction; promoted teams inherit Championship ratings.
- **Market snapshot.** One snapshot per slot; midpoints from best quotes; paper fills walk the recorded ladder, but latency between snapshot and order, minimum order size and our own impact are ignored. Closing prices come from the recorder's minute-by-minute REST snapshots, so "at kickoff" means the last snapshot up to a minute before it, and is missing where the recorder had a gap.
- **Results source.** football-data.co.uk updates within days and can correct rows; forecasts without a result are skipped, not scored as losses.
- **Integrity.** The hash chain plus the pushed hash shows when a record existed; it does not show that no *other* forecasts were made and discarded. Every slot that fires is published, so a missing slot is visible.
- **Operational.** One laptop, a consumer VPN and home internet; gaps are recorded, not filled. The spend cap is shared by search, summaries and forecasts: if it were exhausted, later language-model members would be missing and evaluation would fall back to the members present in every record.
- **Live match data.** API-Football's free plan serves only live fixtures for the current season, matched to ours by exact team names and kickoff; its dashboard rejects VPN connections (the API does not).

## Improving beyond the limitations

Done before the 17–18 October matches: items 2 (closing-line value), 4 (market-blind and market-aware members), 5 (odds stripped from evidence), 7 (a 6-hour slot; line-ups are usually published about 75 minutes before kickoff, so the 1-hour slot already covers them), 8 (ladder-aware paper fills; routing through the full paper broker remains) and 9 (hashes pushed to the `forecast-log` branch). The rest need more data, more reviewed leagues or a server.


1. **Grow the sample.** Add other leagues Polymarket lists and football-data.co.uk covers (Championship, La Liga, Serie A, Bundesliga, Ligue 1) plus the Champions League. Selection and rule review already work per league; the statistical members need each league's history.
2. **Faster signal: closing-line value.** Compare each forecast and paper entry with the market price at kickoff, which the recorder already captures. Beating the closing price is a far less noisy indicator than results and needs fewer matches.
3. **Fit weights honestly.** Once there are a few hundred frozen forecasts, fit pool weights by leave-one-event-out (as Mantic did for its market blend); select members on accuracy *and* JS diversity, as the paper recommends; test a pool–market blend.
4. **Separate market-blind and market-aware members.** Mantic shows the market price to its forecaster as evidence. Run both variants and report them separately, so independence and accuracy can be told apart.
5. **Strip odds from evidence** before it reaches market-blind members.
6. **Stronger statistical members.** Dixon-Coles correction, xG-based ratings (football-data.co.uk now includes expected goals), ratings from bookmaker odds, rest and travel.
7. **More slots.** Add 6 hours and confirmed-line-up time to see when, if ever, the ensemble leads the market.
8. **Realistic paper execution.** Route paper entries through the existing paper broker against recorded depth (size at the ask, minimum order size, latency), and test simple sizing rules with caps.
9. **External timestamping.** Commit the latest forecast hash to the repository (or another public log) before each kickoff, making the record independently verifiable.
10. **Evaluator agent.** A separate reviewer that checks forecasts and explanations against the frozen evidence; parked until its design is chosen.
11. **Hosting.** Run the recorder and watchers on a small server in a region where Polymarket and API-Football are both reachable without a VPN.
