# Pilot report: 72-hour EPL pilot, 15–18 October 2026

Interim report, 20 October 2026. Collection is complete and verified. Replay, paper execution, research and forecast scoring are still to run (see [Pending](#pending)). Times are UTC.

## Summary

- **Collection worked end to end through a real matchday.** The recorder captured 4.50 million messages on 36 assets for six EPL matches. Archive verification and a restore test both passed.
- **The forecasting pipeline worked as designed.** 18 of 18 forecasts were frozen before kickoff, each with a market snapshot taken at the same moment. The hash chain is intact, all 18 hashes are published on the `forecast-log` branch, and no forecast read evidence retrieved after it was made.
- **The recorder gate is not yet passed, and probably will not be on this run.** The 72-hour wall-clock span was reached, with a matchday. But the Mac slept for about 2¾ hours in total, and the runbook says a 72-hour span with gaps does not pass. Usable asset-time comes from `pml pilot-report` once the replay has run.
- **Two bugs found live and fixed:**
  - The API-Football live query was rejected, so no live match events were recorded for about 3¾ hours on Saturday.
  - Replaying the full capture overflowed a 2 GB string limit.
- **Total spend: $4.86 of the $20 cap.**
- **Nothing here shows an edge.** Six matches cannot; detecting a 0.01 RPS difference needs roughly 220 ([forecasting.md](forecasting.md#what-to-expect)).

## What ran

| | |
|---|---|
| Window | 15 Oct 22:35:45 → recorder ended 19 Oct 01:20:18 (watchers ended 18 Oct 22:36) |
| Fixtures | Everton–Chelsea, Brentford–Liverpool, Fulham–Hull, Man City–Ipswich, Newcastle–Aston Villa (Sat 17 Oct); Brighton–Crystal Palace (Sun 18 Oct) |
| Assets | 36: home/draw/away × YES/NO per fixture, rules signed off 16 Oct |
| Processes | Recorder, evidence watcher (Exa, API-Football), forecast watcher; one Mac on a consumer VPN |
| Code | Recorder and evidence watcher from their original launches; forecast watcher moved to `3cad970` on 16 Oct 17:56, then `scripts/pilot/forecasts.sh` (automatic backups) at 18:06; evidence watcher restarted on `99679c1` on 17 Oct 14:59 |

## Results by component

### Recorder

| Measure | Value |
|---|---|
| Messages archived and verified | **4,503,356** (`pml verify`); restore check passed (`pml backup-check`) |
| Raw size | 675 MB; projected 6.7 GB per 30 days at this mix of quiet and matchday time |
| Main message types | 7.53 M price changes, 51.7 k full books, 21.1 k trades, 2.5 k canonical trade rows, 151.9 k score messages, 59.8 k REST book snapshots, 23 market settlements |
| Reconnects / recorded gaps / feed silences | 571 / 844 / 38 |
| Peak queue depth | 438 (bounded queue, no overflow) |
| Source errors | 38,713, of which **38,697** were `404` on REST book snapshots for markets that had already settled; 16 were short connection failures (DNS or timeouts, consistent with brief VPN drops) |
| Machine asleep | ≈ 2 h 45 min in total (the recorder's monotonic clock ran 259,199 s while the wall clock covered 269,073 s) |

The recorder also received 142,663 `new_market` messages for unrelated markets on the shared stream. They are archived, and replay ignores them.

### Evidence watcher

| Measure | Value |
|---|---|
| Evidence items | **259**: 157 Exa search results, 102 API-Football match events |
| Search slots fired | 72 (24 h, 2 h and 1 h before kickoff, in play, after the match) |
| API-Football links | 5 of 6 fixtures linked by exact team names and kickoff (all except Everton–Chelsea; see incidents) |
| Calls | 75 Exa ($0.53), 106 API-Football (free plan) |

### Forecast watcher

| Measure | Value |
|---|---|
| Forecasts frozen | **18 / 18** (6 fixtures × 24 h, 6 h, 1 h), all before kickoff |
| Integrity | Hash chain verified; all 18 hashes on the `forecast-log` branch; 0 evidence items retrieved after their forecast |
| Timeliness | 13 records carry `scheduled_at`: on average 3.2 min after the slot, at most 12.7 min (fixtures sharing a slot run one after another) |
| Pre-cutoff-fix records | 5 (the first T-24h batch), flagged, not excluded ([forecasting.md](forecasting.md#forecasts-frozen-before-the-evidence-cutoff)) |
| Member failures | One DeepSeek sample (Fulham–Hull, 1 h before); its other samples were still pooled |
| Research backups | 9 verified copies to iCloud, one after each slot plus manual ones |
| LLM cost | $4.33 via OpenRouter; the cost per forecast rose from about $0.19 to $0.35 as evidence accumulated |

Final forecasts, 1 hour before kickoff: home / draw / away (%).

| Match | Equal-weight ensemble | Polymarket midpoint |
|---|---|---|
| Everton – Chelsea | 32 / 28 / 40 | 33 / 27 / 41 |
| Brentford – Liverpool | 30 / 26 / 44 | 36 / 27 / 38 |
| Fulham – Hull City | 48 / 27 / 25 | 59 / 24 / 18 |
| Man City – Ipswich | 80 / 13 / 7 | 78 / 14 / 9 |
| Newcastle – Aston Villa | 43 / 26 / 31 | 43 / 26 / 31 |
| Brighton – Crystal Palace | 51 / 25 / 24 | 68 / 18 / 13 |

Across all 18 forecasts the ensemble differed from the market by 4.0 points on average per outcome, and by at most 17.5 points (Brighton). Where it disagreed, it was less confident in the favourite than the market. These are not results: scoring waits for football-data.co.uk, and six matches cannot separate the two.

## Incidents and fixes

| When | What happened | Effect | Fix |
|---|---|---|---|
| 16 Oct morning | Mac slept with the lid closed on battery | Most of the ≈ 2¾ h recorder downtime | Charger; lid kept open |
| 16 Oct 17:56–18:06 | Planned restarts of the forecast watcher onto the evidence cutoff, then onto the versioned script with backups | No slot missed (next was 05:40) | — |
| 17 Oct 11:15–15:00 | API-Football rejected `live=39`; it accepts only `all` or two or more hyphenated league IDs. The test mock had accepted the wrong value. | No live events for Everton–Chelsea; the 14:00 matches' first-half events were stored only at 15:00, with `first_seen` 15:00 | `99679c1`: request `all`, keep league 39; evidence watcher restarted 14:59 |
| 17 Oct 11:20–14:58 | The restart waited for approval, and the watch lapsed during the wait | No monitoring for about 3 h (nothing else went wrong) | Restart the watch before waiting on a decision |
| 17–18 Oct | Mac unplugged five times (longest 17:16–end on 18 Oct, down to 22%) | No downtime; low-battery alert added | Charger |
| 17 Oct 13:xx onwards | REST book snapshots returned `404` for settled markets, every minute | 38,697 error records; no data lost | To do: stop snapshot polling for a market after its settlement message |
| 18 Oct 22:35 | The recorder's timer excludes sleep, so it ran until 01:20 | 2¾ h more recording than planned | To do: end at a wall-clock deadline |
| 19 Oct | Full replay failed: the raw payload column passed 2 GB of 32-bit string offsets | Replay not yet produced | `3603be4`: 64-bit string offsets |
| 19 Oct | Replay killed for memory when run alongside paper trading | Replay not yet produced | Run analysis steps one at a time |

## Gate status

From [delivery-status.md](delivery-status.md), gate A (independent foundation):

| Requirement | Status |
|---|---|
| Live EPL IDs and reviewed rules | **Passed**: six families, rules signed off 16 Oct |
| 72 hours plus a matchday | **Span reached, gaps present**: 74.7 h wall clock including Saturday's five matches, but ≈ 2¾ h asleep. Strictly not passed; the cost in usable asset-time is pending `pilot-report` |
| Visible gaps | **Passed**: 844 gaps and 571 reconnects recorded, none filled |
| Restore test | **Passed**: 4,503,356 records restored and verified |
| Sports-stream join | **Pending replay**: 151,864 score messages received; whether they join to these fixtures is not yet checked |
| Measured storage and cost forecast | **Partly**: 6.7 GB per 30 days measured; `pml budget` with real quotes not yet run |

## Lessons

1. **A laptop is the weakest link.** Sleep, battery, VPN and home internet caused every collection gap. A small server where Polymarket and API-Football are both reachable would remove most of them ([roadmap](forecasting.md#improving-beyond-the-limitations), item 11).
2. **Mocks must match the real API.** The API-Football bug passed its test because the mock accepted a value the real service rejects. Make one live call against each new external query before relying on it.
3. **Approval latency is an operational risk.** A fix ready at 11:20 went live at 14:59. For a time-critical repair, agree in advance which restarts may proceed without a second approval.
4. **Analysis must scale with the capture.** A 72-hour capture is 15 times the one-hour check: the replay hit a string-size limit and memory pressure that small runs never showed.
5. **Backups after every slot worked.** Every forecast batch had a verified iCloud copy within about 2 minutes.

## Pending

1. Replay the full capture, then paper trading (taker, static, inventory), research with incident dossiers, and `pml pilot-report`. These run one at a time from `data/pilot-2026-10/post/run-post.sh`.
2. `pml forecast-evaluate` once football-data.co.uk publishes the 17–18 October results. Its file stopped at 12 October on 19 October.
3. Update [delivery-status.md](delivery-status.md) and [validation.md](validation.md), and finalise this report with the gate decision.
