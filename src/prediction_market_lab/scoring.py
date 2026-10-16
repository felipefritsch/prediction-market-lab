"""Deterministic evaluation of frozen 1X2 forecasts against results and market prices."""

import math
from typing import Any

import numpy as np

OUTCOMES = ("home", "draw", "away")


def rps(p: dict[str, float], result: str) -> float:
	"""Ranked probability score for the ordered outcomes home < draw < away (lower is better)."""
	y = [float(result == k) for k in OUTCOMES]
	cum_p, cum_y, total = 0.0, 0.0, 0.0
	for k, label in zip(OUTCOMES[:-1], y[:-1], strict=True):
		cum_p += p[k]
		cum_y += label
		total += (cum_p - cum_y) ** 2
	return total / (len(OUTCOMES) - 1)


def brier(p: dict[str, float], result: str) -> float:
	return sum((p[k] - float(result == k)) ** 2 for k in OUTCOMES)


def log_loss(p: dict[str, float], result: str) -> float:
	return -math.log(max(p[result], 1e-6))


SCORES = {"rps": rps, "brier": brier, "log_loss": log_loss}


def calibration_error(pairs: list[tuple[dict[str, float], str]], bins: int = 10) -> float:
	"""One-vs-rest expected calibration error over all three outcomes."""
	rows = [(p[k], float(r == k)) for p, r in pairs for k in OUTCOMES]
	if not rows:
		return float("nan")
	error = 0.0
	for b in range(bins):
		inside = [
			(q, y) for q, y in rows if b / bins <= q < (b + 1) / bins or (b == bins - 1 and q == 1)
		]
		if inside:
			error += (
				len(inside)
				/ len(rows)
				* abs(np.mean([q for q, _ in inside]) - np.mean([y for _, y in inside]))
			)
	return float(error)


def js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
	m = {k: (p[k] + q[k]) / 2 for k in OUTCOMES}

	def kl(a: dict[str, float]) -> float:
		return sum(a[k] * math.log(a[k] / m[k]) for k in OUTCOMES if a[k] > 0)

	return (kl(p) + kl(q)) / 2


def paired_bootstrap(
	a: list[float], b: list[float], samples: int = 2000, seed: int = 2026
) -> dict[str, float]:
	"""Mean of a-b with a 95% interval; negative favours a for loss-type scores."""
	diff = np.array(a) - np.array(b)
	if len(diff) == 0:
		return {"mean": float("nan"), "low": float("nan"), "high": float("nan"), "n": 0}
	rng = np.random.default_rng(seed)
	means = rng.choice(diff, size=(samples, len(diff)), replace=True).mean(axis=1)
	return {
		"mean": float(diff.mean()),
		"low": float(np.quantile(means, 0.025)),
		"high": float(np.quantile(means, 0.975)),
		"n": len(diff),
	}


def summarize(
	forecasts: dict[str, list[dict[str, float]]], results: list[str], benchmark: str
) -> dict[str, Any]:
	"""Scores per forecaster, paired differences against the benchmark, calibration."""
	table: dict[str, Any] = {}
	for name, rows in forecasts.items():
		table[name] = {
			"n": len(rows),
			**{
				s: float(np.mean([f(p, r) for p, r in zip(rows, results, strict=True)]))
				for s, f in SCORES.items()
			},
			"calibration_error": calibration_error(list(zip(rows, results, strict=True))),
		}
		if name != benchmark and benchmark in forecasts:
			table[name]["rps_minus_" + benchmark] = paired_bootstrap(
				[rps(p, r) for p, r in zip(rows, results, strict=True)],
				[rps(p, r) for p, r in zip(forecasts[benchmark], results, strict=True)],
			)
	return table


def diversity(members: dict[str, list[dict[str, float]]]) -> dict[str, dict[str, float]]:
	"""Mean pairwise Jensen-Shannon divergence, the diversity measure of Aitchison et al."""
	names = sorted(members)
	return {
		a: {
			b: float(
				np.mean([js_divergence(p, q) for p, q in zip(members[a], members[b], strict=True)])
			)
			for b in names
		}
		for a in names
	}


def leave_one_out(
	members: dict[str, list[dict[str, float]]], results: list[str]
) -> dict[str, float]:
	"""Increase in the equal-weight pool's mean RPS when each member is removed."""

	def pooled_rps(names: list[str]) -> float:
		scores = []
		for i, r in enumerate(results):
			p = {k: float(np.mean([members[n][i][k] for n in names])) for k in OUTCOMES}
			scores.append(rps(p, r))
		return float(np.mean(scores))

	full = pooled_rps(sorted(members))
	return {
		n: pooled_rps([m for m in sorted(members) if m != n]) - full
		for n in members
		if len(members) > 1
	}


def paper_trades(
	forecast: dict[str, float],
	book: dict[str, dict[str, Any]],
	result: str,
	*,
	fee_rate: float,
	threshold: float = 0.03,
	stake: float = 10.0,
	closing: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
	"""Buy YES (or NO) when the forecast clears the ask by `threshold`; hold to settlement.

	`book` maps "home_yes", "home_no", ... to {"bid", "ask", "asks": [[price, size], ...]}.
	With a recorded ladder, the order walks levels only while each price still clears the
	threshold and stops at `stake`; without one it assumes the whole stake fills at the best ask.
	Taker fee per share is fee_rate * price * (1 - price), as in the paper broker. `closing`
	maps the same keys to the midpoint at kickoff, giving closing-line value per trade.
	"""
	trades = []
	for k in OUTCOMES:
		for side, prob, wins in (
			("yes", forecast[k], result == k),
			("no", 1 - forecast[k], result != k),
		):
			key = f"{k}_{side}"
			quote = book.get(key) or {}
			ask = quote.get("ask")
			if ask is None or not 0 < ask < 1 or prob - ask < threshold:
				continue
			ladder = quote.get("asks") or [[ask, float("inf")]]
			shares = spent = fee = 0.0
			for price, size in ladder:
				if prob - price < threshold or spent >= stake:
					break
				take = min(size, (stake - spent) / price)
				shares += take
				spent += take * price
				fee += take * fee_rate * price * (1 - price)
			if shares == 0:
				continue
			average = spent / shares
			close = (closing or {}).get(key)
			trades.append(
				{
					"outcome": k,
					"side": side,
					"ask": ask,
					"average_price": average,
					"forecast": prob,
					"edge": prob - average,
					"shares": shares,
					"filled_stake": spent,
					"depth_checked": "asks" in quote,
					"fee": fee,
					"pnl": shares * float(wins) - spent - fee,
					"closing_mid": close,
					"clv": None if close is None else close - average,
				}
			)
	return trades
