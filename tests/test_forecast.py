import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from prediction_market_lab import forecast, scoring

UNIFORM = {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}


def test_rps_rewards_near_misses_on_the_ordered_scale():
	assert scoring.rps({"home": 1, "draw": 0, "away": 0}, "home") == 0
	assert scoring.rps({"home": 0, "draw": 1, "away": 0}, "home") == pytest.approx(0.5)
	assert scoring.rps({"home": 0, "draw": 0, "away": 1}, "home") == pytest.approx(1.0)
	assert scoring.brier(UNIFORM, "draw") == pytest.approx(2 / 3)


def test_pool_is_weighted_linear_and_clipped():
	pooled = forecast.pool(
		{"a": {"home": 1.0, "draw": 0.0, "away": 0.0}, "b": UNIFORM}, {"a": 3, "b": 1}
	)
	assert sum(pooled.values()) == pytest.approx(1)
	assert pooled["draw"] > 0.02 * 0.9 and pooled["home"] > pooled["away"]


def test_paper_trades_buy_only_clear_edges_and_pay_fees():
	book = {"home_yes": {"bid": 0.39, "ask": 0.40}, "away_no": {"bid": 0.5, "ask": 0.75}}
	trades = scoring.paper_trades(
		{"home": 0.5, "draw": 0.3, "away": 0.2}, book, "home", fee_rate=0.05
	)
	assert [(t["outcome"], t["side"]) for t in trades] == [("home", "yes"), ("away", "no")]
	home = trades[0]
	assert home["shares"] == pytest.approx(25)
	assert home["pnl"] == pytest.approx(25 - 10 - 25 * 0.05 * 0.4 * 0.6)


def test_freeze_chains_hashes(tmp_path):
	path = tmp_path / "forecasts.jsonl"
	first = forecast.freeze(path, {"fixture_id": "f", "slot": "t-24h"})
	second = forecast.freeze(path, {"fixture_id": "f", "slot": "t-1h"})
	assert second["previous_sha256"] == first["sha256"]
	assert forecast.done_slots(path) == {"f:t-24h", "f:t-1h"}


def league(seasons: int = 3) -> pd.DataFrame:
	"""A strong, a middling and a weak team playing each other repeatedly."""
	rows = []
	teams = {"Strong": 3, "Middle": 1, "Weak": 0}
	day = pd.Timestamp("2023-08-01")
	for _ in range(seasons * 20):
		for home in teams:
			for away in teams:
				if home != away:
					rows.append(
						{
							"HomeTeam": home,
							"AwayTeam": away,
							"FTHG": teams[home] + 1,
							"FTAG": teams[away],
							"division": "E0",
							"season": "2324",
							"date": day,
						}
					)
					day += pd.Timedelta(days=1)
	return pd.DataFrame(rows)


def test_statistical_members_rank_teams_sensibly():
	matches = league()
	elo = forecast.Elo().fit(matches)
	goal = forecast.GoalModel().fit(matches, datetime(2026, 1, 1, tzinfo=UTC))
	for model in (elo, goal):
		strong = model.predict("Strong", "Weak")
		weak = model.predict("Weak", "Strong")
		assert strong["home"] > weak["home"]
		assert sum(strong.values()) == pytest.approx(1)


def test_evaluate_scores_frozen_forecasts_against_results(tmp_path):
	path = tmp_path / "forecasts.jsonl"
	book = {f"{k}_{s}": {"bid": 0.3, "ask": 0.32} for k in forecast.OUTCOMES for s in ("yes", "no")}
	forecast.freeze(
		path,
		{
			"fixture_id": "f",
			"slot": "t-1h",
			"kickoff": "2026-10-17T14:00:00+00:00",
			"home": "Everton FC",
			"away": "Chelsea FC",
			"members": {"elo": {"home": 0.5, "draw": 0.3, "away": 0.2}},
			"ensemble": {"home": 0.5, "draw": 0.3, "away": 0.2},
			"market": {"book": book, "probabilities": UNIFORM},
			"fee_rate": 0.05,
		},
	)
	matches = pd.DataFrame(
		[
			{
				"HomeTeam": "Everton",
				"AwayTeam": "Chelsea",
				"FTHG": 2,
				"FTAG": 1,
				"date": pd.Timestamp("2026-10-17"),
				"AvgCH": 3.0,
				"AvgCD": 3.4,
				"AvgCA": 2.4,
			}
		]
	)
	report = forecast.evaluate(path, matches)
	slot = report["slots"]["t-1h"]
	assert slot["matches"] == 1
	assert slot["scores"]["ensemble"]["rps"] < slot["scores"]["market"]["rps"]
	assert slot["paper_trades"][0]["outcome"] == "home"
	assert json.dumps(report)


def test_family_pool_balances_statistical_and_language_models():
	members = {
		"elo": {"home": 0.6, "draw": 0.2, "away": 0.2},
		**{f"llm:m{i}": {"home": 0.2, "draw": 0.2, "away": 0.6} for i in range(6)},
	}
	assert forecast.family_pool(members)["home"] == pytest.approx(0.4)
	assert forecast.pool(members)["home"] < 0.3


def test_openrouter_output_parsing_tolerates_fences():
	text = 'Here:\n```json\n{"home": 0.5, "draw": 0.3, "away": 0.2, "reasoning": "r"}\n```'
	parsed = forecast.LLMForecast.model_validate_json(forecast.json_object(text))
	assert parsed.home == 0.5


def test_strip_odds_removes_betting_sentences_only():
	text = (
		"Reece James is a doubt for Saturday. Chelsea are 2.10 favourites with bookmakers. "
		"Everton are unbeaten at home. Polymarket gives Everton 29%."
	)
	kept, removed = forecast.strip_odds(text)
	assert kept == "Reece James is a doubt for Saturday. Everton are unbeaten at home."
	assert removed == 2


def test_market_aware_members_are_never_pooled():
	members = {
		"elo": {"home": 0.5, "draw": 0.3, "away": 0.2},
		"mkt:m": {"home": 0.1, "draw": 0.1, "away": 0.8},
	}
	assert forecast.pool(forecast.pooled(members))["home"] == pytest.approx(0.5)
	assert forecast.family_pool(members)["home"] == pytest.approx(0.5)


def test_slots_include_six_hours():
	kickoff = datetime(2026, 10, 17, 14, tzinfo=UTC)
	assert [s for s, _ in forecast.forecast_slots(kickoff)] == ["t-24h", "t-6h", "t-1h"]


def test_paper_fill_walks_ladder_while_edge_clears_and_reports_clv():
	book = {"home_yes": {"bid": 0.39, "ask": 0.40, "asks": [[0.40, 10], [0.45, 100], [0.48, 100]]}}
	trades = scoring.paper_trades(
		{"home": 0.5, "draw": 0.3, "away": 0.2},
		book,
		"away",
		fee_rate=0.0,
		closing={"home_yes": 0.44},
	)
	trade = trades[0]
	# 10 shares at 0.40, then $6 at 0.45; 0.48 no longer clears the 3-point threshold.
	assert trade["shares"] == pytest.approx(10 + 6 / 0.45)
	assert trade["filled_stake"] == pytest.approx(10)
	assert trade["clv"] == pytest.approx(0.44 - 10 / trade["shares"])
	assert trade["pnl"] == pytest.approx(-10)


def test_closing_price_is_last_snapshot_before_kickoff():
	kickoff = datetime(2026, 10, 17, 14, tzinfo=UTC)
	mids = {
		"t": [(kickoff.replace(hour=13), 0.4), (kickoff, 0.42), (kickoff.replace(hour=15), 0.9)]
	}
	closing = forecast.closing_prices({"kickoff": kickoff.isoformat()}, {"home_yes": "t"}, mids)
	assert closing == {"home_yes": 0.42}


def test_publish_hash_pushes_to_branch(tmp_path):
	import subprocess

	remote, work = tmp_path / "remote.git", tmp_path / "work"
	subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
	subprocess.run(["git", "init", "-q", "-b", "forecast-log", str(work)], check=True)
	for args in (
		["config", "user.email", "t@example.invalid"],
		["config", "user.name", "t"],
		["remote", "add", "origin", str(remote)],
		["commit", "-q", "--allow-empty", "-m", "start"],
		["push", "-q", "-u", "origin", "forecast-log"],
	):
		subprocess.run(["git", "-C", str(work), *args], check=True)
	forecast.publish_hash(work, {"fixture_id": "f", "slot": "t-1h", "sha256": "abc"})
	log = subprocess.run(
		["git", "-C", str(remote), "log", "--format=%s", "forecast-log"],
		check=True,
		capture_output=True,
		text=True,
	).stdout
	assert log.splitlines()[0] == "Forecast hash f t-1h"
