"""Fully funded paper execution. This module has no venue order API."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from prediction_market_lab.books import Book, number
from prediction_market_lab.models import Contract, Event, Quality

ZERO = Decimal(0)


@dataclass
class PaperConfig:
	initial_cash: Decimal = Decimal("10000")
	event_risk_limit: Decimal = Decimal("100")
	portfolio_risk_limit: Decimal = Decimal("1000")
	latency_ms: int = 250
	cancel_latency_ms: int = 250
	order_size: Decimal = Decimal("5")
	queue_multiplier: Decimal = Decimal("1")

	def __post_init__(self) -> None:
		values = (
			self.initial_cash,
			self.event_risk_limit,
			self.portfolio_risk_limit,
			self.order_size,
		)
		if any(not value.is_finite() or value <= 0 for value in values):
			raise ValueError("Paper cash, risk limits and size must be finite and positive")
		if (
			self.latency_ms < 0
			or self.cancel_latency_ms < 0
			or not self.queue_multiplier.is_finite()
			or self.queue_multiplier < 0
		):
			raise ValueError("Paper latency and queue multiplier must be nonnegative")


@dataclass
class Order:
	order_id: str
	asset_id: str
	side: str
	quantity: Decimal
	remaining: Decimal
	limit: Decimal
	submitted_at: datetime
	ready_at: datetime
	kind: str = "FAK"
	status: str = "pending"
	queue_ahead: Decimal = ZERO
	cancel_at: datetime | None = None
	reason: str = ""
	metadata_version: str = ""


class PaperBroker:
	def __init__(self, config: PaperConfig | None = None) -> None:
		self.config = config or PaperConfig()
		self.cash = self.config.initial_cash
		self.positions: dict[str, Decimal] = {}
		self.basis: dict[str, Decimal] = {}
		self.gross_basis: dict[str, Decimal] = {}
		self.orders: list[Order] = []
		self.fills: list[dict[str, Any]] = []
		self.ledger: list[dict[str, Any]] = []
		self.contracts: dict[str, Contract] = {}
		self.fees = ZERO
		self.realized = ZERO
		self.realized_price_pnl = ZERO
		self.settlement_pnl = ZERO
		self.settled: set[str] = set()
		self._trades: set[str] = set()
		self._consumed: dict[tuple[str, datetime | None, str, Decimal], Decimal] = {}

	def update_contract(self, contract: Contract) -> None:
		self.contracts[contract.asset_id] = contract

	def _reserved_cash(self) -> Decimal:
		return sum(
			(
				self._reserve(o.remaining, o.limit, self.contracts[o.asset_id], o.kind)
				for o in self.orders
				if o.side == "BUY" and o.status in {"pending", "resting"}
			),
			ZERO,
		)

	@staticmethod
	def _reserve(quantity: Decimal, price: Decimal, contract: Contract, kind: str) -> Decimal:
		# On [0,1], p + r*p*(1-p) is increasing for fee rates r <= 1.
		rate = ZERO if kind == "MAKER" else contract.fee_rate
		rounding_buffer = (
			ZERO if not rate else Decimal(".00001") / (contract.tick_size or Decimal(".0001"))
		)
		return quantity * (price + (rate or ZERO) * price * (1 - price)) + rounding_buffer

	def event_exposure(
		self, fixture_id: str, extra_sell: tuple[str, Decimal] | None = None
	) -> Decimal:
		contracts = [c for c in self.contracts.values() if c.fixture_id == fixture_id]
		positions = self.positions.copy()
		costs = self.basis.copy()
		sells = [
			(o.asset_id, o.remaining)
			for o in self.orders
			if o.side == "SELL" and o.status in {"pending", "resting"}
		]
		if extra_sell:
			sells.append(extra_sell)
		for asset, quantity in sells:
			position = positions.get(asset, ZERO)
			if position:
				costs[asset] = costs.get(asset, ZERO) * max(ZERO, position - quantity) / position
				positions[asset] = max(ZERO, position - quantity)
		basis = sum((costs.get(c.asset_id, ZERO) for c in contracts), ZERO)
		reserved = sum(
			(
				self._reserve(o.remaining, o.limit, self.contracts[o.asset_id], o.kind)
				for o in self.orders
				if o.status in {"pending", "resting"}
				and o.side == "BUY"
				and self.contracts[o.asset_id].fixture_id == fixture_id
			),
			ZERO,
		)
		if contracts and all(c.rules_reviewed for c in contracts):
			football = all(c.regulation_time for c in contracts)
			states = ["home", "draw", "away"] if football else ["yes", "no"]
			payouts = []
			for state in states:
				payout = ZERO
				for c in contracts:
					wins = c.family_outcome == state if football else c.outcome.lower() == state
					if football and c.outcome.lower() == "no":
						wins = not wins
					payout += positions.get(c.asset_id, ZERO) * int(wins)
				payouts.append(payout)
			basis = max(ZERO, basis - min(payouts))
		return basis + reserved

	def total_exposure(self) -> Decimal:
		return sum(
			(self.event_exposure(f) for f in {c.fixture_id for c in self.contracts.values()}), ZERO
		)

	def submit(
		self,
		asset_id: str,
		side: str,
		quantity: Decimal,
		limit: Decimal,
		at: datetime,
		kind: str = "FAK",
	) -> Order:
		contract = self.contracts.get(asset_id)
		delay = (
			contract.seconds_delay
			if kind != "MAKER" and contract and contract.seconds_delay is not None
			else 0
		)
		order = Order(
			f"paper-{len(self.orders) + 1:06d}",
			asset_id,
			side,
			quantity,
			quantity,
			limit,
			at,
			at + timedelta(milliseconds=self.config.latency_ms, seconds=delay),
			kind,
		)
		reason = ""
		if kind not in {"FAK", "FOK", "MAKER"} or side not in {"BUY", "SELL"}:
			reason = "invalid_order_type"
		elif (
			not quantity.is_finite()
			or not limit.is_finite()
			or quantity <= 0
			or not ZERO < limit < 1
		):
			reason = "invalid_price_or_size"
		elif not contract or contract.known_at > at:
			reason = "metadata_unavailable_asof"
		elif (
			contract.fee_rate is None
			or contract.seconds_delay is None
			or contract.tick_size is None
			or contract.min_order_size is None
		):
			reason = "missing_execution_parameters"
		elif (
			not contract.rules_reviewed
			or contract.accepting_orders is not True
			or asset_id in self.settled
		):
			reason = "market_not_tradeable"
		elif (
			limit % contract.tick_size
			or quantity < contract.min_order_size
			or quantity % Decimal(".01")
		):
			reason = "tick_or_size_constraint"
		elif side == "SELL":
			reserved = sum(
				(
					o.remaining
					for o in self.orders
					if o.asset_id == asset_id
					and o.side == "SELL"
					and o.status in {"pending", "resting"}
				),
				ZERO,
			)
			if quantity > self.positions.get(asset_id, ZERO) - reserved:
				reason = "insufficient_inventory"
			else:
				projected = self.event_exposure(contract.fixture_id, (asset_id, quantity))
				if (
					projected > self.config.event_risk_limit
					or self.total_exposure() - self.event_exposure(contract.fixture_id) + projected
					> self.config.portfolio_risk_limit
				):
					reason = "risk_limit_removing_hedge"
		else:
			reserve = self._reserve(quantity, limit, contract, kind)
			if reserve > self.cash - self._reserved_cash():
				reason = "insufficient_cash"
			elif (
				self.event_exposure(contract.fixture_id) + reserve > self.config.event_risk_limit
				or self.total_exposure() + reserve > self.config.portfolio_risk_limit
			):
				reason = "risk_limit"
		if contract:
			order.metadata_version = contract.version
		if reason:
			order.status, order.reason = "rejected", reason
		self.orders.append(order)
		return order

	def cancel(self, order_id: str, at: datetime) -> None:
		order = next(o for o in self.orders if o.order_id == order_id)
		if order.status == "pending" and at < order.ready_at:
			# Delayed takers cannot be withdrawn while the venue delay is pending.
			if order.kind != "MAKER":
				return
		order.cancel_at = at + timedelta(milliseconds=self.config.cancel_latency_ms)

	def _fill(
		self, order: Order, price: Decimal, quantity: Decimal, at: datetime, maker: bool
	) -> None:
		contract = self.contracts[order.asset_id]
		assert contract.fee_rate is not None
		fee = ZERO if maker else quantity * contract.fee_rate * price * (1 - price)
		fee = (
			fee.quantize(Decimal(".00001"), rounding=ROUND_HALF_UP)
			if fee >= Decimal(".00001")
			else ZERO
		)
		notional = price * quantity
		position = self.positions.get(order.asset_id, ZERO)
		basis = self.basis.get(order.asset_id, ZERO)
		gross_basis = self.gross_basis.get(order.asset_id, ZERO)
		if order.side == "BUY":
			if notional + fee > self.cash:
				raise ValueError("Paper cash invariant violated")
			self.cash -= notional + fee
			self.positions[order.asset_id] = position + quantity
			self.basis[order.asset_id] = basis + notional + fee
			self.gross_basis[order.asset_id] = gross_basis + notional
		else:
			if quantity > position:
				raise ValueError("Paper inventory invariant violated")
			removed_basis = basis * quantity / position
			removed_gross = gross_basis * quantity / position
			self.cash += notional - fee
			self.positions[order.asset_id] = position - quantity
			self.basis[order.asset_id] = basis - removed_basis
			self.gross_basis[order.asset_id] = gross_basis - removed_gross
			self.realized += notional - fee - removed_basis
			self.realized_price_pnl += notional - removed_gross
		self.fees += fee
		order.remaining -= quantity
		fill = {
			"order_id": order.order_id,
			"asset_id": order.asset_id,
			"fixture_id": contract.fixture_id,
			"received_at": at.isoformat(),
			"side": order.side,
			"price": str(price),
			"quantity": str(quantity),
			"fee": str(fee),
			"maker": maker,
			"metadata_version": contract.version,
			"evidence_class": "simulated",
			"cash_after": str(self.cash),
		}
		self.fills.append(fill)
		self.ledger.append({"kind": "fill", **fill})
		if order.remaining == 0:
			order.status = "filled"

	def advance(self, at: datetime, books: dict[str, Book], *, inclusive: bool = True) -> None:
		for order in self.orders:
			if order.status not in {"pending", "resting"}:
				continue
			current = self.contracts.get(order.asset_id)
			if order.status == "resting" and (
				not current
				or current.accepting_orders is not True
				or not current.rules_reviewed
				or current.version != order.metadata_version
			):
				order.status, order.reason = "cancelled", "market_or_metadata_changed"
				continue
			if order.cancel_at and order.cancel_at <= at:
				order.status = "cancelled"
				continue
			if (
				order.status != "pending"
				or order.ready_at > at
				or (not inclusive and order.ready_at == at)
			):
				continue
			book = books.get(order.asset_id)
			contract = self.contracts.get(order.asset_id)
			if (
				not book
				or book.quality != Quality.USABLE
				or not contract
				or contract.accepting_orders is not True
				or order.asset_id in self.settled
			):
				order.status, order.reason = "rejected", "book_or_market_unavailable"
				continue
			if (
				contract.tick_size is None
				or order.limit % contract.tick_size
				or contract.fee_rate is None
				or contract.version != order.metadata_version
			):
				order.status, order.reason = "rejected", "changed_execution_parameters"
				continue
			if order.kind == "MAKER":
				crosses = (order.side == "BUY" and order.limit >= min(book.asks)) or (
					order.side == "SELL" and order.limit <= max(book.bids)
				)
				if crosses:
					order.status, order.reason = "rejected", "post_only_would_cross"
					continue
				levels = book.bids if order.side == "BUY" else book.asks
				own_ahead = sum(
					(
						o.remaining
						for o in self.orders
						if o is not order
						and o.status == "resting"
						and o.asset_id == order.asset_id
						and o.side == order.side
						and o.limit == order.limit
					),
					ZERO,
				)
				order.queue_ahead = (
					levels.get(order.limit, ZERO) * self.config.queue_multiplier + own_ahead
				)
				order.status = "resting"
				continue
			fills = []
			remaining = order.remaining
			for p, available in book.sweep(order.side, Decimal("1e30"), order.limit):
				key = (order.asset_id, book.received_at, order.side, p)
				q = min(remaining, max(ZERO, available - self._consumed.get(key, ZERO)))
				if q:
					fills.append((p, q, key))
					remaining -= q
				if not remaining:
					break
			if order.kind == "FOK" and remaining:
				order.status, order.reason = "cancelled", "insufficient_depth_fok"
				continue
			for p, q, key in fills:
				self._fill(order, p, q, order.ready_at, False)
				self._consumed[key] = self._consumed.get(key, ZERO) + q
			if order.remaining:
				order.status, order.reason = "cancelled", "unfilled_remainder_fak"

	def observe_trade(self, event: Event, book: Book | None) -> None:
		if event.record_id in self._trades:
			return
		self._trades.add(event.record_id)
		# A vendor's unverified `side` field is never silently treated as aggressor side.
		if (
			event.data.get("side_semantics") != "verified_aggressor"
			or not book
			or book.quality != Quality.USABLE
		):
			return
		try:
			price = number(event.data["price"], price=True)
			volume = number(event.data["size"])
		except (ValueError, KeyError):
			return
		for order in self.orders:
			if order.status != "resting" or order.asset_id != event.asset_id:
				continue
			contract = self.contracts[order.asset_id]
			if (
				contract.accepting_orders is not True
				or not contract.rules_reviewed
				or contract.version != order.metadata_version
			):
				order.status, order.reason = "cancelled", "market_or_metadata_changed"
				continue
			eligible_side = "SELL" if order.side == "BUY" else "BUY"
			if event.data.get("side") != eligible_side or price != order.limit:
				continue
			ahead = order.queue_ahead
			order.queue_ahead = max(ZERO, ahead - volume)
			quantity = min(order.remaining, max(ZERO, volume - ahead))
			if quantity:
				self._fill(order, order.limit, quantity, event.received_at, True)

	def settle(self, asset_id: str, payout: Decimal, at: datetime) -> None:
		if asset_id in self.settled:
			return
		if not ZERO <= payout <= 1:
			raise ValueError("Payout must be in [0, 1]")
		quantity = self.positions.get(asset_id, ZERO)
		amount = quantity * payout
		self.cash += amount
		self.realized += amount - self.basis.get(asset_id, ZERO)
		self.settlement_pnl += amount - self.gross_basis.get(asset_id, ZERO)
		self.positions[asset_id] = ZERO
		self.basis[asset_id] = ZERO
		self.gross_basis[asset_id] = ZERO
		self.settled.add(asset_id)
		for order in self.orders:
			if order.asset_id == asset_id and order.status in {"pending", "resting"}:
				order.status, order.reason = "cancelled", "settled"
		self.ledger.append(
			{
				"kind": "settlement",
				"asset_id": asset_id,
				"received_at": at.isoformat(),
				"payout": str(payout),
				"amount": str(amount),
				"evidence_class": "simulated",
			}
		)

	def summary(self, books: dict[str, Book]) -> dict[str, Any]:
		value, unknown = ZERO, []
		for asset, quantity in self.positions.items():
			if quantity:
				mid = books[asset].midpoint if asset in books else None
				if mid is None:
					unknown.append(asset)
				else:
					value += quantity * mid
		return {
			"evidence_class": "simulated",
			"cash": str(self.cash),
			"inventory_mid_value": str(value),
			"equity": str(self.cash + value) if not unknown else None,
			"net_pnl": str(self.cash + value - self.config.initial_cash) if not unknown else None,
			"fees": str(self.fees),
			"realized_pnl": str(self.realized),
			"realized_price_pnl": str(self.realized_price_pnl),
			"settlement_pnl": str(self.settlement_pnl),
			"inventory_price_revaluation": str(value - sum(self.gross_basis.values(), ZERO))
			if not unknown
			else None,
			"unrealized_pnl": str(value - sum(self.basis.values(), ZERO)) if not unknown else None,
			"turnover": str(
				sum((Decimal(f["price"]) * Decimal(f["quantity"]) for f in self.fills), ZERO)
			),
			"unpriced_positions": unknown,
			"fills": len(self.fills),
			"orders": len(self.orders),
			"exposure": str(self.total_exposure()),
			"passive_fill_assumption": "scenario_only_no_cancellation_credit",
		}


class Strategy:
	"""Small, declared baselines; no score/news input is accepted."""

	def __init__(self, name: str, interval_seconds: int = 30) -> None:
		if name not in {"taker", "static", "inventory"}:
			raise ValueError("Unknown strategy")
		self.name = name
		self.interval = interval_seconds
		self.last: dict[str, datetime] = {}

	def on_book(self, at: datetime, book: Book, broker: PaperBroker) -> None:
		contract = broker.contracts.get(book.asset_id)
		if book.quality != Quality.USABLE or not contract or not contract.tick_size:
			return
		if (
			book.asset_id in self.last
			and (at - self.last[book.asset_id]).total_seconds() < self.interval
		):
			return
		self.last[book.asset_id] = at
		for order in broker.orders:
			if order.asset_id == book.asset_id and order.status in {"pending", "resting"}:
				broker.cancel(order.order_id, at)
		bid, ask = max(book.bids), min(book.asks)
		position = broker.positions.get(book.asset_id, ZERO)
		q = broker.config.order_size
		if self.name == "taker":
			imbalance = (book.bids[bid] - book.asks[ask]) / (book.bids[bid] + book.asks[ask])
			if imbalance > Decimal("0.25"):
				broker.submit(book.asset_id, "BUY", q, ask, at)
			elif imbalance < Decimal("-0.25") and position >= q:
				broker.submit(book.asset_id, "SELL", q, bid, at)
		else:
			skew = contract.tick_size if self.name == "inventory" and position >= q * 2 else ZERO
			broker.submit(book.asset_id, "BUY", q, max(contract.tick_size, bid - skew), at, "MAKER")
			if position >= q:
				broker.submit(
					book.asset_id, "SELL", q, max(bid + contract.tick_size, ask - skew), at, "MAKER"
				)
