"""Bounded public-data capture with independent feeds and durable publication."""

import asyncio
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from prediction_market_lab.adapters import CLOB, DATA, GAMMA, PolymarketAdapter
from prediction_market_lab.config import CaptureConfig
from prediction_market_lab.models import Contract, RawRecord, utc_now
from prediction_market_lab.replay import ReplayEngine
from prediction_market_lab.storage import Catalog, RawArchive, atomic_write, canonical


async def collect(
	root: Path,
	contracts: list[Contract],
	duration: float,
	config: CaptureConfig,
	*,
	sports: bool = True,
) -> dict[str, Any]:
	if not contracts or len(contracts) > config.max_assets:
		raise ValueError("Capture requires a nonempty reviewed watchlist within the asset cap")
	if any(not c.rules_reviewed or c.known_at > utc_now() for c in contracts):
		raise ValueError("Watchlist rules must be reviewed and known now")
	assets = sorted({c.asset_id for c in contracts})
	queue: asyncio.Queue[RawRecord] = asyncio.Queue(config.queue_size)
	adapter = PolymarketAdapter()
	archive = RawArchive(root, config.segment_records)
	catalog = Catalog(root)
	start = time.monotonic()
	sequence = 0
	stop = asyncio.Event()
	reconnect = asyncio.Event()
	engine = ReplayEngine(retain=False)
	last_resync = 0.0
	trade_pages: dict[str, list[bytes]] = {}
	health: dict[str, Any] = {
		"started_at": utc_now().isoformat(),
		"records": 0,
		"reconnects": 0,
		"errors": {},
		"last_received": {},
		"assets": assets,
		"status": "running",
		"max_queue_depth": 0,
	}
	previous_wall, previous_mono = utc_now(), time.monotonic()

	async def emit(source: str, session: str, payload: Any, at: datetime | None = None) -> None:
		nonlocal sequence
		sequence += 1
		record = RawRecord(
			record_id=f"{session}:{sequence}",
			source=source,
			session_id=session,
			sequence=sequence,
			received_at=at or utc_now(),
			monotonic_ns=time.monotonic_ns(),
			payload=payload,
		)
		await queue.put(record)
		health["max_queue_depth"] = max(health["max_queue_depth"], queue.qsize())

	async def stream(source: str) -> None:
		failures = 0
		while not stop.is_set():
			session = uuid.uuid4().hex
			try:
				await emit(
					source,
					session,
					{
						"event_type": "gap",
						"reason": "new_connection",
						"assets": assets if source == "market" else [],
					},
				)
				async for frame in adapter.frames(assets, sports=source == "sports"):
					await emit(source, session, frame)
					failures = 0
					if stop.is_set():
						return
					if source == "market" and reconnect.is_set():
						reconnect.clear()
						health["reconnects"] += 1
						break
			except asyncio.CancelledError:
				raise
			except Exception as exc:
				failures += 1
				health["errors"][source] = type(exc).__name__ + ": " + str(exc)[:200]
				health["reconnects"] += 1
				await emit(
					source,
					session,
					{
						"event_type": "gap",
						"reason": type(exc).__name__,
						"assets": assets if source == "market" else [],
					},
				)
				await asyncio.sleep(min(30, 2 ** min(failures, 5)) + random.random())

	async def poll() -> None:
		session = uuid.uuid4().hex
		while not stop.is_set():
			requests: list[tuple[str, str, dict[str, Any] | None]] = [
				("analytics_status", DATA + "/v2/status", None),
				("server_time", CLOB + "/time", None),
			]
			for condition in sorted({c.condition_id for c in contracts}):
				requests.append(("open_interest", DATA + "/v2/oi", {"condition_id": condition}))
			for market_id in sorted({c.market_id for c in contracts}):
				requests.append(("metadata_snapshot", GAMMA + "/markets/" + market_id, None))
			for asset in assets:
				requests.append(("reconciliation", CLOB + "/book", {"token_id": asset}))
			for source, url, params in requests:
				if stop.is_set():
					return
				try:
					data = await adapter.get(url, params)
					await emit(
						source,
						session,
						{"event_type": source, "response": data, "params": params or {}},
					)
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					health["errors"][source] = type(exc).__name__ + ": " + str(exc)[:200]
					await emit(
						"health",
						session,
						{
							"event_type": "source_error",
							"source": source,
							"error": type(exc).__name__,
						},
					)
			for condition in sorted({c.condition_id for c in contracts}):
				try:
					known = trade_pages.get(condition)
					first = True
					async for page in adapter.trade_pages(condition, pages=3):
						rows = [canonical(row) for row in page["data"]]
						if first:
							first = False
							if rows == known:
								break
							trade_pages[condition] = rows
						await emit(
							"canonical_trades",
							session,
							{
								"event_type": "canonical_trades",
								"condition_id": condition,
								"response": page,
							},
						)
						if stop.is_set():
							return
						# Pages are newest first: an already archived row means older pages are known.
						if known and set(rows) & set(known):
							break
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					health["errors"]["canonical_trades"] = type(exc).__name__
			await asyncio.sleep(config.poll_seconds)

	tasks: list[asyncio.Task[None]] = []
	try:
		for contract in contracts:
			catalog.store_contract(contract)
			sequence += 1
			session = "catalog-" + uuid.uuid4().hex
			record = RawRecord(
				record_id=f"{session}:{sequence}",
				source="catalog",
				session_id=session,
				sequence=sequence,
				received_at=utc_now(),
				monotonic_ns=time.monotonic_ns(),
				payload={"event_type": "metadata", "contract": contract.model_dump(mode="json")},
			)
			archive.append(record)
			engine.consume(record)
		tasks = [asyncio.create_task(stream("market")), asyncio.create_task(poll())]
		if sports:
			tasks.append(asyncio.create_task(stream("sports")))
		last_flush = time.monotonic()
		while time.monotonic() - start < duration:
			try:
				record = await asyncio.wait_for(queue.get(), timeout=1)
				archive.append(record)
				try:
					engine.consume(record)
				except (ValueError, KeyError, TypeError) as exc:
					health["errors"]["normalization"] = type(exc).__name__
					reconnect.set()
				health["processing_lag_seconds"] = max(
					0, (utc_now() - record.received_at).total_seconds()
				)
				health["records"] += 1
				health["last_received"][record.source] = record.received_at.isoformat()
				queue.task_done()
			except TimeoutError:
				pass
			if time.monotonic() - last_flush >= config.flush_seconds:
				now, mono = utc_now(), time.monotonic()
				skew = abs((now - previous_wall).total_seconds() - (mono - previous_mono))
				if skew > config.max_clock_skew_seconds:
					reconnect.set()
				previous_wall, previous_mono = now, mono
				archive.flush()
				health["updated_at"] = now.isoformat()
				health["queue_depth"] = queue.qsize()
				health["books"] = {
					asset: {"quality": book.quality.value, "reason": book.reason}
					for asset, book in engine.books.items()
				}
				health["event_counts"] = dict(engine.counts)
				if (
					any(
						book.reason
						in {
							"delta_without_valid_snapshot",
							"unknown_market_schema",
							"out_of_order_source_time",
						}
						or book.reason.startswith("invalid_book")
						for book in engine.books.values()
					)
					and mono - last_resync > 60
				):
					reconnect.set()
					last_resync = mono
				health["raw_bytes"] = sum(p.stat().st_size for p in (root / "raw").glob("*"))
				health["elapsed_seconds"] = mono - start
				health["projected_30d_gb"] = (
					health["raw_bytes"] / max(1, mono - start) * 30 * 86400 / 1e9
				)
				atomic_write(root / "health.json", canonical(health))
				last_flush = mono
				if health["raw_bytes"] > config.max_raw_gb * 1e9:
					raise RuntimeError(
						"Raw storage budget reached; capture stopped without silent downsampling"
					)
		health["status"] = "completed"
	finally:
		stop.set()
		for task in tasks:
			task.cancel()
		await asyncio.gather(*tasks, return_exceptions=True)
		while not queue.empty():
			archive.append(queue.get_nowait())
			queue.task_done()
		archive.close()
		catalog.close()
		await adapter.close()
		health["ended_at"] = utc_now().isoformat()
		if health["status"] == "running":
			health["status"] = "interrupted"
		if not engine.counts.get("book"):
			# Keep "interrupted" visible; a run without books still fails qualification.
			health["no_book_data"] = True
			if health["status"] == "completed":
				health["status"] = "no_book_data"
		atomic_write(root / "health.json", canonical(health))
	return health
