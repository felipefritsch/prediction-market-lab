"""Public-data and offline research commands. No live-trading command exists."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from prediction_market_lab.adapters import GAMMA, PolymarketAdapter, contracts_from_discovery
from prediction_market_lab.config import Settings
from prediction_market_lab.models import Contract, RawRecord, timestamp, utc_now
from prediction_market_lab.operations import backup, budget_forecast, restore, restore_check
from prediction_market_lab.paper import PaperConfig
from prediction_market_lab.storage import (
	RawArchive,
	atomic_write,
	canonical,
	read_archive,
	verify_run,
)


def save_new(path: Path, document: Any) -> None:
	if path.exists():
		raise FileExistsError(f"Output already exists: {path}")
	atomic_write(path, canonical(document))


def paper_config(
	settings: Settings, latency: int | None = None, queue: str | None = None
) -> PaperConfig:
	values: dict[str, Any] = dict(settings.paper)
	for field in (
		"initial_cash",
		"event_risk_limit",
		"portfolio_risk_limit",
		"order_size",
		"queue_multiplier",
	):
		if field in values:
			values[field] = Decimal(str(values[field]))
	if latency is not None:
		values["latency_ms"] = latency
	if queue is not None:
		values["queue_multiplier"] = Decimal(queue)
	config = PaperConfig(**values)
	if config.latency_ms < 0 or config.cancel_latency_ms < 0 or config.queue_multiplier < 0:
		raise ValueError("Latency and queue multiplier must be nonnegative")
	return config


def parser() -> argparse.ArgumentParser:
	p = argparse.ArgumentParser(
		description="Prediction Market Lab — capture, replay, research and paper execution"
	)
	p.add_argument("--config", type=Path, help="TOML configuration")
	sub = p.add_subparsers(dest="command", required=True)
	demo = sub.add_parser("demo", help="Complete deterministic offline synthetic demonstration")
	demo.add_argument("--out", type=Path, default=Path("data/demo"))
	demo.add_argument(
		"--quick", action="store_true", help="One paper run instead of latency/queue scenarios"
	)
	for name in ("sports", "discover"):
		command = sub.add_parser(name)
		command.add_argument("--out", type=Path, required=True)
		if name == "discover":
			command.add_argument("--sport")
			command.add_argument("--pages", type=int, default=2)
	review = sub.add_parser(
		"review-template", help="Create unapproved, hash-bound rule mapping candidates"
	)
	review.add_argument("--discovery", type=Path, required=True)
	review.add_argument("--out", type=Path, required=True)
	build = sub.add_parser("catalog-build")
	build.add_argument("--discovery", type=Path, action="append", required=True)
	build.add_argument("--reviews", type=Path, required=True)
	build.add_argument("--out", type=Path, required=True)
	selection = sub.add_parser("select")
	selection.add_argument("--contracts", type=Path, required=True)
	selection.add_argument("--asof")
	selection.add_argument("--out", type=Path, required=True)
	capture = sub.add_parser("collect")
	capture.add_argument("--watchlist", type=Path, required=True)
	capture.add_argument("--out", type=Path, required=True)
	capture.add_argument("--seconds", type=float, default=3600)
	capture.add_argument("--no-sports", action="store_true")
	backfill = sub.add_parser("backfill-trades")
	backfill.add_argument("--condition", required=True)
	backfill.add_argument("--out", type=Path, required=True)
	backfill.add_argument("--pages", type=int, default=10)
	prices = sub.add_parser("backfill-prices")
	prices.add_argument("--asset", required=True)
	prices.add_argument("--start", required=True)
	prices.add_argument("--end", required=True)
	prices.add_argument("--out", type=Path, required=True)
	for name in ("replay", "paper"):
		command = sub.add_parser(name)
		command.add_argument("--capture", type=Path, required=True)
		command.add_argument("--out", type=Path, required=True)
		command.add_argument("--until")
		command.add_argument("--from", dest="since")
		command.add_argument("--max-records", type=int, default=250000)
		if name == "paper":
			command.add_argument(
				"--strategy", choices=["taker", "static", "inventory"], default="taker"
			)
			command.add_argument("--latency-ms", type=int, default=250)
			command.add_argument("--queue-multiplier", default="1")
	research = sub.add_parser("research")
	research.add_argument("--run", type=Path, required=True)
	research.add_argument("--out", type=Path, required=True)
	research.add_argument("--paper-root", type=Path)
	research.add_argument("--evidence", type=Path)
	research.add_argument("--summarizer", choices=["none", "openai"], default="none")
	research.add_argument("--model", default="gpt-5.4-mini")
	research.add_argument("--spend-ledger", type=Path, help="Shared spend ledger (JSON lines)")
	research.add_argument("--budget-usd", type=float, default=20)
	dashboard = sub.add_parser("dashboard")
	dashboard.add_argument("--workspace", type=Path, default=Path("data/demo"))
	dashboard.add_argument("--port", type=int, default=8501)
	for name in ("verify", "health", "backup-check", "pilot-report"):
		command = sub.add_parser(name)
		command.add_argument("path", type=Path)
	for name in ("backup", "restore"):
		command = sub.add_parser(name)
		command.add_argument("source", type=Path)
		command.add_argument("destination", type=Path)
	budget = sub.add_parser("budget")
	budget.add_argument("--raw-bytes", type=int, required=True)
	budget.add_argument("--elapsed-seconds", type=float, required=True)
	budget.add_argument("--server-usd", type=float, required=True)
	budget.add_argument("--storage-gb-usd", type=float, required=True)
	sub.add_parser("doctor")
	feed = sub.add_parser(
		"ingest-rss", help="Record source links/timestamps; separate from the collector"
	)
	feed.add_argument("--url", required=True)
	feed.add_argument("--fixture", required=True)
	feed.add_argument("--out", type=Path, required=True)
	football = sub.add_parser(
		"football-map", help="Propose watchlist-to-API-Football fixture IDs for review"
	)
	football.add_argument("--watchlist", type=Path, required=True)
	football.add_argument("--season", type=int, required=True)
	football.add_argument("--out", type=Path, required=True)
	football.add_argument("--spend-ledger", type=Path, required=True)
	watcher = sub.add_parser(
		"evidence-watch", help="Timed web search and match events; separate from the collector"
	)
	watcher.add_argument("--watchlist", type=Path, required=True)
	watcher.add_argument("--out", type=Path, required=True)
	watcher.add_argument("--until", required=True)
	watcher.add_argument("--mapping", type=Path, help="Reviewed football-map output")
	watcher.add_argument("--budget-usd", type=float, default=20)
	back = sub.add_parser(
		"forecast-backtest", help="Walk-forward Elo/goal models vs bookmaker closing odds"
	)
	back.add_argument("--data", type=Path, default=Path("data/football-data"))
	back.add_argument("--first-test-season", type=int, default=2022)
	back.add_argument("--last-test-season", type=int, default=2025)
	back.add_argument("--out", type=Path, required=True)
	fwatch = sub.add_parser(
		"forecast-watch", help="Freeze pre-match ensemble forecasts with a market snapshot"
	)
	fwatch.add_argument("--watchlist", type=Path, required=True)
	fwatch.add_argument("--evidence", type=Path, required=True)
	fwatch.add_argument(
		"--out", type=Path, required=True, help="Append-only forecast log (JSON lines)"
	)
	fwatch.add_argument("--spend-ledger", type=Path, required=True)
	fwatch.add_argument("--budget-usd", type=float, default=20)
	fwatch.add_argument("--until", required=True)
	fwatch.add_argument("--data", type=Path, default=Path("data/football-data"))
	fwatch.add_argument("--season", type=int, default=2026, help="Current season start year")
	fwatch.add_argument("--models", default="gpt-5.4-mini,gpt-4.1-mini")
	fwatch.add_argument("--samples", type=int, default=3)
	feval = sub.add_parser(
		"forecast-evaluate", help="Score frozen forecasts against results and market"
	)
	feval.add_argument("--forecasts", type=Path, required=True)
	feval.add_argument("--data", type=Path, default=Path("data/football-data"))
	feval.add_argument("--season", type=int, default=2026)
	feval.add_argument("--out", type=Path, required=True)
	return p


async def online(args: argparse.Namespace, settings: Settings) -> Any:
	adapter = PolymarketAdapter()
	try:
		if args.command == "sports":
			result = await adapter.get(GAMMA + "/sports")
			save_new(args.out, result)
			return {"sports": result}
		if args.command == "discover":
			result = await adapter.discover(sport=args.sport, pages=args.pages)
			save_new(args.out, result)
			return {
				"events": len(result["events"]),
				"scan_complete": result["scan_complete"],
				"path": str(args.out),
			}
		if args.command == "collect":
			from prediction_market_lab.collector import collect
			from prediction_market_lab.universe import select_universe

			if args.seconds <= 0:
				raise ValueError("Capture duration must be positive")
			doc = json.loads(args.watchlist.read_text())
			contracts = [Contract.model_validate(c) for c in doc["contracts"]]
			eligible, _ = select_universe(contracts, utc_now(), settings.capture)
			if {c.asset_id for c in eligible} != {c.asset_id for c in contracts}:
				raise ValueError(
					"Watchlist must contain only complete, reviewed, eligible groups within the caps"
				)
			args.out.mkdir(parents=True, exist_ok=True)
			selection_path = args.out / "selections" / (uuid.uuid4().hex + ".json")
			save_new(selection_path, {"started_at": utc_now().isoformat(), **doc})
			return await collect(
				args.out, contracts, args.seconds, settings.capture, sports=not args.no_sports
			)
		if args.out.exists():
			raise FileExistsError("Backfill output must be a new directory")
		count = 0
		complete = False
		session = uuid.uuid4().hex
		with RawArchive(args.out) as archive:
			if args.command == "backfill-trades":
				if not 1 <= args.pages <= 100:
					raise ValueError("Request between 1 and 100 pages per resumable batch")
				async for page in adapter.trade_pages(args.condition, pages=args.pages):
					count += 1
					archive.append(
						RawRecord(
							record_id=f"{session}:{count}",
							source="canonical_trades",
							session_id=session,
							sequence=count,
							received_at=utc_now(),
							monotonic_ns=time.monotonic_ns(),
							payload={
								"event_type": "canonical_trades",
								"condition_id": args.condition,
								"response": page,
							},
						)
					)
					complete = not page.get("pagination", {}).get("next_cursor")
					atomic_write(args.out / "cursor.json", canonical(page.get("pagination", {})))
			else:
				start, end = timestamp(args.start), timestamp(args.end)
				assert start and end
				cursor = None
				for count in range(1, 101):
					page = await adapter.prices(
						args.asset, int(start.timestamp()), int(end.timestamp()), cursor=cursor
					)
					archive.append(
						RawRecord(
							record_id=f"{session}:{count}",
							source="sampled_prices",
							session_id=session,
							sequence=count,
							received_at=utc_now(),
							monotonic_ns=time.monotonic_ns(),
							payload={
								"event_type": "sampled_prices",
								"asset_id": args.asset,
								"response": page,
							},
						)
					)
					next_cursor = page.get("pagination", {}).get("next_cursor")
					if not next_cursor:
						complete = True
						break
					if next_cursor == cursor:
						raise ValueError("Price-history cursor did not advance")
					cursor = next_cursor
		return {"pages": count, "endpoint_exhausted": complete, "historical_depth": False}
	finally:
		await adapter.close()


async def evidence_command(args: argparse.Namespace) -> Any:
	import os

	import httpx

	from prediction_market_lab import live_evidence as live

	contracts = [
		Contract.model_validate(c) for c in json.loads(args.watchlist.read_text())["contracts"]
	]
	if args.command == "evidence-watch":
		until = timestamp(args.until)
		assert until is not None
		mapping = json.loads(args.mapping.read_text()) if args.mapping else None
		return await live.watch(
			contracts, args.out, budget_usd=args.budget_usd, until=until, mapping=mapping
		)
	key = os.environ.get("API_FOOTBALL_KEY", "")
	if not key:
		raise ValueError("Set API_FOOTBALL_KEY (e.g. in .env)")
	kickoffs = sorted(c.kickoff for c in contracts if c.kickoff)
	if not kickoffs:
		raise ValueError("Watchlist has no kickoff times")
	cap = live.SpendCap(args.spend_ledger, 0)
	async with httpx.AsyncClient(timeout=30) as client:
		fixtures = await live.football_fixtures(
			client,
			key,
			cap,
			{
				"league": live.EPL_LEAGUE,
				"season": args.season,
				"from": kickoffs[0].date().isoformat(),
				"to": kickoffs[-1].date().isoformat(),
			},
		)
	mapping = live.propose_mapping(contracts, fixtures)
	save_new(args.out, mapping)
	return mapping


async def forecast_watch(args: argparse.Namespace) -> Any:
	from datetime import timedelta

	from prediction_market_lab import forecast
	from prediction_market_lab.live_evidence import SpendCap

	until = timestamp(args.until)
	assert until is not None
	contracts = [
		Contract.model_validate(c) for c in json.loads(args.watchlist.read_text())["contracts"]
	]
	fixtures = forecast.fixtures_from_watchlist(contracts)
	models = tuple(m for m in args.models.split(",") if m)
	llm_client = None
	if models:
		from openai import OpenAI

		llm_client = OpenAI(timeout=90, max_retries=1)
	cap = SpendCap(args.spend_ledger, args.budget_usd)
	adapter = PolymarketAdapter()
	current = forecast.seasons(args.season, args.season)[0]
	frozen = 0
	try:
		while (now := utc_now()) < until:
			done = forecast.done_slots(args.out)
			due = [
				(fixture, slot)
				for fixture in fixtures
				for slot, at in forecast.forecast_slots(fixture.kickoff)
				if f"{fixture.fixture_id}:{slot}" not in done
				and at <= now < min(fixture.kickoff, at + timedelta(hours=6))
			]
			if due:
				matches = forecast.load_matches(
					args.data, forecast.seasons(args.season - 8, args.season), refresh=current
				)
				evidence = json.loads(args.evidence.read_text()) if args.evidence.exists() else []
				for fixture, slot in due:
					record = await forecast.forecast_once(
						fixture,
						slot,
						matches,
						evidence,
						adapter=adapter,
						llm_client=llm_client,
						llm_models=models,
						samples=args.samples,
						cap=cap,
						now=utc_now(),
					)
					forecast.freeze(args.out, record)
					frozen += 1
					print(
						json.dumps(
							{
								"frozen": record["fixture_id"],
								"slot": slot,
								"ensemble": record["ensemble"],
							}
						),
						flush=True,
					)
			await asyncio.sleep(60)
	finally:
		await adapter.close()
	return {"frozen": frozen, "spent_usd": round(cap.spent, 4)}


def run(args: argparse.Namespace) -> Any:
	settings = Settings.load(args.config)
	if args.command in {"forecast-backtest", "forecast-evaluate"}:
		from prediction_market_lab import forecast

		if args.command == "forecast-backtest":
			matches = forecast.load_matches(
				args.data, forecast.seasons(args.first_test_season - 8, args.last_test_season)
			)
			result = forecast.backtest(
				matches, forecast.seasons(args.first_test_season, args.last_test_season)
			)
		else:
			current = forecast.seasons(args.season, args.season)[0]
			matches = forecast.load_matches(
				args.data, forecast.seasons(args.season, args.season), refresh=current
			)
			result = forecast.evaluate(args.forecasts, matches)
		save_new(args.out, result)
		return result
	if args.command == "forecast-watch":
		return asyncio.run(forecast_watch(args))
	if args.command in {"football-map", "evidence-watch"}:
		return asyncio.run(evidence_command(args))
	if args.command == "ingest-rss":
		from prediction_market_lab.evidence_sources import fetch_feed

		rows = asyncio.run(fetch_feed(args.url, args.fixture))
		save_new(args.out, rows)
		return {"source_items": len(rows), "permitted_model_input": False}
	if args.command in {"sports", "discover", "collect", "backfill-trades", "backfill-prices"}:
		return asyncio.run(online(args, settings))
	if args.command == "demo":
		from prediction_market_lab.replay import replay
		from prediction_market_lab.reporting import report
		from prediction_market_lab.synthetic import generate

		if args.out.exists():
			raise FileExistsError("Demo directory exists; choose --out with a new path")
		generate(args.out / "capture", seed=int(settings.research.get("random_seed", 2026)))
		runs = {}
		for strategy in ["taker"] if args.quick else ["taker", "static", "inventory"]:
			for latency in [250] if args.quick else [0, 250, 1000]:
				name = f"{strategy}-{latency}ms-q1"
				runs[name] = args.out / "paper" / name
				replay(
					args.out / "capture",
					runs[name],
					strategy=strategy,
					paper=paper_config(settings, latency),
				)
		if not args.quick:
			for multiplier in ("0", "2"):
				name = f"static-250ms-q{multiplier}"
				runs[name] = args.out / "paper" / name
				replay(
					args.out / "capture",
					runs[name],
					strategy="static",
					paper=paper_config(settings, 250, multiplier),
				)
		primary = runs["taker-250ms-q1"]
		result = report(
			primary,
			args.out / "research",
			paper_runs=runs,
			evidence_path=args.out / "capture" / "evidence.json",
			research_config=settings.research,
		)
		atomic_write(
			args.out / "workspace.json",
			canonical(
				{
					"run": str(primary.relative_to(args.out)),
					"research": "research",
					"capture": "capture",
				}
			),
		)
		return {
			"workspace": str(args.out),
			"report": result["report"],
			"paper_scenarios": len(runs),
			"incidents": result["incidents"],
			"restore_check": restore_check(args.out / "capture"),
			"evidence_class": "synthetic inputs / derived research / simulated execution",
		}
	if args.command == "review-template":
		doc = json.loads(args.discovery.read_text())
		reviews = {}
		for event in doc["events"]:
			for market in event.get("markets", []):
				if market.get("conditionId"):
					reviews[market["conditionId"]] = {
						"fixture_id": "",
						"category": "",
						"competition": "",
						"family_outcome": "",
						"regulation_time": False,
						"rules_sha256": hashlib.sha256(
							market.get("description", "").encode()
						).hexdigest(),
						"rules_url": f"https://polymarket.com/event/{event.get('slug', '')}",
						"reviewed_at": None,
						"reviewed_by": "",
						"question_for_review": market.get("question", ""),
						"rules_for_review": market.get("description", ""),
					}
		save_new(args.out, reviews)
		return {"unapproved_candidates": len(reviews), "path": str(args.out)}
	if args.command == "catalog-build":
		reviews = json.loads(args.reviews.read_text())
		contracts = [
			c
			for path in args.discovery
			for c in contracts_from_discovery(json.loads(path.read_text()), reviews)
		]
		save_new(args.out, [c.model_dump(mode="json") for c in contracts])
		return {"versions": len(contracts), "reviewed": sum(c.rules_reviewed for c in contracts)}
	if args.command == "select":
		from prediction_market_lab.universe import select_universe

		contracts = [Contract.model_validate(c) for c in json.loads(args.contracts.read_text())]
		selected, decisions = select_universe(
			contracts, timestamp(args.asof) or utc_now(), settings.capture
		)
		save_new(
			args.out,
			{"contracts": [c.model_dump(mode="json") for c in selected], "decisions": decisions},
		)
		return {"assets": len(selected), "decisions": decisions}
	if args.command in {"replay", "paper"}:
		from prediction_market_lab.replay import replay

		return replay(
			args.capture,
			args.out,
			strategy=getattr(args, "strategy", None),
			paper=paper_config(
				settings, getattr(args, "latency_ms", None), getattr(args, "queue_multiplier", None)
			),
			since=timestamp(args.since),
			until=timestamp(args.until),
			max_records=args.max_records,
		)
	if args.command == "research":
		from prediction_market_lab.reporting import report

		runs = (
			{p.parent.name: p.parent for p in args.paper_root.glob("*/manifest.json")}
			if args.paper_root
			else {}
		)
		summarizer = None
		if args.summarizer == "openai":
			from prediction_market_lab.live_evidence import SpendCap
			from prediction_market_lab.summarizer import OpenAISummarizer

			if args.spend_ledger is None:
				raise ValueError("--summarizer openai requires --spend-ledger")
			cap = SpendCap(args.spend_ledger, args.budget_usd)
			summarizer = OpenAISummarizer(cap, args.model)
		result = report(
			args.run,
			args.out,
			paper_runs=runs,
			evidence_path=args.evidence,
			research_config=settings.research,
			summarizer=summarizer,
		)
		spent = {"spent_usd": round(cap.spent, 4)} if summarizer else {}
		return {"report": result["report"], "incidents": result["incidents"], **spent}
	if args.command == "dashboard":
		app = Path(__file__).with_name("app.py")
		return {
			"exit_code": subprocess.call(
				[
					sys.executable,
					"-m",
					"streamlit",
					"run",
					str(app),
					"--server.address=127.0.0.1",
					f"--server.port={args.port}",
					"--server.headless=true",
					"--browser.gatherUsageStats=false",
					"--",
					str(args.workspace.resolve()),
				]
			)
		}
	if args.command == "verify":
		return (
			verify_run(args.path)
			if (args.path / "manifest.json").exists()
			else {"verified_raw_records": sum(1 for _ in read_archive(args.path))}
		)
	if args.command == "health":
		return json.loads((args.path / "health.json").read_text())
	if args.command == "pilot-report":
		from prediction_market_lab.research import coverage, load_table

		frame = load_table(args.path, "features")
		cov = coverage(frame)
		duration = (
			(frame.received_at.max() - frame.received_at.min()).total_seconds()
			if not frame.empty
			else 0
		)
		return {
			"observed_span_seconds": duration,
			"at_least_72h_span": duration >= 72 * 3600,
			"observed_only": bool(not frame.empty and set(frame.input_provenance) == {"observed"}),
			"coverage": cov.to_dict("records"),
			"gate": "requires reviewed matchday coverage and cost forecast; elapsed span alone does not pass",
		}
	if args.command == "backup":
		return backup(args.source, args.destination)
	if args.command == "restore":
		return restore(args.source, args.destination)
	if args.command == "backup-check":
		return restore_check(args.path)
	if args.command == "budget":
		return budget_forecast(
			args.raw_bytes,
			args.elapsed_seconds,
			server_usd=args.server_usd,
			storage_gb_usd=args.storage_gb_usd,
		)
	if args.command == "doctor":
		from importlib.metadata import version

		return {
			"python": sys.version.split()[0],
			"packages": {
				name: version(name)
				for name in ("polymarket-client", "duckdb", "streamlit", "pyarrow")
			},
			"live_trading": "not implemented",
			"network_checked": False,
		}
	raise ValueError("Unknown command")


def main() -> None:
	args = parser().parse_args()
	try:
		print(json.dumps(run(args), indent=2, default=str))
	except (ValueError, FileNotFoundError, FileExistsError) as exc:
		print(f"pml: {exc}", file=sys.stderr)
		raise SystemExit(2) from exc


if __name__ == "__main__":
	main()
