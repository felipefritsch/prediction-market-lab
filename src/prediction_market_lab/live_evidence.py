"""Live web-search and match-event evidence, separate from market capture, under a hard spend cap."""

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from prediction_market_lab.models import Contract, Evidence, timestamp, utc_now
from prediction_market_lab.storage import atomic_write, canonical

EXA = "https://api.exa.ai/search"
FOOTBALL = "https://v3.football.api-sports.io"
EPL_LEAGUE = 39


class BudgetExceeded(RuntimeError):
	pass


class SpendCap:
	"""Append-only ledger shared by every paid call; refuses calls that could exceed the cap."""

	def __init__(self, path: Path, limit_usd: float, football_daily: int = 90) -> None:
		self.path, self.limit, self.football_daily = path, limit_usd, football_daily
		self.rows: list[dict[str, Any]] = []
		if path.exists():
			self.rows = [json.loads(line) for line in path.read_text().splitlines() if line]

	@property
	def spent(self) -> float:
		return sum(float(r["usd"]) for r in self.rows)

	def check(self, estimate_usd: float) -> None:
		if self.spent + estimate_usd > self.limit:
			raise BudgetExceeded(f"Spend cap ${self.limit:.2f} reached (${self.spent:.4f} spent)")

	def check_football(self, at: datetime) -> None:
		day = at.date().isoformat()
		used = sum(1 for r in self.rows if r["service"] == "api-football" and r["at"][:10] == day)
		if used >= self.football_daily:
			raise BudgetExceeded("API-Football daily request cap reached")

	def charge(self, service: str, usd: float, detail: str, at: datetime | None = None) -> None:
		row = {
			"at": (at or utc_now()).isoformat(),
			"service": service,
			"usd": round(usd, 6),
			"detail": detail[:200],
		}
		self.path.parent.mkdir(parents=True, exist_ok=True)
		with self.path.open("a") as handle:
			handle.write(json.dumps(row) + "\n")
		self.rows.append(row)


class EvidenceStore:
	"""One JSON list, as `read_evidence` expects; an item keeps the time it was first retrieved."""

	def __init__(self, path: Path) -> None:
		self.path = path
		self.items: dict[str, dict[str, Any]] = {}
		if path.exists():
			self.items = {e["evidence_id"]: e for e in json.loads(path.read_text())}

	def add(self, evidence: list[Evidence]) -> int:
		new = [e for e in evidence if e.evidence_id not in self.items]
		for item in new:
			self.items[item.evidence_id] = item.model_dump(mode="json")
		if new:
			atomic_write(
				self.path, canonical(sorted(self.items.values(), key=lambda e: e["first_seen"]))
			)
		return len(new)


def evidence_id(*parts: Any) -> str:
	return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:20]


async def exa_search(
	client: httpx.AsyncClient,
	key: str,
	query: str,
	fixture_id: str,
	cap: SpendCap,
	*,
	since: datetime,
	results: int = 5,
) -> list[Evidence]:
	estimate = 0.007 + 0.001 * results
	cap.check(estimate)
	response = await client.post(
		EXA,
		headers={"x-api-key": key},
		json={
			"query": query,
			"type": "auto",
			"numResults": results,
			"startPublishedDate": since.isoformat().replace("+00:00", "Z"),
			"contents": {"text": {"maxCharacters": 1200}},
		},
	)
	at = utc_now()
	response.raise_for_status()
	body = response.json()
	cap.charge("exa", float((body.get("costDollars") or {}).get("total", estimate)), query, at)
	found = []
	for row in body.get("results", []):
		if not row.get("url"):
			continue
		found.append(
			Evidence(
				evidence_id=evidence_id(fixture_id, row["url"]),
				fixture_id=fixture_id,
				title=str(row.get("title") or "Untitled search result")[:500],
				url=row["url"],
				published_at=timestamp(row.get("publishedDate")),
				first_seen=at,
				source="exa: " + query,
				excerpt=str(row.get("text") or "")[:1200],
				allow_model_input=True,
				allow_redistribution=False,
			)
		)
	return found


def team_key(name: str) -> str:
	words = re.sub(r"[^a-z ]", " ", name.lower().replace("&", " and ")).split()
	return " ".join(
		w for w in words if w not in {"fc", "afc", "and", "hove", "albion", "united", "town"}
	)


def fixture_teams(contracts: list[Contract]) -> dict[str, tuple[str, str, datetime | None]]:
	"""Home/away names from each reviewed family's draw question, plus kickoff."""
	teams = {}
	for contract in contracts:
		found = re.fullmatch(r"Will (.+) vs\. (.+) end in a draw\?", contract.label)
		if contract.family_outcome == "draw" and found:
			teams[contract.fixture_id] = (found[1], found[2], contract.kickoff)
	return teams


def same_fixture(teams: tuple[str, str, datetime | None], row: dict[str, Any]) -> bool:
	"""Exact normalized home and away names and kickoff within two hours; no fuzzy matching."""
	home, away, kickoff = teams
	started = timestamp(row["fixture"]["date"])
	return (
		team_key(row["teams"]["home"]["name"]) == team_key(home)
		and team_key(row["teams"]["away"]["name"]) == team_key(away)
		and kickoff is not None
		and started is not None
		and abs(started - kickoff) <= timedelta(hours=2)
	)


def propose_mapping(contracts: list[Contract], fixtures: list[dict[str, Any]]) -> dict[str, Any]:
	"""Exact normalized names plus kickoff time; written for review, never trusted silently."""
	mapping: dict[str, Any] = {}
	for fixture_id, (home_name, away_name, kickoff) in sorted(fixture_teams(contracts).items()):
		matches = [f for f in fixtures if same_fixture((home_name, away_name, kickoff), f)]
		mapping[fixture_id] = {
			"api_football_id": matches[0]["fixture"]["id"] if len(matches) == 1 else None,
			"polymarket": f"{home_name} vs. {away_name}",
			"api_football": (
				f"{matches[0]['teams']['home']['name']} vs. {matches[0]['teams']['away']['name']}"
				if len(matches) == 1
				else f"{len(matches)} candidates"
			),
			"kickoff": kickoff.isoformat() if kickoff else None,
			"reviewed_by": "",
		}
	return mapping


async def football_fixtures(
	client: httpx.AsyncClient, key: str, cap: SpendCap, params: dict[str, Any]
) -> list[dict[str, Any]]:
	at = utc_now()
	cap.check_football(at)
	response = await client.get(
		FOOTBALL + "/fixtures", headers={"x-apisports-key": key}, params=params
	)
	cap.charge("api-football", 0.0, "fixtures " + json.dumps(params, sort_keys=True), at)
	response.raise_for_status()
	body = response.json()
	if body.get("errors"):
		raise ValueError(f"API-Football error: {body['errors']}")
	return list(body.get("response", []))


def match_evidence(fixture_id: str, row: dict[str, Any], at: datetime) -> list[Evidence]:
	fixture, teams, goals = row["fixture"], row["teams"], row.get("goals") or {}
	url = f"{FOOTBALL}/fixtures?id={fixture['id']}"
	score = (
		f"{teams['home']['name']} {goals.get('home')}-{goals.get('away')} {teams['away']['name']}"
	)
	status = fixture.get("status") or {}
	items = [
		Evidence(
			evidence_id=evidence_id(fixture_id, "status", status.get("short")),
			fixture_id=fixture_id,
			title=f"Match status {status.get('long', status.get('short'))}: {score}",
			url=url,
			first_seen=at,
			source="api-football",
			excerpt=f"Status {status.get('short')} at minute {status.get('elapsed')}. {score}.",
			allow_model_input=True,
		)
	]
	for event in row.get("events") or []:
		minute = event.get("time") or {}
		label = (
			f"{event.get('type')} ({event.get('detail')}) {minute.get('elapsed')}"
			f"{'+' + str(minute['extra']) if minute.get('extra') else ''}' "
			f"{(event.get('team') or {}).get('name')}: {(event.get('player') or {}).get('name')}"
		)
		items.append(
			Evidence(
				evidence_id=evidence_id(fixture_id, "event", label),
				fixture_id=fixture_id,
				title=label[:500],
				url=url,
				first_seen=at,
				source="api-football",
				excerpt=f"{label}. Score when retrieved: {score}. Minute is match time, not wall-clock.",
				allow_model_input=True,
			)
		)
	return items


def search_slots(kickoff: datetime) -> list[tuple[str, datetime, str]]:
	"""(slot name, due time, query template) for one fixture."""
	slots = [
		("pre-24h", kickoff - timedelta(hours=24), "{home} vs {away} preview injuries team news"),
		("pre-2h", kickoff - timedelta(hours=2), "{home} vs {away} team news"),
		("pre-1h", kickoff - timedelta(hours=1), "{home} vs {away} confirmed lineups"),
	]
	for minutes in range(15, 121, 15):
		slots.append(
			(f"live-{minutes}m", kickoff + timedelta(minutes=minutes), "{home} vs {away} live")
		)
	slots.append(("post", kickoff + timedelta(hours=3), "{home} vs {away} match report"))
	return slots


def due(name: str, slot_at: datetime, kickoff: datetime, now: datetime) -> bool:
	if now < slot_at:
		return False
	if name.startswith("pre"):
		return now < kickoff
	return now - slot_at < timedelta(minutes=15)


async def watch(
	contracts: list[Contract],
	out: Path,
	*,
	budget_usd: float,
	until: datetime,
	mapping: dict[str, Any] | None = None,
	poll_seconds: float = 360,
	tick_seconds: float = 60,
	clock: Callable[[], datetime] = utc_now,
	client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
	exa_key = os.environ.get("EXA_API_KEY", "")
	football_key = os.environ.get("API_FOOTBALL_KEY", "")
	if not exa_key and not football_key:
		raise ValueError("Set EXA_API_KEY and/or API_FOOTBALL_KEY (e.g. in .env)")
	fixtures = fixture_teams(contracts)
	cap = SpendCap(out / "spend.jsonl", budget_usd)
	store = EvidenceStore(out / "evidence.json")
	state_path = out / "state.json"
	state: dict[str, Any] = json.loads(state_path.read_text()) if state_path.exists() else {}
	fired: set[str] = set(state.get("fired", []))
	last_poll = timestamp(state.get("last_poll"))
	# Reviewed IDs win; otherwise a live EPL fixture is linked by exact names and kickoff.
	linked: dict[str, dict[str, str]] = state.get("football_links", {})
	for fixture_id, row in (mapping or {}).items():
		if row.get("api_football_id") and row.get("reviewed_by"):
			linked[str(row["api_football_id"])] = {
				"fixture_id": fixture_id,
				"by": row["reviewed_by"],
			}
	errors: dict[str, str] = {}
	http = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
	try:
		while (now := clock()) < until:
			for fixture_id, (home, away, kickoff) in sorted(fixtures.items()):
				if kickoff is None or not exa_key:
					continue
				for name, slot_at, template in search_slots(kickoff):
					key = f"{fixture_id}:{name}"
					if key in fired or not due(name, slot_at, kickoff, now):
						continue
					fired.add(key)
					try:
						found = await exa_search(
							http,
							exa_key,
							template.format(home=home, away=away),
							fixture_id,
							cap,
							since=kickoff - timedelta(days=7),
						)
						store.add(found)
					except BudgetExceeded as exc:
						errors["exa"] = str(exc)
						exa_key = ""
						break
					except Exception as exc:
						errors[key] = type(exc).__name__ + ": " + str(exc)[:200]
			live = [
				f
				for f, (_, _, k) in fixtures.items()
				if k and k - timedelta(minutes=15) <= now <= k + timedelta(hours=2, minutes=30)
			]
			if (
				football_key
				and live
				and (last_poll is None or (now - last_poll).total_seconds() >= poll_seconds)
			):
				last_poll = now
				try:
					# The API accepts only "all" or two or more hyphenated league IDs, so one league
					# is filtered here.
					rows = await football_fixtures(http, football_key, cap, {"live": "all"})
					at = utc_now()
					for row in rows:
						if (row.get("league") or {}).get("id") != EPL_LEAGUE:
							continue
						api_id = str(row["fixture"]["id"])
						if api_id not in linked:
							candidates = [f for f in live if same_fixture(fixtures[f], row)]
							if len(candidates) == 1:
								linked[api_id] = {
									"fixture_id": candidates[0],
									"by": "exact names+kickoff",
								}
						if api_id in linked:
							store.add(match_evidence(linked[api_id]["fixture_id"], row, at))
				except BudgetExceeded as exc:
					errors["api-football"] = str(exc)
				except Exception as exc:
					errors["api-football"] = type(exc).__name__ + ": " + str(exc)[:200]
			state = {
				"fired": sorted(fired),
				"last_poll": last_poll.isoformat() if last_poll else None,
				"football_links": linked,
				"spent_usd": round(cap.spent, 4),
				"evidence_items": len(store.items),
				"errors": errors,
				"updated_at": now.isoformat(),
			}
			atomic_write(state_path, canonical(state))
			await asyncio.sleep(tick_seconds)
	finally:
		if client is None:
			await http.aclose()
	return {**state, "status": "completed"}
