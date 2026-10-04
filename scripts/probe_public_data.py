"""Bounded unauthenticated connectivity check; never sends orders or loads .env."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS = "wss://sports-api.polymarket.com/ws"


async def rest(url: str, params: dict[str, Any]) -> tuple[dict[str, Any], Any]:
	result: dict[str, Any] = {"url": url, "params": params}
	try:
		async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
			response = await client.get(url, params=params)
		result["http_status"] = response.status_code
		result["content_type"] = response.headers.get("content-type", "")
		try:
			payload = response.json()
		except ValueError:
			payload = None
		result["json_type"] = type(payload).__name__
		if isinstance(payload, list):
			result["rows"] = len(payload)
		elif isinstance(payload, dict):
			result["keys"] = sorted(payload)
		return result, payload if response.is_success else None
	except httpx.TransportError as exc:
		result.update(error_type=type(exc).__name__, error=str(exc))
		return result, None


async def stream(url: str, assets: list[str], seconds: int) -> dict[str, Any]:
	result: dict[str, Any] = {"url": url, "handshake": False, "frames": 0}
	try:
		async with websockets.connect(url, open_timeout=12, max_size=8 * 1024 * 1024) as ws:
			result["handshake"] = True
			if url == MARKET_WS:
				if not assets:
					result["subscription"] = "not attempted: discovery supplied no active asset"
					return result
				await ws.send(
					json.dumps({"type": "market", "assets_ids": assets, "initial_dump": True})
				)
				result["assets"] = assets
			loop = asyncio.get_running_loop()
			deadline = loop.time() + seconds
			kinds: set[str] = set()
			while (remaining := deadline - loop.time()) > 0:
				try:
					frame = await asyncio.wait_for(ws.recv(), remaining)
				except TimeoutError:
					break
				result["frames"] += 1
				if frame == "ping":
					await ws.send("pong")
					kinds.add("heartbeat")
					continue
				try:
					payload = json.loads(frame)
				except (ValueError, TypeError):
					kinds.add("non_json")
					continue
				for item in payload if isinstance(payload, list) else [payload]:
					if isinstance(item, dict):
						kinds.add(str(item.get("event_type", item.get("type", "untyped_json"))))
			result["message_types"] = sorted(kinds)
	except Exception as exc:
		# Diagnostics retain the failed check explicitly, rather than reporting a healthy feed.
		result.update(error_type=type(exc).__name__, error=str(exc))
	return result


async def probe(seconds: int) -> dict[str, Any]:
	report: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "authenticated": False}
	initial = await asyncio.gather(
		rest(GAMMA + "/markets", {"limit": 20, "active": "true", "closed": "false"}),
		rest(CLOB + "/time", {}),
		rest(DATA + "/v2/trades", {"limit": 1}),
	)
	report["rest"] = [item[0] for item in initial]
	markets = initial[0][1]
	assets: list[str] = []
	condition = ""
	if isinstance(markets, list):
		for market in markets:
			if not isinstance(market, dict) or not market.get("acceptingOrders"):
				continue
			tokens = market.get("clobTokenIds", [])
			if isinstance(tokens, str):
				tokens = json.loads(tokens)
			if isinstance(tokens, list) and tokens:
				assets = [str(tokens[0])]
				condition = str(market.get("conditionId", ""))
				break
	if assets:
		checks = await asyncio.gather(
			rest(CLOB + "/book", {"token_id": assets[0]}),
			rest(DATA + "/v2/trades", {"condition_id": condition, "limit": 1}),
			rest(DATA + "/v2/oi", {"condition_id": condition}),
		)
		report["rest"].extend(item[0] for item in checks)
	report["websockets"] = await asyncio.gather(
		stream(MARKET_WS, assets, seconds), stream(SPORTS_WS, [], seconds)
	)
	return report


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--seconds", type=int, default=15, choices=range(1, 31))
	parser.add_argument("--out", type=Path, required=True)
	args = parser.parse_args()
	if args.out.exists():
		parser.error("Choose a new report path; existing checks are never overwritten")
	report = asyncio.run(probe(args.seconds))
	args.out.parent.mkdir(parents=True, exist_ok=True)
	with args.out.open("x") as output:
		json.dump(report, output, indent=2)
	print(json.dumps(report, indent=2))


if __name__ == "__main__":
	main()
