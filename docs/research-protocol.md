# Research protocol

## Scope and evidence

The initial sample comprises reviewed EPL/UCL regulation-time match-result families, with macro/politics comparisons. Exclude qualification, extra-time, penalties and ambiguous or incomplete groups. Selection is prospective: kickoff proximity first, observed activity for ties, fixed caps and saved reasons. Retain quiet/completed events. A sample duration is not a power calculation.

Synthetic demo prices are independent test paths; even 1X2 sums need not be coherent. They exercise software and failure handling, not a structural market model. Do not interpret synthetic P&L or fitted coefficients as market evidence.

## Liquidity and price discovery

Hypothesis: near-touch imbalance and order-flow imbalance contain information about subsequent price changes. Traditional-market evidence motivates the hypothesis, not its applicability to prediction markets.

Features: midpoint, spread in probability points/ticks, imbalance, imbalance-weighted midpoint, top-of-book OFI, trailing ten-second OFI, two-percentage-point depth, buy VWAP for 10/100 shares, and trailing 60-second stream-notification count. Notification intensity is explicitly not unique canonical volume. Depth and VWAP use shares, not fixed cash notional.

Targets: next midpoint-move direction within five minutes and probability-point/tick changes at 10, 60 and 300 seconds. Fixed-horizon endpoints use the last observation at/before the horizon, no more than five seconds old, in the same usable epoch. Sparse endpoints are excluded, never interpolated; report that selection effect. Next-move labels cannot cross gaps.

Baselines/models: no-change midpoint, imbalance-weighted midpoint, scaled Ridge OFI/imbalance/spread regression (alpha=10), and scaled logistic imbalance regression (C=0.1) versus training-frequency direction probability. These are deliberately fixed baselines. No tuning against held-out events.

Validation: order fixture/event groups by first receipt; hold out the last 30%. No asset from a held-out fixture enters training. Purge label endpoints and embargo the maximum forecast horizon before the test cutoff. Score event-mean RMSE and event-mean Brier loss; YES assets avoid treating binary complements as independent samples. Bootstrap events, not ticks. With fewer than five events, intervals are unavailable rather than spuriously precise. Event means are equally weighted.

Report coverage, valid labels, exclusions, baseline errors, uncertainty, phase/category composition and negative findings. Fit phase is based on the known schedule; “after scheduled kickoff” is not a claim that the match actually started. Further tuning requires nested chronological validation and a untouched final test block.

## Information shocks

Anchor on the first-known scheduled kickoff and on local receipt of each score update. Preserve corrections. Never label a scoreboard timestamp as goal time or infer exchange suspension from sports status. Scoreboard/evidence data are not strategy inputs.

Compare the 60 seconds before/after each anchor: spreads, depth and mean absolute repricing. A mechanical local comparison uses the same assets three minutes earlier; later 1–5-minute spreads describe persistence/recovery. These are descriptive controls and can differ in phase/news exposure. They are not causal matching or proof that a particular event moved the market. Review matched external episodes before strengthening a causal claim. Aggregate overlapping anchors within event before uncertainty calculation.

Lineups/injuries/disciplinary announcements initially use manually curated links and timestamps. RSS ingestion records links/title/publication/first-seen independently of market collection. No paid live-event source is required. A future independently timed sports feed must establish coverage/latency before use.

## Execution

Strategies are illustrative baselines, not optimized trading recommendations. Taker buys above +0.25 top imbalance and sells owned inventory below −0.25. Static quotes at the touch. Inventory-aware quotes move one tick down once inventory reaches twice the order size. Decisions occur at most every 30 seconds per asset; no free initial inventory is supplied.

- $10,000 virtual cash; $100 worst-case risk per reviewed event; $1,000 portfolio risk; five-share order size.
- Additional network latency: 0/250/1,000 ms. Takers add captured `seconds_delay`; post-only makers do not take liquidity.
- FAK permits partial fills and cancels the remainder; FOK requires enough displayed depth. Limit prices provide protection. Min size/tick constraints apply.
- Fees follow captured rates and the documented `shares × rate × p × (1−p)` schedule, rounded to five decimals. Makers pay zero. Unsupported/missing schedules block execution; category averages are not substituted.
- A metadata version change invalidates outstanding orders conservatively. This can understate resting-order duration; it avoids silently applying stale parameters.
- No naked sells; purchases debit cash and acquire basis. Risk recognizes compatible YES/NO or 1X2 terminal payouts. Pending buys reserve cash; pending sells account for removing hedges. Negative-risk conversions are not simulated.
- Cancellation latency is explicit; delayed takers cannot be withdrawn during the modeled venue delay. Censored orders at recording end are not reported as fills.
- Same-observation takers share displayed capacity. Later historical book observations are exogenous; the model cannot reproduce the market response to our hypothetical participation.
- Passive orders join behind displayed depth and preceding simulated orders. Only identified, verified-aggressor trades at the quoted price advance the queue. Book reductions/cancellations receive no credit. Queue multipliers 0/1/2 are sensitivity scenarios, **not bounds**.
- Unverified public notifications may therefore produce zero passive fills. Treat that as an evidence limitation, never infer fills from optimistic queue assumptions.
- Ledger separates realized price P&L, settlement P&L, inventory revaluation and fees; their sum reconciles to net marked equity change. Unknown marks remain unknown. Rewards/rebates = 0.

Public depth does not reveal exact queue position. This is paper simulation, not a claim of executable alpha or faithful market making. Do not rank strategies solely by terminal synthetic outcomes. Assess markouts, turnover, risk and latency/queue sensitivity on prospective observed data.

## Incident reporting

A default incident is a usable, same-epoch 60-second move of at least three probability points, with a five-minute fixture cooldown. These price-triggered cases are descriptive selection, separate from scheduled-event studies. Reports contain tested numerical facts and versioned rules, then-known evidence, missing information and later follow-up explicitly labeled as unavailable at alert time.

Template explanations default to “no convincing explanation.” The optional summarizer protocol takes at most 12 permitted evidence items / 24 KB, validates structured claims and source IDs, and falls back on failure. It cannot overwrite numeric facts. Since 16 October 2026 an OpenAI summarizer can be enabled with `--summarizer openai`; it receives the 12 most recently retrieved items before the move and every call is charged to the spend ledger. First-seen must refer to the observed revision, not the original document's first publication.

## Pre-match forecasting

The forecast-versus-market protocol, members, evaluation and limitations are in [forecasting.md](forecasting.md). It is a separate question from the microstructure studies above and shares only the recorder, the evidence store and the fee model.
