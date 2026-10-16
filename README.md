# Forecast hash log

Each line of `hashes.jsonl` is pushed here as soon as `pml forecast-watch` freezes a pre-match forecast. The `sha256` is the hash of the full record in the local `forecasts.jsonl`, which also chains each record to the previous one. GitHub's push time is an independent timestamp: a record whose hash appears here before kickoff could not have been written after the result.

To verify: recompute each record's SHA-256 (canonical JSON without the `sha256` field, as in `prediction_market_lab.forecast.freeze`) and match it to the line here.
