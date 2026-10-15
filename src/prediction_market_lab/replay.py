"""One causal clock and feature engine for recorded and live paper observations."""

import hashlib
import json
from collections import Counter, defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from prediction_market_lab.books import Book, number
from prediction_market_lab.models import Contract, Event, Quality, RawRecord, timestamp
from prediction_market_lab.normalization import normalize
from prediction_market_lab.paper import PaperBroker, PaperConfig, Strategy
from prediction_market_lab.storage import atomic_write, canonical, read_archive, write_table


def update_metadata(old: Contract, data: dict[str, Any], at: datetime) -> Contract:
	"""Changes become knowable on receipt; changed rules require another review."""
	rules = data.get("description", old.rules)
	fee = data.get("feeSchedule") or {}
	rate = fee.get("rate") if fee.get("exponent") == 1 else None
	if data.get("feesEnabled") is False:
		rate = "0"
	changes: dict[str, Any] = {
		"known_at": at,
		"rules": rules,
		"rules_reviewed": old.rules_reviewed and rules == old.rules,
		"fee_rate": rate,
		"seconds_delay": data.get("secondsDelay"),
		"accepting_orders": data.get("acceptingOrders"),
		"tick_size": data.get("orderPriceMinTickSize", data.get("minimumTickSize")),
		"min_order_size": data.get("orderMinSize", data.get("minimumOrderSize")),
	}
	if data.get("gameStartTime"):
		changes["kickoff"] = timestamp(data["gameStartTime"])
	return Contract.model_validate({**old.model_dump(), **changes})


class ReplayEngine:
	def __init__(
		self, strategy: str | None = None, paper: PaperConfig | None = None, *, retain: bool = True
	) -> None:
		self.books: dict[str, Book] = {}
		self.contracts: dict[str, Contract] = {}
		self.broker = PaperBroker(paper)
		self.strategy = Strategy(strategy) if strategy else None
		self.retain = retain
		self.features: list[dict[str, Any]] = []
		self.events: list[dict[str, Any]] = []
		self.contract_versions: list[dict[str, Any]] = []
		self.trades: list[dict[str, Any]] = []
		self.depth: list[dict[str, Any]] = []
		self.canonical_trades: list[dict[str, Any]] = []
		self.open_interest: list[dict[str, Any]] = []
		self.counts: Counter[str] = Counter()
		self.clock: datetime | None = None
		self.last_market_message: datetime | None = None
		self.ofi: dict[str, deque[tuple[datetime, float]]] = defaultdict(deque)
		self.trade_times: dict[str, deque[datetime]] = defaultdict(deque)
		self._trade_ids: set[str] = set()
		self._canonical_emitted: Counter[str] = Counter()
		self._pending_metadata: list[Contract] = []
		self._last_depth: dict[str, datetime] = {}

	def _contract(self, contract: Contract) -> None:
		old = self.contracts.get(contract.asset_id)
		if old and contract.known_at < old.known_at:
			self.counts["outdated_metadata"] += 1
			return
		self.contracts[contract.asset_id] = contract
		self.broker.update_contract(contract)
		if old and old.rules != contract.rules and contract.asset_id in self.books:
			self.books[contract.asset_id].invalidate("rules_changed")
		if self.retain:
			self.contract_versions.append(
				{**contract.model_dump(mode="json"), "version": contract.version}
			)
		if old and (old.rules != contract.rules or old.kickoff != contract.kickoff):
			self.counts["rules_or_schedule_changes"] += 1

	def _stale(self, at: datetime) -> None:
		if self.last_market_message and (at - self.last_market_message).total_seconds() > 35:
			for book in self.books.values():
				if book.quality == Quality.USABLE:
					book.invalidate("feed_silence", recovering=True)
					self._feature(book, at)
			self.counts["feed_silence"] += 1
			self.last_market_message = None

	def _advance(self, at: datetime, *, inclusive: bool) -> None:
		# Process timers at their own times against the latest THEN-known book.
		while True:
			due = []
			for order in self.broker.orders:
				if order.status == "pending":
					due.append(order.ready_at)
				if order.status in {"pending", "resting"} and order.cancel_at:
					due.append(order.cancel_at)
			eligible = [t for t in due if t < at or (inclusive and t == at)]
			if not eligible:
				break
			time = min(eligible)
			self._stale(time)
			self.broker.advance(time, self.books)

	def _feature(self, book: Book, at: datetime) -> None:
		contract = self.contracts.get(book.asset_id)
		if not contract or not self.retain:
			return
		row = book.features(at, contract.tick_size)
		if not contract.rules_reviewed:
			row.update(quality="degraded", quality_reason="rules_not_reviewed")
		if book.quality != Quality.USABLE:
			self.ofi[book.asset_id].clear()
		flow = self.ofi[book.asset_id]
		while flow and flow[0][0] < at - timedelta(seconds=10):
			flow.popleft()
		times = self.trade_times[book.asset_id]
		while times and times[0] < at - timedelta(seconds=60):
			times.popleft()
		row.update(
			{
				"fixture_id": contract.fixture_id,
				"category": contract.category,
				"competition": contract.competition,
				"outcome": contract.outcome,
				"family_outcome": contract.family_outcome,
				"kickoff": contract.kickoff,
				"metadata_version": contract.version,
				"tick_size": float(contract.tick_size) if contract.tick_size else None,
				"ofi_10s": sum(value for _, value in flow),
				"trade_notifications_60s": len(times),
				"seconds_to_kickoff": (contract.kickoff - at).total_seconds()
				if contract.kickoff
				else None,
				"evidence_class": "derived",
				"input_provenance": contract.provenance.value,
			}
		)
		self.features.append(row)
		if book.quality == Quality.USABLE and (
			book.asset_id not in self._last_depth
			or (at - self._last_depth[book.asset_id]).total_seconds() >= 10
		):
			for side, levels in (("BUY", book.bids), ("SELL", book.asks)):
				for price, quantity in levels.items():
					self.depth.append(
						{
							"asset_id": book.asset_id,
							"received_at": at,
							"side": side,
							"price": float(price),
							"quantity": float(quantity),
							"evidence_class": "derived",
						}
					)
			self._last_depth[book.asset_id] = at

	def consume(self, record: RawRecord) -> None:
		at = record.received_at
		if self.clock and at < self.clock:
			self.counts["receipt_clock_reversal"] += 1
			for book in self.books.values():
				book.invalidate("receipt_clock_reversal", recovering=True)
			at = self.clock
		self._advance(at, inclusive=False)
		self._stale(at)
		self.clock = at
		for contract in list(self._pending_metadata):
			if contract.known_at <= at:
				self._contract(contract)
				self._pending_metadata.remove(contract)
		touched: set[str] = set()
		normalized = normalize(record)
		batch_applied: set[str] = set()
		for event in normalized:
			if event.kind == "score":
				mapped = {
					c.fixture_id
					for c in self.contracts.values()
					if c.sports_fixture_id and c.sports_fixture_id == event.fixture_id
				}
				if len(mapped) == 1:
					event = event.model_copy(update={"fixture_id": mapped.pop()})
			# Archive receipt time remains unmodified in the event audit.
			self.counts[event.kind] += 1
			if self.retain:
				self.events.append(
					{
						"record_id": event.record_id,
						"kind": event.kind,
						"asset_id": event.asset_id,
						"fixture_id": event.fixture_id,
						"source": event.source,
						"received_at": event.received_at,
						"source_at": event.source_at,
						"data_json": json.dumps(event.data, sort_keys=True),
						"evidence_class": event.provenance.value,
					}
				)
			if event.source == "market" and event.kind != "gap":
				self.last_market_message = at
			if event.kind == "metadata":
				contract = Contract.model_validate(event.data["contract"])
				if contract.known_at <= at:
					self._contract(contract)
				else:
					self._pending_metadata.append(contract)
			elif event.kind == "metadata_snapshot":
				data = event.data["response"]
				for contract in list(self.contracts.values()):
					if contract.market_id == str(data.get("id")):
						self._contract(update_metadata(contract, data, at))
			elif event.kind in {"open_interest", "canonical_trades"}:
				self._auxiliary(event)
			elif event.kind in {"gap", "rejected"}:
				if event.source == "market":
					for asset in event.data.get("assets", list(self.books)):
						if asset in self.broker.settled:
							continue
						book = self.books.setdefault(asset, Book(asset))
						book.invalidate(str(event.data.get("reason", event.kind)), recovering=True)
						touched.add(asset)
			elif event.kind in {"book", "price_change"}:
				if event.asset_id in self.broker.settled:
					self.counts["post_settlement_book_message"] += 1
					continue
				if event.kind == "price_change":
					if event.asset_id in batch_applied:
						continue
					batch_applied.add(event.asset_id)
					event = event.model_copy(
						update={
							"data": {
								"_level_updates": [
									e.data
									for e in normalized
									if e.kind == "price_change" and e.asset_id == event.asset_id
								]
							}
						}
					)
				book = self.books.setdefault(event.asset_id, Book(event.asset_id))
				book.apply(event)
				self.ofi[event.asset_id].append((at, float(book.last_ofi)))
				touched.add(event.asset_id)
			elif event.kind == "tick_size_change" and event.asset_id in self.contracts:
				old = self.contracts[event.asset_id]
				self._contract(
					Contract.model_validate(
						{
							**old.model_dump(),
							"known_at": at,
							"tick_size": event.data["new_tick_size"],
						}
					)
				)
			elif event.kind in {"last_trade_price", "trade"}:
				self._trade(event)
			elif event.kind == "settlement":
				self.broker.settle(event.asset_id, Decimal(str(event.data["payout"])), at)
				if event.asset_id in self.books:
					self.books[event.asset_id].invalidate("settled")
					touched.add(event.asset_id)
			elif event.kind == "market_resolved":
				winner = str(event.data.get("winning_asset_id", ""))
				winning_contract = self.contracts.get(winner)
				if winning_contract:
					for contract in self.contracts.values():
						if contract.condition_id == winning_contract.condition_id:
							self.broker.settle(
								contract.asset_id, Decimal(int(contract.asset_id == winner)), at
							)
							if contract.asset_id in self.books:
								self.books[contract.asset_id].invalidate("settled")
								touched.add(contract.asset_id)
			elif event.kind == "unknown" and event.source == "market":
				for asset, book in self.books.items():
					if asset in self.broker.settled:
						continue
					book.invalidate("unknown_market_schema")
					touched.add(asset)
		# A multi-level wire update produces one feature/decision per asset.
		for asset in sorted(touched):
			self._feature(self.books[asset], at)
		self._advance(at, inclusive=True)
		if self.strategy:
			for asset in sorted(touched):
				self.strategy.on_book(at, self.books[asset], self.broker)
			self._advance(at, inclusive=True)

	def _trade(self, event: Event) -> None:
		# Source namespaces remain distinct; never deduplicate solely by transaction hash.
		data = event.data
		try:
			price, size = number(data["price"], price=True), number(data["size"])
		except (ValueError, KeyError, TypeError):
			self.counts["rejected_trade_notification"] += 1
			return
		identity = str(data.get("trade_id") or data.get("id") or "")
		if identity:
			key = event.source + ":" + identity
			if key in self._trade_ids:
				self.counts["duplicate_trades"] += 1
				return
			self._trade_ids.add(key)
		elif event.kind == "last_trade_price":
			self.counts["notifications_without_stable_id"] += 1
		self.trade_times[event.asset_id].append(event.received_at)
		if self.retain:
			self.trades.append(
				{
					"asset_id": event.asset_id,
					"received_at": event.received_at,
					"source_at": event.source_at,
					"source": event.source,
					"kind": event.kind,
					"price": float(price),
					"size": float(size),
					"side": data.get("side"),
					"side_semantics": data.get("side_semantics", "unverified"),
					"identity_quality": "stable_source_id"
					if identity
					else "notification_no_dedup_claim",
					"evidence_class": event.provenance.value,
				}
			)
		# Ambiguous duplicate notifications cannot create a passive fill.
		if identity:
			self.broker.observe_trade(
				event.model_copy(update={"record_id": event.source + ":" + identity}),
				self.books.get(event.asset_id),
			)

	def _auxiliary(self, event: Event) -> None:
		response = event.data["response"]
		rows = response.get("data", []) if isinstance(response, dict) else response
		if not isinstance(rows, list):
			self.counts["unknown_analytics_schema"] += 1
			return
		page_counts: Counter[str] = Counter()
		for row in rows:
			try:
				if event.kind == "open_interest":
					value = number(row["value"])
					if self.retain:
						self.open_interest.append(
							{
								"condition_id": row.get("condition_id"),
								"value_usdc": str(value),
								"received_at": event.received_at,
								"source": event.source,
								"evidence_class": event.provenance.value,
							}
						)
				elif self.retain:
					identity_fields = {
						k: row.get(k)
						for k in (
							"transaction_hash",
							"token_id",
							"condition_id",
							"side",
							"price",
							"size",
							"timestamp",
							"proxy_wallet",
							"log_index",
							"trade_index",
						)
					}
					candidate = hashlib.sha256(canonical(identity_fields)).hexdigest()
					# A refetched page repeats rows; only twins within one page are distinct candidates.
					page_counts[candidate] += 1
					if page_counts[candidate] <= self._canonical_emitted[candidate]:
						continue
					self._canonical_emitted[candidate] += 1
					self.canonical_trades.append(
						{
							"asset_id": str(row["token_id"]),
							"condition_id": str(row["condition_id"]),
							"received_at": event.received_at,
							"source_at": timestamp(row["timestamp"]),
							"price": str(number(row["price"], price=True)),
							"size": str(number(row["size"])),
							"side": row["side"],
							"candidate_trade_key": candidate,
							"identity_quality": "composite_candidate_not_guaranteed_unique",
							"page_record_id": event.record_id,
							"source": event.source,
							"evidence_class": event.provenance.value,
						}
					)
			except (ValueError, KeyError, TypeError):
				self.counts["rejected_analytics_row"] += 1

	def finish(self) -> dict[str, Any]:
		for order in self.broker.orders:
			if order.status in {"pending", "resting"}:
				order.status, order.reason = "censored", "recording_ended"
		return {
			"events": dict(sorted(self.counts.items())),
			"feature_rows": len(self.features),
			"assets": len(self.contracts),
			"paper": self.broker.summary(self.books),
		}


def replay(
	capture: Path,
	output: Path,
	*,
	strategy: str | None = None,
	paper: PaperConfig | None = None,
	since: datetime | None = None,
	until: datetime | None = None,
	max_records: int = 250000,
) -> dict[str, Any]:
	if output.exists():
		raise FileExistsError("Choose a new run directory; committed runs are immutable")
	output.mkdir(parents=True)
	engine = ReplayEngine(None if since else strategy, paper, retain=since is None)
	digest = hashlib.sha256()
	count = 0
	retained = 0
	for record in read_archive(capture):
		if until and record.received_at > until:
			break
		if since and record.received_at >= since and not engine.retain:
			engine.retain = True
			engine.strategy = Strategy(strategy) if strategy else None
			engine.contract_versions = [
				{**c.model_dump(mode="json"), "version": c.version}
				for c in engine.contracts.values()
			]
		if engine.retain:
			retained += 1
			if retained > max_records:
				raise ValueError(
					"Research window exceeds --max-records; choose --from/--until or explicitly raise the memory bound"
				)
		digest.update(record.model_dump_json().encode())
		engine.consume(record)
		count += 1
	if not count or not retained:
		raise ValueError("Capture contains no committed observations")
	summary = engine.finish()
	for name, rows in (
		("features", engine.features),
		("events", engine.events),
		("contracts", engine.contract_versions),
		("trades", engine.trades),
		("depth", engine.depth),
		("orders", [asdict(o) for o in engine.broker.orders]),
		("fills", engine.broker.fills),
		("ledger", engine.broker.ledger),
		("canonical_trades", engine.canonical_trades),
		("open_interest", engine.open_interest),
	):
		write_table(output, name, rows)
	atomic_write(output / "summary.json", canonical(summary))
	manifest = {
		"schema_version": 1,
		"input_sha256": digest.hexdigest(),
		"raw_records": count,
		"window_start": since.isoformat() if since else None,
		"window_end": until.isoformat() if until else None,
		"retained_raw_records": retained,
		"source_sha256": hashlib.sha256(
			b"".join(
				p.name.encode() + p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py"))
			)
		).hexdigest(),
		"strategy": strategy,
		"paper_config": asdict(engine.broker.config),
		"code_version": "0.1.0",
		"clock": "journal_arrival_order; timers before next observation; ties observations before timers",
		"files": {
			str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
			for p in sorted(output.rglob("*"))
			if p.is_file()
		},
	}
	atomic_write(output / "manifest.json", canonical(manifest))
	return summary
