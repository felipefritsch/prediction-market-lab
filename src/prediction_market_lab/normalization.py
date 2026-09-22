"""Translate raw wire observations without silently dropping unknown schemas."""

import json
from typing import Any

from prediction_market_lab.models import Event, RawRecord, timestamp


def normalize(record: RawRecord) -> list[Event]:
	payload = record.payload
	if isinstance(payload, str):
		if payload.upper() in {"PING", "PONG"}:
			payload = {"event_type": "heartbeat"}
		else:
			try:
				payload = json.loads(payload)
			except json.JSONDecodeError:
				payload = {"event_type": "unknown", "wire_text": payload}
	items = payload if isinstance(payload, list) else [payload]
	result: list[Event] = []
	for i, item in enumerate(items):
		if not isinstance(item, dict):
			item = {"event_type": "unknown", "value": item}
		kind = str(item.get("event_type", item.get("type", "")))
		body = item.get("payload", item)
		if not isinstance(body, dict):
			body = {"value": body}
		if record.source == "sports" and not kind:
			kind = "score"
		if not kind:
			kind = "unknown"
		known = {
			"book",
			"price_change",
			"last_trade_price",
			"trade",
			"tick_size_change",
			"best_bid_ask",
			"new_market",
			"market_resolved",
			"metadata",
			"metadata_snapshot",
			"gap",
			"heartbeat",
			"score",
			"settlement",
			"evidence",
			"source_error",
			"open_interest",
			"analytics_status",
			"server_time",
			"reconciliation",
			"canonical_trades",
			"sampled_prices",
			"unknown",
		}
		if kind not in known:
			body = {**body, "unrecognized_event_type": kind}
			kind = "unknown"
		try:
			source_at = timestamp(body.get("timestamp", body.get("last_update")))
		except (ValueError, OverflowError, TypeError):
			source_at = None
			kind = "rejected"
		entries: list[dict[str, Any]] = [body]
		if kind == "price_change":
			changes = body.get("price_changes", body.get("priceChanges"))
			if isinstance(changes, list) and all(isinstance(c, dict) for c in changes):
				entries = changes
			else:
				kind = "rejected"
		for j, data in enumerate(entries):
			asset = str(data.get("asset_id", data.get("token_id", data.get("tokenId", ""))))
			fixture = str(
				data.get(
					"fixture_id", data.get("gameId", data.get("game_id", data.get("slug", "")))
				)
			)
			result.append(
				Event(
					record_id=f"{record.record_id}:{i}:{j}",
					kind=kind,
					asset_id=asset,
					fixture_id=fixture,
					source=record.source,
					session_id=record.session_id,
					received_at=record.received_at,
					source_at=source_at,
					sequence=record.sequence,
					data=data,
					provenance=record.provenance,
				)
			)
	return result
