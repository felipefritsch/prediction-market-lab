from datetime import timedelta
from decimal import Decimal

import pytest

from prediction_market_lab.books import Book
from prediction_market_lab.models import Quality, timestamp
from prediction_market_lab.normalization import normalize
from prediction_market_lab.paper import PaperConfig
from prediction_market_lab.replay import ReplayEngine
from tests.conftest import START, event, raw, snapshot


def test_replacement_zero_duplicate_and_recovery():
	book = Book("a")
	book.apply(normalize(snapshot())[0])
	delta = event(
		{
			"event_type": "price_change",
			"price_changes": [{"asset_id": "a", "side": "BUY", "price": ".49", "size": "3"}],
		},
		1,
	)
	book.apply(delta)
	book.apply(delta)
	assert book.bids[Decimal(".49")] == 3
	assert book.last_ofi == 0
	book.apply(
		event(
			{
				"event_type": "price_change",
				"price_changes": [{"asset_id": "a", "side": "BUY", "price": ".49", "size": "0"}],
			},
			2,
		)
	)
	assert not book.bids and book.quality == Quality.DEGRADED
	book.apply(delta)
	assert book.quality == Quality.RECOVERING
	book.apply(normalize(snapshot(3, session="new"))[0])
	assert book.quality == Quality.USABLE and book.epoch == 2


@pytest.mark.parametrize("bid,ask", [(".52", ".51"), ("NaN", ".51"), ("-.1", ".51"), (".5", "1.1")])
def test_invalid_snapshot_is_not_usable(bid, ask):
	book = Book("a")
	book.apply(normalize(snapshot(bid=bid, ask=ask))[0])
	assert book.quality != Quality.USABLE


def test_unknown_retained_and_degrades():
	engine = ReplayEngine()
	engine.consume(snapshot())
	engine.consume(raw({"event_type": "unknown", "new_field": 123}, 1))
	assert engine.books["a"].quality == Quality.DEGRADED
	assert engine.events[-1]["data_json"].find("new_field") >= 0
	engine.consume(snapshot(2))
	assert engine.books["a"].quality == Quality.USABLE


def test_atomic_multilevel_delta_does_not_create_temporary_cross():
	engine = ReplayEngine()
	engine.consume(snapshot())
	engine.consume(
		raw(
			{
				"event_type": "price_change",
				"price_changes": [
					{"asset_id": "a", "side": "BUY", "price": ".52", "size": "5"},
					{"asset_id": "a", "side": "SELL", "price": ".51", "size": "0"},
					{"asset_id": "a", "side": "SELL", "price": ".54", "size": "5"},
				],
			},
			1,
		)
	)
	assert engine.books["a"].quality == Quality.USABLE
	assert engine.books["a"].midpoint == Decimal(".53")


def test_gap_requires_snapshot_and_heartbeat_is_not_quote_change():
	book = Book("a")
	book.apply(normalize(snapshot())[0])
	book.apply(event("PONG", 10))
	assert book.last_heartbeat == START + timedelta(seconds=10)
	assert book.last_quote_change == START
	book.apply(event({"event_type": "gap"}, 11))
	assert book.midpoint is None


def test_arrival_clock_cannot_use_future_book(contract):
	engine = ReplayEngine(paper=PaperConfig(latency_ms=250))
	engine.consume(
		raw(
			{"event_type": "metadata", "contract": contract.model_dump(mode="json")},
			source="catalog",
		)
	)
	engine.consume(snapshot(0.1))
	order = engine.broker.submit(
		"a", "BUY", Decimal(2), Decimal(".60"), START + timedelta(seconds=0.1)
	)
	engine.consume(snapshot(2, ask=".59"))
	assert order.status == "filled"
	assert engine.broker.fills[0]["price"] == "0.51"
	assert engine.broker.fills[0]["received_at"] == (START + timedelta(seconds=1.35)).isoformat()


def test_future_metadata_not_applied_and_score_not_strategy_input(contract):
	engine = ReplayEngine()
	future = contract.model_copy(update={"known_at": START + timedelta(seconds=10)})
	engine.consume(
		raw(
			{"event_type": "metadata", "contract": future.model_dump(mode="json")}, source="catalog"
		)
	)
	engine.consume(snapshot(1))
	assert not engine.contracts
	engine.consume(raw({"fixture_id": "f", "score": "1-0"}, 10, source="sports"))
	assert engine.contracts["a"].known_at == START + timedelta(seconds=10)
	assert engine.counts["score"] == 1
	assert engine.books["a"].midpoint == Decimal(".50")


def test_feed_silence_is_not_unchanged_quote():
	engine = ReplayEngine()
	engine.consume(snapshot())
	engine.consume(raw({"fixture_id": "f", "score": "0-0"}, 40, source="sports"))
	assert engine.books["a"].reason == "feed_silence"


def test_timestamps_require_timezone():
	with pytest.raises(ValueError):
		timestamp("2026-01-01T00:00:00")
	assert timestamp(1767225600000) == START
