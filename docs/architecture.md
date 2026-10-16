# Architecture and ownership

One Python application with separate collection and research state ownership.

| Module | Responsibility |
|---|---|
| `models` / `config` | Domain records, timestamps, provenance and limits |
| `adapters` | Pinned official SDK discovery; raw public HTTP/WebSocket transport |
| `universe` | As-of complete-family selection with explicit exclusion reasons |
| `collector` | Independent producers, bounded queue, one archive/catalog writer, health |
| `storage` | Fsynced journal, immutable compressed segments/checksums, SQLite/WAL and Parquet |
| `normalization` / `books` | Wire translation, unknown schemas, atomic replacement-quantity L2 updates |
| `replay` | Causal arrival clock, as-of metadata, quality states and shared features |
| `paper` | Fully funded accounting, related-outcome risk, taker depth and passive scenarios |
| `research` | Labels, grouped chronological validation, event-level uncertainty |
| `incidents` / `evidence_sources` | Bounded evidence, template reports, optional RSS and summarizer boundary |
| `reporting` / `app` | HTML/memos and five terminal views |
| `operations` | Checksums, online SQLite backup, restoration and cost forecasts |
| `live_evidence` | Timed Exa searches, live API-Football events, evidence store, shared spend ledger and cap |
| `summarizer` | Optional metered OpenAI summarizer behind the incident boundary |
| `forecast` | Historical results, Elo/goal/language-model members, pools, frozen forecast log, backtest |
| `scoring` | Deterministic proper scores, calibration, bootstrap, diversity, leave-one-out, paper trades |

## Durability and concurrency

Each observation receives local UTC receipt time, session/source identity, local sequence and monotonic time. Raw payloads are journaled and fsynced before append returns. A count/time boundary publishes a compressed segment followed by its manifest. Readers only see committed manifests. A torn final journal line is recoverable; malformed complete lines are errors. Recovered already-published segments are deduplicated by full content digest.

Segment ordinals preserve arrival order if the wall clock steps backward. Replay flags reversals, invalidates books and clamps its processing clock; original timestamps remain in the audit. Timestamps cannot establish feed completeness because no replayable exchange sequence is available.

The archive OS lock prevents competing collectors. The coordinator owns SQLite writes. Annotations use separate immutable files. Checkpoint/outbox tables support local coordination, not a claim of external message delivery. News/model providers are outside the market producer's call chain: `evidence-watch` and `forecast-watch` are separate processes that write their own files and never block the collector. Every paid call is checked against one append-only spend ledger first.

## Replay timing

Pending timers strictly earlier than a new observation run against the previously known book. At exact ties, observations apply first, then timers; manifests declare this convention. Strategies can only act after the observations that triggered them. Unknown trade-side semantics or unstable notification identities cannot create passive fills.

All changes for an asset in one wire message apply atomically. Quantities replace prior values; zero deletes a level. Invalid/crossed/out-of-order books are excluded. Fresh snapshots after gaps create a new epoch; future labels cannot cross epochs.

## Storage and bounds

Immutable runs contain date-partitioned Parquet, input/source digests, settings and file checksums. The manifest is the run commit marker. DuckDB never shares a writable database with the collector/dashboard. Paper runs own their ledgers.

Capture uses bounded queues and a hard archive-growth cap. Research keeps a declared bounded window in memory, not an entire eight-week recording. The collector shares reconstruction through `ReplayEngine(retain=False)` without retaining feature tables. REST reconciliation snapshots remain audit evidence; asynchronous comparisons do not prove lossless synchronization.

No secure venue client, wallet, order submission, employer credentials, email sending or automatic deployment exists here. Graph mailbox ingestion and cross-venue/negative-risk execution are later adapters.
