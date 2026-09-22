from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from prediction_market_lab.models import Contract, EvidenceClass, RawRecord
from prediction_market_lab.normalization import normalize

START = datetime(2026, 1, 1, tzinfo=UTC)


def raw(
	payload: Any,
	seconds: float = 0,
	*,
	session: str = "s",
	source: str = "market",
	sequence: int = 1,
) -> RawRecord:
	return RawRecord(
		record_id=f"{session}:{sequence}:{seconds}",
		source=source,
		session_id=session,
		sequence=sequence,
		received_at=START + timedelta(seconds=seconds),
		monotonic_ns=int(seconds * 1e9),
		payload=payload,
		provenance=EvidenceClass.SYNTHETIC,
	)


def snapshot(
	seconds: float = 0,
	*,
	asset: str = "a",
	bid: str = ".49",
	ask: str = ".51",
	size: str = "10",
	session: str = "s",
) -> RawRecord:
	return raw(
		{
			"event_type": "book",
			"asset_id": asset,
			"bids": [{"price": bid, "size": size}],
			"asks": [{"price": ask, "size": size}],
		},
		seconds,
		session=session,
	)


def event(payload: Any, seconds: float = 0, **kwargs: Any) -> Any:
	return normalize(raw(payload, seconds, **kwargs))[0]


@pytest.fixture
def contract() -> Contract:
	return Contract(
		asset_id="a",
		condition_id="c",
		event_id="e",
		fixture_id="f",
		market_id="m",
		label="Synthetic binary",
		outcome="Yes",
		category="macro",
		known_at=START,
		rules="Synthetic rules",
		rules_reviewed=True,
		tick_size=Decimal(".01"),
		min_order_size=Decimal("1"),
		fee_rate=Decimal(".02"),
		seconds_delay=1,
		accepting_orders=True,
		provenance=EvidenceClass.SYNTHETIC,
	)
