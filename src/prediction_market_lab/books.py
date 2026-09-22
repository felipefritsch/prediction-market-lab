"""Aggregate L2 reconstruction and causal feature measurements."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from prediction_market_lab.models import Event, Quality


def number(value: Any, *, price: bool = False) -> Decimal:
	try:
		result = Decimal(str(value))
	except InvalidOperation as exc:
		raise ValueError("Invalid decimal") from exc
	if not result.is_finite() or result < 0 or (price and result > 1):
		raise ValueError("Invalid probability or quantity")
	return result


@dataclass
class Book:
	asset_id: str
	bids: dict[Decimal, Decimal] = field(default_factory=dict)
	asks: dict[Decimal, Decimal] = field(default_factory=dict)
	quality: Quality = Quality.RECOVERING
	reason: str = "awaiting_snapshot"
	session_id: str = ""
	source_at: datetime | None = None
	received_at: datetime | None = None
	last_quote_change: datetime | None = None
	last_heartbeat: datetime | None = None
	epoch: int = 0
	previous_top: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
	last_ofi: Decimal = Decimal(0)

	def invalidate(self, reason: str, *, recovering: bool = False) -> None:
		self.quality = Quality.RECOVERING if recovering else Quality.DEGRADED
		self.reason = reason
		self.previous_top = None
		self.last_ofi = Decimal(0)

	def apply(self, event: Event) -> bool:
		if event.kind == "gap":
			self.invalidate(str(event.data.get("reason", "gap")), recovering=True)
			return False
		if event.kind == "heartbeat":
			self.last_heartbeat = event.received_at
			return False
		if event.kind not in {"book", "price_change"}:
			return False
		if event.kind == "price_change" and (
			self.quality != Quality.USABLE or self.session_id != event.session_id
		):
			self.invalidate("delta_without_valid_snapshot", recovering=True)
			return False
		if (
			event.source_at
			and self.source_at
			and event.session_id == self.session_id
			and event.source_at < self.source_at
		):
			self.invalidate("out_of_order_source_time")
			return False
		try:
			bids, asks = self.bids.copy(), self.asks.copy()
			if event.kind == "book":
				bids, asks = {}, {}
				for name, levels in (("bids", bids), ("asks", asks)):
					for level in event.data[name]:
						p, q = number(level["price"], price=True), number(level["size"])
						if q:
							if p in levels:
								raise ValueError("Duplicate price level")
							levels[p] = q
			else:
				for update in event.data.get("_level_updates", [event.data]):
					side = update["side"].upper()
					if side not in {"BUY", "SELL"}:
						raise ValueError("Unknown book side")
					levels = bids if side == "BUY" else asks
					p, q = number(update["price"], price=True), number(update["size"])
					if q:
						levels[p] = q
					else:
						levels.pop(p, None)
			if bids and asks and max(bids) >= min(asks):
				raise ValueError("Crossed or locked book")
		except (ValueError, KeyError, TypeError, AttributeError) as exc:
			self.invalidate("invalid_book:" + str(exc))
			return False
		was_valid = self.quality == Quality.USABLE and self.session_id == event.session_id
		changed = bids != self.bids or asks != self.asks
		self.bids, self.asks = bids, asks
		if not was_valid:
			self.epoch += 1
			self.previous_top = None
		self.session_id = event.session_id
		self.source_at = event.source_at or self.source_at
		self.received_at = event.received_at
		if changed or self.last_quote_change is None:
			self.last_quote_change = event.received_at
		self.quality = Quality.USABLE if bids and asks else Quality.DEGRADED
		self.reason = "" if bids and asks else "one_sided_book"
		self.last_ofi = Decimal(0)
		if self.quality == Quality.USABLE:
			top = (max(bids), bids[max(bids)], min(asks), asks[min(asks)])
			if self.previous_top:
				bp, bq, ap, aq = top
				obp, obq, oap, oaq = self.previous_top
				self.last_ofi = (
					(bq if bp >= obp else Decimal(0))
					- (obq if bp <= obp else Decimal(0))
					- (aq if ap <= oap else Decimal(0))
					+ (oaq if ap >= oap else Decimal(0))
				)
			self.previous_top = top
		else:
			self.previous_top = None
		return True

	@property
	def midpoint(self) -> Decimal | None:
		if self.quality != Quality.USABLE:
			return None
		return (max(self.bids) + min(self.asks)) / 2

	def sweep(self, side: str, quantity: Decimal, limit: Decimal) -> list[tuple[Decimal, Decimal]]:
		if self.quality != Quality.USABLE:
			return []
		levels = self.asks if side == "BUY" else self.bids
		remaining, fills = quantity, []
		for p in sorted(levels, reverse=side == "SELL"):
			if (side == "BUY" and p > limit) or (side == "SELL" and p < limit):
				break
			q = min(remaining, levels[p])
			fills.append((p, q))
			remaining -= q
			if remaining <= 0:
				break
		return fills

	def features(self, at: datetime, tick_size: Decimal | None) -> dict[str, Any]:
		base: dict[str, Any] = {
			"asset_id": self.asset_id,
			"received_at": at,
			"quality": self.quality.value,
			"quality_reason": self.reason,
			"epoch": self.epoch,
		}
		if self.quality != Quality.USABLE:
			return base
		bid, ask = max(self.bids), min(self.asks)
		bq, aq = self.bids[bid], self.asks[ask]
		mid = (bid + ask) / 2
		base.update(
			{
				"bid": float(bid),
				"ask": float(ask),
				"bid_size": float(bq),
				"ask_size": float(aq),
				"midpoint": float(mid),
				"spread": float(ask - bid),
				"spread_ticks": float((ask - bid) / tick_size) if tick_size else None,
				"imbalance": float((bq - aq) / (bq + aq)),
				"weighted_midpoint": float((ask * bq + bid * aq) / (bq + aq)),
				"ofi": float(self.last_ofi),
				"bid_depth_2pp": float(
					sum(q for p, q in self.bids.items() if p >= mid - Decimal("0.02"))
				),
				"ask_depth_2pp": float(
					sum(q for p, q in self.asks.items() if p <= mid + Decimal("0.02"))
				),
				"quote_age_seconds": (at - self.last_quote_change).total_seconds()
				if self.last_quote_change
				else None,
			}
		)
		for quantity in (10, 100):
			fills = self.sweep("BUY", Decimal(quantity), Decimal(1))
			total = sum(q for _, q in fills)
			base[f"buy_{quantity}_vwap"] = (
				float(sum(p * q for p, q in fills) / total) if total == quantity else None
			)
		return base
