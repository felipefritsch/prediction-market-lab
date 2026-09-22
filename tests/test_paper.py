from datetime import timedelta
from decimal import Decimal

import pytest

from prediction_market_lab.books import Book
from prediction_market_lab.normalization import normalize
from prediction_market_lab.paper import PaperBroker, PaperConfig
from tests.conftest import START, event, snapshot


def setup_broker(contract, **config):
	broker = PaperBroker(PaperConfig(**config))
	broker.update_contract(contract)
	book = Book("a")
	book.apply(normalize(snapshot())[0])
	return broker, {"a": book}


def test_delay_fee_partial_fill_cash_and_settlement(contract):
	broker, books = setup_broker(contract)
	order = broker.submit("a", "BUY", Decimal(12), Decimal(".51"), START)
	broker.advance(START + timedelta(seconds=1), books)
	assert not broker.fills
	broker.advance(START + timedelta(seconds=2), books)
	assert order.remaining == 2 and order.status == "cancelled"
	assert broker.positions["a"] == 10
	assert broker.fees == Decimal(".04998")
	assert broker.cash == Decimal("9994.85002")
	broker.settle("a", Decimal(".5"), START + timedelta(seconds=3))
	broker.settle("a", Decimal(".5"), START + timedelta(seconds=3))
	assert broker.cash == Decimal("9999.85002")
	assert broker.summary(books)["net_pnl"] == str(broker.realized)


def test_fok_never_partially_fills(contract):
	broker, books = setup_broker(contract)
	order = broker.submit("a", "BUY", Decimal(12), Decimal(".51"), START, "FOK")
	broker.advance(START + timedelta(seconds=2), books)
	assert order.status == "cancelled" and not broker.fills
	assert broker.cash == 10000


def test_shared_depth_not_double_spent(contract):
	broker, books = setup_broker(contract)
	for _ in range(2):
		broker.submit("a", "BUY", Decimal(8), Decimal(".51"), START)
	broker.advance(START + timedelta(seconds=2), books)
	assert broker.positions["a"] == 10


def test_unfunded_sell_and_risk_limit(contract):
	broker, _ = setup_broker(contract, event_risk_limit=Decimal(1))
	assert (
		broker.submit("a", "SELL", Decimal(1), Decimal(".49"), START).reason
		== "insufficient_inventory"
	)
	assert broker.submit("a", "BUY", Decimal(3), Decimal(".51"), START).reason == "risk_limit"


@pytest.mark.parametrize("field", ["fee_rate", "seconds_delay", "tick_size", "min_order_size"])
def test_missing_parameters_forbid_cost_claim(contract, field):
	broker, _ = setup_broker(contract.model_copy(update={field: None}))
	assert (
		broker.submit("a", "BUY", Decimal(1), Decimal(".51"), START).reason
		== "missing_execution_parameters"
	)


def test_passive_queue_no_cancel_credit_and_dedup(contract):
	broker, books = setup_broker(contract, latency_ms=0)
	first = broker.submit("a", "BUY", Decimal(4), Decimal(".49"), START, "MAKER")
	second = broker.submit("a", "BUY", Decimal(4), Decimal(".49"), START, "MAKER")
	broker.advance(START, books)
	assert first.queue_ahead == 10 and second.queue_ahead == 14
	books["a"].bids[Decimal(".49")] = Decimal(1)
	trade = event(
		{
			"event_type": "trade",
			"asset_id": "a",
			"price": ".49",
			"size": "16",
			"side": "SELL",
			"side_semantics": "verified_aggressor",
		},
		1,
	)
	broker.observe_trade(trade, books["a"])
	broker.observe_trade(trade, books["a"])
	assert broker.positions["a"] == 6
	assert first.status == "filled" and second.remaining == 2
	assert broker.fees == 0


def test_unverified_side_never_fills_passive_order(contract):
	broker, books = setup_broker(contract, latency_ms=0)
	order = broker.submit("a", "BUY", Decimal(1), Decimal(".49"), START, "MAKER")
	broker.advance(START, books)
	broker.observe_trade(
		event(
			{"event_type": "trade", "asset_id": "a", "price": ".49", "size": "100", "side": "SELL"},
			1,
		),
		books["a"],
	)
	assert order.remaining == 1


def test_cancel_latency_and_trade_before_cancellation(contract):
	broker, books = setup_broker(contract, latency_ms=0, cancel_latency_ms=250)
	order = broker.submit("a", "BUY", Decimal(1), Decimal(".49"), START, "MAKER")
	broker.advance(START, books)
	broker.cancel(order.order_id, START)
	broker.advance(START + timedelta(milliseconds=100), books)
	assert order.status == "resting"
	broker.advance(START + timedelta(milliseconds=250), books)
	assert order.status == "cancelled"


def test_market_closure_rejects_pending_taker(contract):
	broker, books = setup_broker(contract)
	order = broker.submit("a", "BUY", Decimal(1), Decimal(".51"), START)
	broker.update_contract(contract.model_copy(update={"accepting_orders": False}))
	broker.advance(START + timedelta(seconds=2), books)
	assert order.status == "rejected"


@pytest.mark.parametrize("quantity", [Decimal("NaN"), Decimal("Infinity"), Decimal("1.001")])
def test_invalid_or_overprecise_order_sizes(contract, quantity):
	broker, _ = setup_broker(contract)
	assert broker.submit("a", "BUY", quantity, Decimal(".5"), START).status == "rejected"
