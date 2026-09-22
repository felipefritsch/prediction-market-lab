"""Reusable, quality-aware research calculations. No network calls or hidden data."""

import json
from functools import partial
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def load_table(run: Path, name: str) -> pd.DataFrame:
	if name not in {
		"features",
		"events",
		"contracts",
		"trades",
		"depth",
		"orders",
		"fills",
		"ledger",
		"canonical_trades",
		"open_interest",
	}:
		raise ValueError("Unknown table")
	paths = list((run / "tables" / name).rglob("*.parquet"))
	if not paths:
		return pd.DataFrame()
	with duckdb.connect(":memory:") as connection:
		frame = connection.execute(
			"SELECT * FROM read_parquet(?, union_by_name=true)", [[str(p) for p in sorted(paths)]]
		).df()
	if "_empty" in frame and len(frame.columns) == 1:
		return pd.DataFrame()
	for column in ("received_at", "source_at", "known_at", "kickoff", "submitted_at", "ready_at"):
		if column in frame:
			frame[column] = pd.to_datetime(frame[column], utc=True, format="ISO8601")
	return frame


def coverage(features: pd.DataFrame) -> pd.DataFrame:
	if features.empty:
		return pd.DataFrame()
	frame = features.sort_values(["asset_id", "received_at"]).copy()
	duration = (
		(frame.groupby("asset_id").received_at.shift(-1) - frame.received_at)
		.dt.total_seconds()
		.fillna(0)
		.clip(lower=0)
	)
	frame["seconds"] = duration
	# Silence beyond the heartbeat timeout cannot count as usable coverage.
	frame["usable_seconds"] = np.where(frame.quality == "usable", duration.clip(upper=35), 0)
	result = frame.groupby(["fixture_id", "category"], as_index=False).agg(
		assets=("asset_id", "nunique"),
		observations=("quality", "size"),
		first_received=("received_at", "min"),
		last_received=("received_at", "max"),
		asset_seconds=("seconds", "sum"),
		usable_asset_seconds=("usable_seconds", "sum"),
	)
	result["usable_fraction"] = result.usable_asset_seconds / result.asset_seconds.replace(
		0, np.nan
	)
	return result


def labels(
	features: pd.DataFrame, horizons: tuple[int, ...] = (10, 60, 300), tolerance: float = 5
) -> pd.DataFrame:
	"""Future labels are analysis-only. They never enter the replay/strategy engine.

	A fixed-horizon label uses the last observation at or before the horizon,
	within five seconds, in the same uninterrupted book epoch. Quiet/incomplete
	endpoints are excluded explicitly rather than interpolated or backfilled.
	"""
	if features.empty:
		return pd.DataFrame()
	parts = []
	for _, group in features.groupby("asset_id", sort=True):
		group = (
			group.sort_values("received_at", kind="stable")
			.drop_duplicates("received_at", keep="last")
			.copy()
		)
		times = group.received_at.dt.as_unit("ns").astype("int64").to_numpy() / 1e9
		mids = group.midpoint.to_numpy(dtype=float)
		epoch = group.epoch.to_numpy()
		valid = (group.quality == "usable").to_numpy() & np.isfinite(mids)
		for horizon in horizons:
			targets = times + horizon
			index = np.searchsorted(times, targets, side="right") - 1
			good = (index >= 0) & (index < len(times)) & (targets <= times[-1])
			index = np.clip(index, 0, len(times) - 1)
			good &= (
				valid
				& valid[index]
				& (epoch == epoch[index])
				& ((targets - times[index]) <= tolerance)
				& (index > np.arange(len(times)))
			)
			group[f"delta_{horizon}s_pp"] = np.where(good, 100 * (mids[index] - mids), np.nan)
			if "tick_size" in group:
				group[f"delta_{horizon}s_ticks"] = group[f"delta_{horizon}s_pp"] / (
					100 * group.tick_size
				)
			group[f"label_end_{horizon}s"] = pd.to_datetime(
				np.where(good, targets, np.nan), unit="s", utc=True
			)
		next_move = np.full(len(group), np.nan)
		move_end = np.full(len(group), np.nan)
		# Group runs of equal mids for linear-time next-move lookup within an epoch.
		next_index: int | None = None
		for i in range(len(group) - 2, -1, -1):
			if not valid[i] or not valid[i + 1] or epoch[i] != epoch[i + 1]:
				next_index = None
			elif mids[i + 1] != mids[i]:
				next_index = i + 1
			if next_index is not None and times[next_index] - times[i] <= 300:
				next_move[i] = float(mids[next_index] > mids[i])
				move_end[i] = times[next_index]
		group["next_move_up"] = next_move
		group["next_move_end"] = pd.to_datetime(move_end, unit="s", utc=True)
		parts.append(group)
	return pd.concat(parts, ignore_index=True)


def chronological_split(
	frame: pd.DataFrame, *, holdout: float = 0.3, embargo_seconds: int = 300
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
	starts = frame.groupby("fixture_id").received_at.min().sort_values(kind="stable")
	if len(starts) < 4:
		return (
			frame.iloc[:0],
			frame.iloc[:0],
			{"status": "insufficient_events", "events": len(starts)},
		)
	n_test = max(1, int(np.ceil(len(starts) * holdout)))
	test_ids = starts.index[-n_test:]
	test = frame[frame.fixture_id.isin(test_ids)].copy()
	cutoff = test.received_at.min() - pd.Timedelta(seconds=embargo_seconds)
	train = frame[~frame.fixture_id.isin(test_ids) & (frame.received_at < cutoff)].copy()
	# Purge the actual label endpoint as well as imposing a horizon embargo.
	for column in [str(c) for c in frame if str(c).startswith("label_end_")] + ["next_move_end"]:
		if column in train:
			train = train[train[column].isna() | (train[column] < cutoff)]
	return (
		train,
		test,
		{
			"status": "ok",
			"train_events": sorted(train.fixture_id.unique()),
			"test_events": sorted(test.fixture_id.unique()),
			"train_rows": len(train),
			"test_rows": len(test),
			"train_label_cutoff": cutoff.isoformat(),
			"embargo_seconds": embargo_seconds,
		},
	)


def event_interval(
	values: pd.Series, *, seed: int = 2026, repetitions: int = 200
) -> dict[str, Any]:
	"""Equal-weight event means, bootstrap events rather than ticks."""
	array = values.dropna().to_numpy(dtype=float)
	if not len(array):
		return {"mean": None, "lower": None, "upper": None, "events": 0}
	result: dict[str, Any] = {
		"mean": float(array.mean()),
		"events": len(array),
		"lower": None,
		"upper": None,
	}
	if len(array) >= 5:
		rng = np.random.default_rng(seed)
		samples = rng.choice(array, (repetitions, len(array)), replace=True).mean(axis=1)
		result.update(
			lower=float(np.quantile(samples, 0.025)), upper=float(np.quantile(samples, 0.975))
		)
	else:
		result["uncertainty_status"] = "fewer_than_five_events_no_interval"
	return result


def liquidity_study(
	features: pd.DataFrame, research_config: dict[str, Any] | None = None
) -> dict[str, Any]:
	if features.empty or "midpoint" not in features:
		return {"status": "insufficient_data"}
	config = research_config or {}
	horizons = tuple(int(h) for h in config.get("horizons_seconds", [10, 60, 300]))
	interval = partial(
		event_interval,
		seed=int(config.get("random_seed", 2026)),
		repetitions=int(config.get("bootstrap_samples", 200)),
	)
	data = labels(features, horizons=horizons)
	# YES assets only: do not count a binary market's complement as another sample.
	data = data[(data.outcome.str.lower() == "yes") & (data.quality == "usable")].copy()
	data["phase"] = np.where(
		data.seconds_to_kickoff.isna(),
		"not_football",
		np.where(data.seconds_to_kickoff > 0, "before_kickoff", "after_scheduled_kickoff"),
	)
	liquidity = data.groupby(["fixture_id", "category", "phase"], as_index=False).agg(
		spread_pp=("spread", lambda s: float(s.mean() * 100)),
		depth_shares=("bid_depth_2pp", "mean"),
		ask_depth_shares=("ask_depth_2pp", "mean"),
		trade_notifications_60s=("trade_notifications_60s", "mean"),
		observations=("asset_id", "size"),
	)
	train, test, split = chronological_split(
		data, holdout=float(config.get("holdout_fraction", 0.3)), embargo_seconds=max(horizons)
	)
	metrics = []
	for horizon in horizons:
		target = f"delta_{horizon}s_pp"
		tr = train.dropna(subset=[target, "ofi_10s", "imbalance"])
		te = test.dropna(subset=[target, "ofi_10s", "imbalance"])
		if len(tr) < 20 or len(te) < 5:
			metrics.append({"horizon_seconds": horizon, "status": "insufficient_labelled_data"})
			continue
		model = make_pipeline(StandardScaler(), Ridge(alpha=10))
		model.fit(tr[["ofi_10s", "imbalance", "spread"]], tr[target])
		predictions = {
			"midpoint_no_change": np.zeros(len(te)),
			"imbalance_weighted_midpoint": 100 * (te.weighted_midpoint - te.midpoint).to_numpy(),
			"regularized_ofi_regression": model.predict(te[["ofi_10s", "imbalance", "spread"]]),
		}
		for name, pred in predictions.items():
			errors = pd.DataFrame(
				{"fixture_id": te.fixture_id, "squared_error": (pred - te[target].to_numpy()) ** 2}
			)
			event_rmse = errors.groupby("fixture_id").squared_error.mean().pow(0.5)
			metrics.append(
				{
					"model": name,
					"horizon_seconds": horizon,
					"metric": "event_mean_rmse_probability_points",
					**interval(event_rmse),
					"rows": len(te),
				}
			)
	classification: dict[str, Any] = {"status": "insufficient_data"}
	tr, te = (
		train.dropna(subset=["next_move_up", "imbalance"]),
		test.dropna(subset=["next_move_up", "imbalance"]),
	)
	if len(tr) >= 20 and len(te) >= 5 and tr.next_move_up.nunique() == 2:
		model = make_pipeline(
			StandardScaler(), LogisticRegression(C=0.1, random_state=2026, max_iter=1000)
		)
		model.fit(tr[["imbalance"]], tr.next_move_up)
		probabilities = {
			"constant_train_frequency": np.full(len(te), tr.next_move_up.mean()),
			"imbalance_logistic": model.predict_proba(te[["imbalance"]])[:, 1],
		}
		classification = {"status": "ok", "metrics": []}
		for name, probability in probabilities.items():
			brier = (
				pd.Series((probability - te.next_move_up.to_numpy()) ** 2, index=te.index)
				.groupby(te.fixture_id)
				.mean()
			)
			classification["metrics"].append(
				{"model": name, "metric": "next_move_brier", **interval(brier)}
			)
	return {
		"status": "ok",
		"evidence_class": "derived",
		"input_provenance": sorted(data.input_provenance.dropna().unique()),
		"coverage": coverage(features).to_dict("records"),
		"liquidity_by_event_phase": liquidity.to_dict("records"),
		"split": split,
		"forecast_metrics": metrics,
		"classification": classification,
		"parameters": config,
		"label_availability": {
			f"{h}s": int(data[f"delta_{h}s_pp"].notna().sum()) for h in horizons
		},
		"limitations": [
			"Synthetic inputs demonstrate software, not predictive edge.",
			"Five-second endpoint tolerance excludes sparse markets; report resulting selection.",
			"Event means are equally weighted; fewer than five held-out events yields no uncertainty interval.",
			"After scheduled kickoff is a timing bucket, not confirmed in-play status.",
			"No model selection on the held-out events; tick labels are dependent.",
		],
	}


def shock_study(features: pd.DataFrame, events: pd.DataFrame) -> dict[str, Any]:
	if features.empty:
		return {"status": "insufficient_data", "episodes": []}
	features = features[
		(features.quality == "usable") & (features.outcome.str.lower() == "yes")
	].copy()
	anchors: list[tuple[str, Any, str]] = []
	for fixture, frame in features.groupby("fixture_id"):
		# Use the earliest THEN-known schedule. Revised schedules form separate audit events.
		known = frame.sort_values("received_at").dropna(subset=["kickoff"])
		if not known.empty and known.iloc[0].received_at <= known.iloc[0].kickoff:
			anchors.append((str(fixture), known.iloc[0].kickoff, "scheduled_kickoff"))
	if not events.empty:
		for tuple_row in events[events.kind == "score"].itertuples():
			row: Any = tuple_row
			anchors.append((str(row.fixture_id), row.received_at, "received_score_update"))
	episodes = []
	for fixture, at, kind in anchors:
		frame = features[features.fixture_id == fixture]
		before = frame[
			(frame.received_at >= at - pd.Timedelta(seconds=60)) & (frame.received_at < at)
		]
		after = frame[
			(frame.received_at >= at) & (frame.received_at < at + pd.Timedelta(seconds=60))
		]
		if before.empty or after.empty:
			continue
		spread_change = float((after.spread.mean() - before.spread.mean()) * 100)
		depth_change = float(after.bid_depth_2pp.mean() - before.bid_depth_2pp.mean())
		# Same-asset local comparison window, 3 minutes earlier, selected mechanically.
		control_at = at - pd.Timedelta(seconds=180)
		cb = frame[
			(frame.received_at >= control_at - pd.Timedelta(seconds=60))
			& (frame.received_at < control_at)
		]
		ca = frame[
			(frame.received_at >= control_at)
			& (frame.received_at < control_at + pd.Timedelta(seconds=60))
		]
		control_change = (
			float((ca.spread.mean() - cb.spread.mean()) * 100)
			if not cb.empty and not ca.empty
			else None
		)
		later = frame[
			(frame.received_at >= at + pd.Timedelta(seconds=60))
			& (frame.received_at <= at + pd.Timedelta(seconds=300))
		]
		episodes.append(
			{
				"fixture_id": fixture,
				"at": at.isoformat(),
				"anchor": kind,
				"spread_change_pp": spread_change,
				"bid_depth_change_shares": depth_change,
				"local_comparison_spread_change_pp": control_change,
				"adjusted_spread_change_pp": spread_change - control_change
				if control_change is not None
				else None,
				"mean_abs_repricing_pp": float(
					abs(
						after.groupby("asset_id").midpoint.mean()
						- before.groupby("asset_id").midpoint.mean()
					).mean()
					* 100
				),
				"later_spread_pp": float(later.spread.mean() * 100) if not later.empty else None,
				"before_rows": len(before),
				"after_rows": len(after),
			}
		)
	intervals = {}
	if episodes:
		frame = pd.DataFrame(episodes)
		for anchor_kind, group in frame.groupby("anchor"):
			intervals[str(anchor_kind)] = event_interval(
				group.groupby("fixture_id").spread_change_pp.mean()
			)
	return {
		"status": "ok" if episodes else "insufficient_windows",
		"evidence_class": "derived",
		"episodes": episodes,
		"event_uncertainty": intervals,
		"limitations": [
			"Score receipt is not goal time; corrections remain separate observations.",
			"Local comparison periods are descriptive controls, not causal identification or randomized matches.",
			"Means are conditional on usable observed books; inspect coverage before interpreting a change.",
			"Overlapping score windows are dependent; uncertainty aggregates within fixture.",
		],
	}


def execution_study(runs: dict[str, Path]) -> dict[str, Any]:
	scenarios = []
	for name, path in sorted(runs.items()):
		summary = json.loads((path / "summary.json").read_text())
		manifest = json.loads((path / "manifest.json").read_text())
		features, fills = load_table(path, "features"), load_table(path, "fills")
		markouts = []
		for tuple_fill in fills.itertuples():
			fill: Any = tuple_fill
			after = features[
				(features.asset_id == fill.asset_id)
				& (features.quality == "usable")
				& (features.received_at >= fill.received_at + pd.Timedelta(seconds=60))
				& (features.received_at <= fill.received_at + pd.Timedelta(seconds=65))
			]
			if len(after):
				markouts.append(
					(float(after.iloc[0].midpoint) - float(fill.price))
					* 100
					* (1 if fill.side == "BUY" else -1)
				)
		scenarios.append(
			{
				"scenario": name,
				"strategy": manifest["strategy"],
				**manifest["paper_config"],
				**summary["paper"],
				"mean_60s_markout_pp": float(np.mean(markouts)) if markouts else None,
				"markout_fills": len(markouts),
			}
		)
	return {
		"status": "ok" if scenarios else "no_paper_runs",
		"evidence_class": "simulated",
		"scenarios": scenarios,
		"limitations": [
			"Passive queue scenarios are not lower/upper guarantees; cancellations never advance queues.",
			"No impact, endogenous response or hidden queue reconstruction; book replay assumes exogenous venue observations.",
			"Unverified-side notifications do not produce passive fills; zero fills may reflect missing information.",
			"Rewards/rebates are zero; every purchase consumes virtual cash and acquires cost basis.",
			"P&L on synthetic outcomes has no economic interpretation.",
		],
	}
