"""Read-only Polymarket adapter. No secure client, wallet or order endpoint."""

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import websockets

from prediction_market_lab.models import Contract, timestamp, utc_now

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS = "wss://sports-api.polymarket.com/ws"


class PolymarketAdapter:
	def __init__(self, http: httpx.AsyncClient | None = None) -> None:
		self.http = http or httpx.AsyncClient(timeout=25, follow_redirects=False)
		self._owned = http is None
		self._limiter = asyncio.Lock()
		self._last_request = 0.0

	async def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
		if not any(url.startswith(base + "/") for base in (GAMMA, DATA, CLOB)):
			raise ValueError("Adapter only supports public Polymarket hosts")
		for attempt in range(4):
			async with self._limiter:
				await asyncio.sleep(max(0, 0.25 - (time.monotonic() - self._last_request)))
				self._last_request = time.monotonic()
			try:
				response = await self.http.get(url, params=params)
			except httpx.TransportError:
				if attempt == 3:
					raise
				await asyncio.sleep(2**attempt)
				continue
			if response.status_code == 429 or response.status_code >= 500:
				if attempt < 3:
					retry = response.headers.get("Retry-After", str(2**attempt))
					await asyncio.sleep(min(float(retry) if retry.isdigit() else 2**attempt, 60))
					continue
			response.raise_for_status()
			return response.json()
		raise RuntimeError("Exhausted request attempts")

	async def discover(self, *, sport: str | None = None, pages: int = 2) -> dict[str, Any]:
		from polymarket import AsyncPublicClient

		if not 1 <= pages <= 20:
			raise ValueError("Discovery is bounded to 1–20 pages")
		sports = await self.get(GAMMA + "/sports")
		filters: dict[str, Any] = {"closed": False, "page_size": 20}
		if sport:
			matches = [s for s in sports if str(s.get("sport", "")).lower() == sport.lower()]
			if len(matches) != 1:
				raise ValueError(
					"Select an exact discovered sport abbreviation; inspect `pml sports`"
				)
			filters["series_ids"] = [int(x) for x in str(matches[0]["series"]).split(",")]
		events: list[dict[str, Any]] = []
		async with AsyncPublicClient() as sdk:
			count = 0
			async for page in sdk.list_events(**filters):
				for event in page.items:
					# Preserve unnormalized metadata as well as using the supported SDK paginator.
					events.append(await self.get(GAMMA + "/events/" + str(event.id)))
				count += 1
				if count >= pages:
					break
		return {
			"known_at": utc_now().isoformat(),
			"sdk": "polymarket-client==0.10.0",
			"sports": sports,
			"events": events,
			"scan_complete": count < pages,
			"pages_requested": pages,
		}

	async def trade_pages(
		self, condition: str, *, pages: int = 10, cursor: str | None = None
	) -> AsyncIterator[dict[str, Any]]:
		for _ in range(pages):
			params: dict[str, Any] = {
				"condition_id": condition,
				"taker_only": "true",
				"limit": 1000,
			}
			if cursor:
				params["cursor"] = cursor
			page = await self.get(DATA + "/v2/trades", params)
			if not isinstance(page, dict) or not isinstance(page.get("data"), list):
				raise ValueError("Unexpected trade-page schema")
			yield page
			next_cursor = page.get("pagination", {}).get("next_cursor")
			if not next_cursor:
				return
			if next_cursor == cursor:
				raise ValueError("Trade cursor did not advance")
			cursor = next_cursor

	async def prices(
		self,
		asset_id: str,
		start: int,
		end: int,
		bucket_seconds: int = 300,
		cursor: str | None = None,
	) -> Any:
		if end <= start or end - start > 15 * 86400:
			raise ValueError("Price-history windows must be positive and no longer than 15 days")
		params: dict[str, Any] = {
			"token_id": asset_id,
			"start": start,
			"end": end,
			"bucket_seconds": bucket_seconds,
			"limit": 1000,
		}
		if cursor:
			params["cursor"] = cursor
		return await self.get(DATA + "/v2/prices-history", params)

	async def frames(self, assets: list[str], *, sports: bool = False) -> AsyncIterator[str]:
		uri = SPORTS_WS if sports else MARKET_WS
		async with websockets.connect(
			uri,
			ping_interval=20,
			ping_timeout=20,
			max_size=8 * 1024 * 1024,
			max_queue=64,
			open_timeout=20,
		) as connection:
			if not sports:
				await connection.send(
					json.dumps(
						{
							"assets_ids": assets,
							"type": "market",
							"initial_dump": True,
							"custom_feature_enabled": True,
						}
					)
				)

			async def heartbeat() -> None:
				while True:
					await asyncio.sleep(10)
					if not sports:
						await connection.send("PING")

			pulse = asyncio.create_task(heartbeat())
			try:
				while True:
					frame = await asyncio.wait_for(connection.recv(), timeout=35)
					text = frame.decode() if isinstance(frame, bytes) else frame
					if sports and text == "ping":
						await connection.send("pong")
					yield text
			finally:
				pulse.cancel()
				await asyncio.gather(pulse, return_exceptions=True)

	async def close(self) -> None:
		if self._owned:
			await self.http.aclose()


def array(value: Any) -> list[Any]:
	if isinstance(value, str):
		value = json.loads(value)
	if not isinstance(value, list):
		raise ValueError("Expected an array")
	return value


def contracts_from_discovery(document: dict[str, Any], reviews: dict[str, Any]) -> list[Contract]:
	known_at = timestamp(document["known_at"])
	assert known_at is not None
	contracts = []
	for event in document["events"]:
		for market in event.get("markets", []):
			condition = str(market.get("conditionId", ""))
			review = reviews.get(condition)
			if not review or not review.get("fixture_id") or not review.get("reviewed_by"):
				continue
			rules = market.get("description", "")
			rules_hash = hashlib.sha256(rules.encode()).hexdigest()
			reviewed_at = timestamp(review.get("reviewed_at"))
			approved = (
				review.get("rules_sha256") == rules_hash
				and bool(review.get("reviewed_by"))
				and reviewed_at is not None
			)
			assets = array(market.get("clobTokenIds", []))
			labels = array(market.get("outcomes", []))
			if len(assets) != 2 or len(labels) != 2 or not condition:
				raise ValueError(f"Unsupported binary mapping for {condition}")
			fee = market.get("feeSchedule") or {}
			fee_rate = fee.get("rate") if fee.get("exponent") == 1 else None
			if market.get("feesEnabled") is False:
				fee_rate = "0"
			for asset, label in zip(assets, labels, strict=True):
				contracts.append(
					Contract(
						asset_id=str(asset),
						condition_id=condition,
						event_id=str(event["id"]),
						fixture_id=str(review["fixture_id"]),
						market_id=str(market["id"]),
						label=market.get("question", ""),
						outcome=str(label),
						category=review["category"],
						competition=review.get("competition", ""),
						sports_fixture_id=str(review.get("sports_fixture_id", "")),
						protocol=str(market.get("version", "unknown")),
						known_at=max(known_at, reviewed_at) if reviewed_at else known_at,
						kickoff=timestamp(market.get("gameStartTime") or event.get("startTime")),
						rules=rules,
						rules_url=review.get("rules_url", ""),
						rules_reviewed=approved,
						family_outcome=review.get("family_outcome", ""),
						regulation_time=review.get("regulation_time", False),
						negative_risk=market.get("negRisk"),
						tick_size=market.get(
							"orderPriceMinTickSize", market.get("minimumTickSize")
						),
						min_order_size=market.get("orderMinSize", market.get("minimumOrderSize")),
						fee_rate=fee_rate,
						seconds_delay=market.get("secondsDelay"),
						accepting_orders=market.get("acceptingOrders"),
						clear_book_on_start=market.get("clearBookOnStart"),
						volume_24h=Decimal(str(market.get("volume24hr") or 0)),
					)
				)
	return contracts
