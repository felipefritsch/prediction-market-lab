# Data dictionary

Probabilities lie in [0,1]. Probability points = 100 × probability difference. Ticks = probability difference / then-known tick size. Prices are collateral dollars/share; paper cash is virtual dollars. Stablecoin, chain and funding risk are not modeled.

| Dataset | Grain | Interpretation |
|---|---|---|
| Raw segments | One received message/response | Source/session, local sequence, UTC receipt, monotonic time, untouched payload and provenance |
| Contracts | One asset metadata version | Fixture/event/condition/market/asset IDs, outcome, rules, kickoff, execution parameters, `known_at`, version |
| Events | One normalized message component | Receipt/source time, kind, original normalized JSON; corrections and unknown schemas retained |
| Features | One asset after a wire batch or quality change | Quality/epoch, quotes, spread, imbalance, OFI/rolling OFI, depth, buy VWAP, notification intensity, as-of metadata |
| Depth | One level per sampled checkpoint | BUY=bid / SELL=ask, price, shares, receipt; at least ten seconds between checkpoints per asset |
| Trades | Stream notification/trade | Price, shares, supplied side/semantics and identity quality; notification count is not canonical volume |
| Canonical trades | One returned Data API page row | Source/receipt time, token/condition, price/size, composite candidate key and page identity; ambiguous repeated observations retained |
| Open interest | One returned condition snapshot | Genuine OI `value_usdc` and receipt time; never replaced by volume |
| Orders | One simulated order | Submission/ready/cancel time, FAK/FOK/MAKER, quantity, limit, queue scenario, rejection and parameter version |
| Fills / ledger | One simulated execution/account action | Price, shares, fee, inventory acquisition cost, cash effect or explicit settlement |
| Evidence | One observed source version | URL/ID, publication, first observation of this version, update time and usage permissions |
| Incidents | One threshold-triggered dossier | Measured change, then-known rules/evidence, candidate/no-explanation status, separately labeled follow-up |
| Evidence store (`evidence.json`) | One search result or match event per fixture | Title, URL, excerpt ≤1,200 characters, publication date if given, `first_seen` = first retrieval; later retrievals of the same item are not stored again |
| Spend ledger (`spend.jsonl`) | One paid or rate-limited call | UTC time, service, USD (provider-reported where available), short detail; the sum is checked against the cap before each call |
| Watcher state (`state.json`) | One evidence-watch process | Search slots already fired, last match-data poll, API-Football fixture links and how they were made, errors |
| Forecast log (`forecasts.jsonl`) | One fixture × slot | Member and pooled home/draw/away probabilities, market book and normalized midpoints at the same moment, evidence IDs, fee rate, data cutoff, `sha256` and `previous_sha256` |
| Forecast evaluation | One slot | Scores, bootstrap differences vs market, calibration, JS diversity, leave-one-out, paper trades and P&L |

## Identifiers and clocks

`fixture_id` is reviewed research identity. Three binary conditions represent home/draw/away, each with YES/NO assets. All six are required. An optional reviewed `sports_fixture_id` joins the stream's game ID/slug. Unknown mappings remain unjoined. Never join by title similarity.

One transaction can contain multiple trades/legs. Canonical v2 rows may lack unique trade IDs; composite candidate keys identify possible repeated observations but are **not guaranteed identities**. They do not silently create passive fills or canonical volume totals.

- `source_at`: claimed venue/publisher event time, potentially delayed/absent/revised.
- `received_at` / `first_seen`: local knowledge time for this observation/version.
- `known_at`: metadata availability, no earlier than recorded discovery/review.
- `epoch`: uninterrupted reconstructed-book episode, reset by recovery snapshots.
- `quality`: usable, recovering or degraded; one-sided books are conservatively excluded.
- `observed`, `synthetic`, `derived`, `simulated`: separate classes; features retain input provenance too.

Revisions never replace old raw records. Future labels/later incident follow-ups cannot enter earlier decisions. Quote age is not feed health. Coverage is conditional on selected assets' recorded intervals, not whole-venue or unrecorded-period coverage.

Source content is evidence, never instructions. Raw live captures are ignored by Git. Redistribute only data with established rights. Synthetic fixtures use fictitious IDs and `example.invalid` links.
