import json
from datetime import timedelta
from decimal import Decimal

import pytest

from prediction_market_lab.evidence_sources import parse_feed
from prediction_market_lab.models import Evidence
from prediction_market_lab.paper import PaperBroker, PaperConfig
from prediction_market_lab.replay import ReplayEngine
from tests.conftest import START, raw, snapshot


def test_sell_cannot_remove_hedge_above_event_limit(contract):
	broker = PaperBroker(PaperConfig(event_risk_limit=Decimal("1")))
	broker.update_contract(contract)
	broker.update_contract(contract.model_copy(update={"asset_id": "no", "outcome": "No"}))
	broker.positions = {"a": Decimal(10), "no": Decimal(10)}
	broker.basis = {"a": Decimal(5), "no": Decimal(5)}
	assert broker.event_exposure("f") == 0
	order = broker.submit("a", "SELL", Decimal(10), Decimal(".5"), START)
	assert order.reason == "risk_limit_removing_hedge"


def test_canonical_hash_does_not_collapse_multiple_legs():
	engine = ReplayEngine()
	rows = [
		{
			"transaction_hash": "same-transaction",
			"token_id": asset,
			"condition_id": "c",
			"price": ".5",
			"size": "10",
			"side": "BUY",
			"timestamp": 1767225600,
		}
		for asset in ("a", "b")
	]
	engine.consume(
		raw(
			{"event_type": "canonical_trades", "response": {"data": rows}},
			source="canonical_trades",
		)
	)
	assert len(engine.canonical_trades) == 2
	assert (
		engine.canonical_trades[0]["candidate_trade_key"]
		!= engine.canonical_trades[1]["candidate_trade_key"]
	)
	assert not engine.broker.fills


def test_real_oi_not_volume():
	engine = ReplayEngine()
	engine.consume(
		raw(
			{
				"event_type": "open_interest",
				"response": {"data": [{"condition_id": "c", "value": "123.4"}]},
			},
			source="open_interest",
		)
	)
	assert engine.open_interest[0]["value_usdc"] == "123.4"
	engine.consume(
		raw(
			{
				"event_type": "open_interest",
				"response": {"data": [{"condition_id": "c", "volume": "999"}]},
			},
			1,
			source="open_interest",
		)
	)
	assert len(engine.open_interest) == 1
	assert engine.counts["rejected_analytics_row"] == 1


def test_score_corrections_preserved_and_reviewed_join(contract):
	engine = ReplayEngine()
	contract = contract.model_copy(update={"sports_fixture_id": "vendor-slug"})
	engine.consume(
		raw(
			{"event_type": "metadata", "contract": contract.model_dump(mode="json")},
			source="catalog",
		)
	)
	for seconds, score in ((1, "1-0"), (2, "0-0")):
		engine.consume(raw({"slug": "vendor-slug", "score": score}, seconds, source="sports"))
	scores = [e for e in engine.events if e["kind"] == "score"]
	assert [json.loads(e["data_json"])["score"] for e in scores] == ["1-0", "0-0"]
	assert all(e["fixture_id"] == "f" for e in scores)


def test_rss_keeps_observation_time_and_defaults_to_no_model_use():
	xml = b"<rss><channel><item><title>Official release</title><link>https://example.org/release</link><pubDate>Thu, 01 Jan 2026 00:00:00 GMT</pubDate></item></channel></rss>"
	items = parse_feed(
		xml,
		source_url="https://example.org/feed",
		fixture_id="f",
		observed_at=START + timedelta(seconds=20),
	)
	assert items[0].published_at == START
	assert items[0].first_seen == START + timedelta(seconds=20)
	assert not items[0].allow_model_input and not items[0].allow_redistribution
	with pytest.raises(ValueError):
		parse_feed(b"<!DOCTYPE entity>", source_url="x", fixture_id="f", observed_at=START)
	with pytest.raises(ValueError):
		Evidence(
			evidence_id="x", fixture_id="f", title="x", url="javascript:alert(1)", first_seen=START
		)


def test_unknown_wire_schema_preserved_as_unknown():
	engine = ReplayEngine()
	engine.consume(snapshot())
	engine.consume(raw({"event_type": "new_venue_message", "field": "value"}, 1))
	assert engine.counts["unknown"] == 1
	assert engine.books["a"].reason == "unknown_market_schema"


def test_paper_equity_decomposition(contract):
	engine = ReplayEngine(paper=PaperConfig(latency_ms=0))
	engine.consume(
		raw(
			{"event_type": "metadata", "contract": contract.model_dump(mode="json")},
			source="catalog",
		)
	)
	engine.consume(snapshot())
	engine.broker.submit("a", "BUY", Decimal(5), Decimal(".51"), START)
	engine.consume(snapshot(2))
	engine.broker.submit("a", "SELL", Decimal(2), Decimal(".49"), START + timedelta(seconds=2))
	engine.consume(snapshot(4))
	for settle in (False, True):
		if settle:
			engine.broker.settle("a", Decimal(1), START + timedelta(seconds=5))
		result = engine.broker.summary(engine.books)
		parts = sum(
			(
				Decimal(result[k])
				for k in ("realized_price_pnl", "settlement_pnl", "inventory_price_revaluation")
			),
			Decimal(0),
		) - Decimal(result["fees"])
		assert parts == Decimal(result["net_pnl"])


def test_completed_market_stops_coverage_denominator(contract):
	engine = ReplayEngine()
	engine.consume(
		raw(
			{"event_type": "metadata", "contract": contract.model_dump(mode="json")},
			source="catalog",
		)
	)
	engine.consume(snapshot())
	engine.consume(
		raw({"event_type": "settlement", "asset_id": "a", "payout": 1}, 10, source="resolution")
	)
	engine.consume(raw({"event_type": "unknown"}, 86400))
	engine.consume(snapshot(86401))
	assert max(row["received_at"] for row in engine.features) == START + timedelta(seconds=10)
	assert engine.books["a"].reason == "settled"


def test_changed_unreviewed_rules_excluded_from_research(contract):
	engine = ReplayEngine()
	engine.consume(
		raw(
			{"event_type": "metadata", "contract": contract.model_dump(mode="json")},
			source="catalog",
		)
	)
	engine.consume(snapshot())
	changed = contract.model_copy(
		update={
			"rules": "Qualification, not regulation result",
			"rules_reviewed": False,
			"known_at": START + timedelta(seconds=1),
		}
	)
	engine.consume(
		raw(
			{"event_type": "metadata", "contract": changed.model_dump(mode="json")},
			1,
			source="catalog",
		)
	)
	engine.consume(snapshot(2))
	assert engine.features[-1]["quality_reason"] == "rules_not_reviewed"
	assert engine.features[-1]["quality"] == "degraded"
