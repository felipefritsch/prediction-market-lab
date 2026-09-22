import asyncio
import json

import httpx
import pytest

from prediction_market_lab.adapters import PolymarketAdapter
from prediction_market_lab.collector import collect
from prediction_market_lab.config import CaptureConfig
from prediction_market_lab.storage import read_archive
from tests.conftest import snapshot


@pytest.mark.asyncio
async def test_v2_request_names_and_cursor():
	requests = []

	def handler(request):
		requests.append(request)
		return httpx.Response(
			200,
			json={
				"data": [],
				"pagination": {"next_cursor": "second" if len(requests) == 1 else None},
			},
		)

	async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
		adapter = PolymarketAdapter(http)
		pages = [p async for p in adapter.trade_pages("0xcondition", pages=2)]
		await adapter.prices("asset", 1, 301)
	assert len(pages) == 2
	assert requests[0].url.params["condition_id"] == "0xcondition"
	assert requests[1].url.params["cursor"] == "second"
	assert requests[2].url.params["token_id"] == "asset"
	assert requests[2].url.params["start"] == "1"
	assert "market" not in requests[2].url.params


@pytest.mark.asyncio
async def test_repeated_cursor_and_invalid_history_window():
	async with httpx.AsyncClient(
		transport=httpx.MockTransport(
			lambda _: httpx.Response(200, json={"data": [], "pagination": {"next_cursor": "same"}})
		)
	) as http:
		adapter = PolymarketAdapter(http)
		with pytest.raises(ValueError, match="advance"):
			[p async for p in adapter.trade_pages("c", pages=3)]
		with pytest.raises(ValueError, match="15 days"):
			await adapter.prices("a", 1, 20 * 86400)
		with pytest.raises(ValueError, match="public Polymarket"):
			await adapter.get("https://unapproved.example/data")


@pytest.mark.asyncio
async def test_collector_small_queue_survives_optional_feed_failure(
	tmp_path, contract, monkeypatch
):
	class Adapter:
		async def frames(self, assets, *, sports=False):
			if sports:
				raise ConnectionError("score provider unavailable")
			while True:
				yield json.dumps(snapshot().payload)
				await asyncio.sleep(0.01)

		async def get(self, url, params=None):
			raise httpx.ConnectError("optional analytics unavailable")

		async def trade_pages(self, condition, *, pages):
			raise httpx.ConnectError("canonical trade service unavailable")
			yield {}

		async def close(self):
			pass

	monkeypatch.setattr("prediction_market_lab.collector.PolymarketAdapter", Adapter)
	health = await asyncio.wait_for(
		collect(tmp_path, [contract], 0.15, CaptureConfig(queue_size=1, flush_seconds=0.03)),
		timeout=3,
	)
	assert health["status"] == "completed"
	assert health["records"] > 0
	assert "sports" in health["errors"]
	assert any(r.source == "market" for r in read_archive(tmp_path))
