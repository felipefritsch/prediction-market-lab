"""Reviewed complete football families, selected using only known metadata."""

from collections import defaultdict
from datetime import datetime
from typing import Any

from prediction_market_lab.config import CaptureConfig
from prediction_market_lab.models import Contract


def select_universe(
	contracts: list[Contract], cutoff: datetime, limits: CaptureConfig
) -> tuple[list[Contract], list[dict[str, Any]]]:
	latest: dict[str, Contract] = {}
	for contract in sorted(contracts, key=lambda c: (c.known_at, c.version)):
		if contract.known_at <= cutoff:
			latest[contract.asset_id] = contract
	groups: dict[str, list[Contract]] = defaultdict(list)
	for contract in latest.values():
		groups[contract.fixture_id].append(contract)
	selected: list[Contract] = []
	decisions: list[dict[str, Any]] = []
	football_count, comparator_count = 0, 0
	ordered = sorted(
		groups.items(),
		key=lambda pair: (
			min(
				(abs((c.kickoff - cutoff).total_seconds()) for c in pair[1] if c.kickoff),
				default=float("inf"),
			),
			-max(c.volume_24h for c in pair[1]),
			pair[0],
		),
	)
	for fixture, group in ordered:
		is_football = all(c.category == "football" for c in group)
		reason = "selected"
		if not all(c.rules_reviewed for c in group):
			reason = "rules_not_reviewed"
		elif not all(c.accepting_orders is True for c in group):
			reason = "market_availability_unconfirmed"
		elif is_football:
			pairs: dict[str, set[str]] = defaultdict(set)
			for c in group:
				pairs[c.family_outcome].add(c.outcome.lower())
			if (
				len(group) != 6
				or set(pairs) != {"home", "draw", "away"}
				or any(v != {"yes", "no"} for v in pairs.values())
			):
				reason = "incomplete_1x2_family"
			elif len({c.condition_id for c in group}) != 3 or any(
				len({c.condition_id for c in group if c.family_outcome == family}) != 1
				for family in pairs
			):
				reason = "inconsistent_condition_mapping"
			elif not all(c.regulation_time and c.competition in {"EPL", "UCL"} for c in group):
				reason = "not_reviewed_regulation_time_epl_ucl"
			elif len({c.kickoff for c in group}) != 1 or group[0].kickoff is None:
				reason = "inconsistent_kickoff"
			elif football_count >= limits.max_fixtures:
				reason = "fixture_cap"
		else:
			if (
				len(group) != 2
				or len({c.condition_id for c in group}) != 1
				or len({c.category for c in group}) != 1
				or {c.outcome.lower() for c in group} != {"yes", "no"}
				or not all(c.category in {"macro", "politics"} for c in group)
			):
				reason = "not_binary_comparator"
			elif comparator_count >= limits.max_comparators:
				reason = "comparator_cap"
		if reason == "selected" and len(selected) + len(group) > limits.max_assets:
			reason = "asset_cap_complete_group_retained"
		if reason == "selected":
			selected.extend(group)
			football_count += int(is_football)
			comparator_count += int(not is_football)
		decisions.append(
			{
				"fixture_id": fixture,
				"selected": reason == "selected",
				"reason": reason,
				"asof": cutoff.isoformat(),
				"assets": sorted(c.asset_id for c in group),
			}
		)
	return selected, decisions
