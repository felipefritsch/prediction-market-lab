import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from prediction_market_lab.incidents import explain
from prediction_market_lab.live_evidence import (
	BudgetExceeded,
	EvidenceStore,
	SpendCap,
	due,
	exa_search,
	match_evidence,
	propose_mapping,
	search_slots,
)
from prediction_market_lab.live_evidence import (
	watch as live_watch,
)
from prediction_market_lab.models import Contract
from prediction_market_lab.summarizer import DraftExplanation, OpenAISummarizer

KICKOFF = datetime(2026, 10, 17, 14, tzinfo=UTC)


def draw_contract(fixture: str = "epl-1") -> Contract:
	return Contract(
		asset_id="a",
		condition_id="c",
		event_id="e",
		fixture_id=fixture,
		market_id="m",
		label="Will Brighton & Hove Albion FC vs. Ipswich Town FC end in a draw?",
		outcome="Yes",
		category="football",
		family_outcome="draw",
		known_at=KICKOFF - timedelta(days=3),
		kickoff=KICKOFF,
		rules="r",
	)


@pytest.mark.asyncio
async def test_exa_search_records_retrieval_time_and_charges_reported_cost(tmp_path):
	def handler(request: httpx.Request) -> httpx.Response:
		assert request.headers["x-api-key"] == "k"
		assert json.loads(request.content)["numResults"] == 5
		return httpx.Response(
			200,
			json={
				"results": [
					{"url": "https://news.example/a", "title": "Team news", "text": "x" * 5000},
					{"title": "no url"},
				],
				"costDollars": {"total": 0.012},
			},
		)

	cap = SpendCap(tmp_path / "spend.jsonl", 1)
	async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
		found = await exa_search(client, "k", "q", "epl-1", cap, since=KICKOFF)
	assert len(found) == 1
	assert found[0].allow_model_input and len(found[0].excerpt) == 1200
	assert cap.spent == pytest.approx(0.012)


def test_spend_cap_persists_and_refuses(tmp_path):
	cap = SpendCap(tmp_path / "spend.jsonl", 0.05)
	cap.charge("exa", 0.04, "q")
	reopened = SpendCap(tmp_path / "spend.jsonl", 0.05)
	assert reopened.spent == pytest.approx(0.04)
	with pytest.raises(BudgetExceeded):
		reopened.check(0.02)


def test_store_keeps_first_retrieval(tmp_path):
	from prediction_market_lab.models import Evidence

	store = EvidenceStore(tmp_path / "evidence.json")
	first = Evidence(
		evidence_id="e", fixture_id="f", title="t", url="https://a.example", first_seen=KICKOFF
	)
	assert store.add([first]) == 1
	assert store.add([first.model_copy(update={"first_seen": KICKOFF + timedelta(hours=1)})]) == 0
	saved = json.loads((tmp_path / "evidence.json").read_text())
	assert saved[0]["first_seen"].startswith("2026-10-17T14:00")


def test_match_events_become_timestamped_evidence():
	row = {
		"fixture": {"id": 7, "status": {"short": "1H", "long": "First Half", "elapsed": 23}},
		"teams": {"home": {"name": "Brighton"}, "away": {"name": "Ipswich"}},
		"goals": {"home": 1, "away": 0},
		"events": [
			{
				"time": {"elapsed": 22, "extra": None},
				"team": {"name": "Brighton"},
				"player": {"name": "A. Player"},
				"type": "Goal",
				"detail": "Normal Goal",
			}
		],
	}
	items = match_evidence("epl-1", row, KICKOFF)
	assert [i.title.split()[0] for i in items] == ["Match", "Goal"]
	assert all(i.first_seen == KICKOFF and i.allow_model_input for i in items)


def test_mapping_proposal_requires_names_and_kickoff():
	fixture = {
		"fixture": {"id": 7, "date": "2026-10-17T14:00:00+00:00"},
		"teams": {"home": {"name": "Brighton"}, "away": {"name": "Ipswich"}},
	}
	moved = {**fixture, "fixture": {"id": 8, "date": "2026-10-19T14:00:00+00:00"}}
	mapping = propose_mapping([draw_contract()], [fixture, moved])
	assert mapping["epl-1"]["api_football_id"] == 7
	assert mapping["epl-1"]["reviewed_by"] == ""


def test_search_slots_are_causal_and_not_backfilled():
	slots = {name: at for name, at, _ in search_slots(KICKOFF)}
	assert due("pre-24h", slots["pre-24h"], KICKOFF, KICKOFF - timedelta(hours=3))
	assert not due("pre-24h", slots["pre-24h"], KICKOFF, KICKOFF + timedelta(minutes=1))
	assert due("live-15m", slots["live-15m"], KICKOFF, KICKOFF + timedelta(minutes=20))
	assert not due("live-15m", slots["live-15m"], KICKOFF, KICKOFF + timedelta(minutes=40))


class FakeResponses:
	def parse(self, **kwargs):
		assert kwargs["text_format"] is DraftExplanation
		parsed = DraftExplanation.model_validate(
			{
				"status": "candidate_explanations",
				"claims": [
					{"text": "Goal", "source_ids": ["g"], "status": "candidate_explanation"}
				],
			}
		)
		return SimpleNamespace(
			output_parsed=parsed, usage=SimpleNamespace(input_tokens=1000, output_tokens=100)
		)


def test_openai_summarizer_is_metered_and_validated(tmp_path):
	cap = SpendCap(tmp_path / "spend.jsonl", 1)
	summarizer = OpenAISummarizer(cap, client=SimpleNamespace(responses=FakeResponses()))
	packet = {
		"incident_id": "i",
		"facts": {},
		"evidence": [{"evidence_id": "g", "allow_model_input": True}],
	}
	result = explain(packet, summarizer)
	assert result["method"] == "optional_model" and result["requires_researcher_review"]
	assert cap.spent == pytest.approx((1000 * 0.75 + 100 * 4.5) / 1e6)
	broke = OpenAISummarizer(
		SpendCap(tmp_path / "other.jsonl", 0), client=SimpleNamespace(responses=FakeResponses())
	)
	assert explain(packet, broke)["reason"] == "Optional summarizer failed: BudgetExceeded"


@pytest.mark.asyncio
async def test_watch_links_live_fixture_and_respects_search_schedule(tmp_path, monkeypatch):
	monkeypatch.setenv("EXA_API_KEY", "e")
	monkeypatch.setenv("API_FOOTBALL_KEY", "f")
	calls: list[str] = []

	def handler(request: httpx.Request) -> httpx.Response:
		calls.append(request.url.host)
		if request.url.host == "api.exa.ai":
			return httpx.Response(200, json={"results": [], "costDollars": {"total": 0.007}})
		assert request.url.params["live"] == "all"
		return httpx.Response(
			200,
			json={
				"errors": [],
				"response": [
					{
						"fixture": {
							"id": fixture_id,
							"date": KICKOFF.isoformat(),
							"status": {"short": "1H"},
						},
						"league": {"id": league},
						"teams": {"home": {"name": "Brighton"}, "away": {"name": "Ipswich"}},
						"goals": {"home": 0, "away": 0},
						"events": [],
					}
					# Same names and kickoff in another league must not link.
					for fixture_id, league in ((7, 39), (8, 140))
				],
			},
		)

	times = iter([KICKOFF + timedelta(minutes=16), KICKOFF + timedelta(minutes=17)])
	async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
		state = await live_watch(
			[draw_contract()],
			tmp_path,
			budget_usd=1,
			until=KICKOFF + timedelta(minutes=17),
			tick_seconds=0,
			clock=lambda: next(times),
			client=client,
		)
	assert state["football_links"] == {"7": {"fixture_id": "epl-1", "by": "exact names+kickoff"}}
	assert state["fired"] == ["epl-1:live-15m"]
	assert sorted(calls) == ["api.exa.ai", "v3.football.api-sports.io"]
	assert json.loads((tmp_path / "evidence.json").read_text())[0]["fixture_id"] == "epl-1"


def test_model_sees_latest_twelve_items():
	seen: list[str] = []

	def capture(bounded):
		seen.extend(e["evidence_id"] for e in bounded["evidence"])
		return {"status": "no_convincing_explanation", "claims": []}

	evidence = [
		{
			"evidence_id": f"e{i:02d}",
			"allow_model_input": True,
			"first_seen": (KICKOFF + timedelta(minutes=i)).isoformat(),
		}
		for i in range(20)
	]
	explain({"incident_id": "i", "facts": {}, "evidence": evidence[::-1]}, capture)
	assert seen == [f"e{i:02d}" for i in range(8, 20)]
