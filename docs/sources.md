# Source register and interface evidence

Checked against official documentation and installed `polymarket-client==0.10.0` source on 2026-09-21. API documentation can change: rerun the live adapter checks before a prospective pilot. Local DNS failure prevented payload verification at that time; live Gamma, CLOB, Data API and stream payloads were checked over a VPN on 14 October 2026.

| Source | Use / constraint |
|---|---|
| [Polymarket Python SDK](https://docs.polymarket.com/getting-started/python) | Public `AsyncPublicClient` behind the adapter; exact pin; no secure client |
| [Platform changelog](https://docs.polymarket.com/changelog/predictions) | Migration awareness; do not reuse 2025 interfaces without verification |
| [Discover markets](https://docs.polymarket.com/market-data/discover-markets) | Sports/series/event/market discovery and reviewed identity mapping |
| [Real-time data](https://docs.polymarket.com/market-data/realtime-data) | Market/sports streams, replacement deltas, heartbeat/reconnect behavior; sports timing limitations |
| [Prices and order books](https://docs.polymarket.com/market-data/prices-order-books) | Public depth and sampled price history; no assumption of historical L2 recovery |
| [Market details](https://docs.polymarket.com/market-data/market-details) | As-of fees, tick sizes, delay and market state |
| [Fees](https://docs.polymarket.com/trading/fees) | Captured rate formula, makers zero, five-decimal fee precision |
| [Order behavior](https://docs.polymarket.com/trading/place-orders) | Size/tick constraints, FAK/FOK, post-only and delayed marketable orders |
| [Negative risk](https://docs.polymarket.com/concepts/negative-risk) | Later extension: conversions require explicit compatible rules/mechanics |
| [DuckDB concurrency](https://duckdb.org/docs/stable/connect/concurrency.html) | Independent read queries over immutable Parquet |
| [Cont, Kukanov, Stoikov](https://arxiv.org/abs/1011.6402) | OFI hypothesis motivation, not evidence of prediction-market alpha |
| [Gould and Bonart](https://arxiv.org/abs/1512.03492) | Queue imbalance hypothesis motivation |
| [football-data.org coverage](https://www.football-data.org/coverage) | Optional independent fixtures/results reference; not required by V1 |
| [football-data.co.uk](https://www.football-data.co.uk/) | Historical and current-season results, expected goals and bookmaker (closing) odds for forecasting members and benchmarks |
| [Exa search API](https://exa.ai/docs/reference/search) | Timed evidence retrieval; cost reported per call; results cannot be re-queried as of a past time |
| [API-Football](https://www.api-football.com/documentation-v3) | Live EPL fixtures and events; free plan serves live data only for the current season |
| [OpenRouter](https://openrouter.ai/docs) | Language-model forecasters from several vendors; live price list; reported per-call cost |
| [Aitchison et al. 2026](https://arxiv.org/abs/2606.29661) | Ensemble diversity (Jensen-Shannon, leave-one-out) motivating member selection |
| [Mantic: backtesting against Polymarket](https://blog.mantic.com/p/backtesting-against-polymarket) | Market-midpoint benchmark, bid/ask entry, edge vs liquidity |
| [Microsoft EWS retirement](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-ews-exchange-online) | Future mailbox adapter should use Graph |

The pinned SDK's public action definitions were used to check v2 parameter names: `condition_id` for trades/OI; `token_id`, `start`, `end`, `bucket_seconds`, cursor pagination for sampled prices. Explicit price windows are limited to 15 days. Backfill commands preserve raw pages and report endpoint exhaustion, not historical depth completeness.

Offline response fixtures are **synthetic examples of documented schemas**. They are not mislabeled as recorded live responses. Once connectivity works, add minimal permitted real response fixtures with capture date/endpoint/schema metadata and no credentials or unnecessary public profile fields.
