"""Deterministic, unrestricted demo tape. Nothing here is an observed market."""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from prediction_market_lab.models import Contract, Evidence, EvidenceClass, RawRecord
from prediction_market_lab.storage import Catalog, RawArchive, atomic_write, canonical


def generate(root: Path, *, seed: int = 2026, fixtures: int = 8) -> dict[str, Any]:
	if root.exists():
		raise FileExistsError("Synthetic capture output must be new")
	rng = random.Random(seed)
	start = datetime(2026, 1, 1, 12, tzinfo=UTC)
	sequence = 0
	contracts: list[Contract] = []
	evidence: list[Evidence] = []
	catalog = Catalog(root)
	with RawArchive(root, segment_records=500) as archive:

		def emit(
			at: datetime, payload: Any, source: str = "market", session: str = "synthetic-stream"
		) -> None:
			nonlocal sequence
			sequence += 1
			archive.append(
				RawRecord(
					record_id=f"synthetic:{seed}:{sequence}",
					source=source,
					session_id=session,
					sequence=sequence,
					received_at=at,
					monotonic_ns=int((at - start).total_seconds() * 1e9),
					payload=payload,
					provenance=EvidenceClass.SYNTHETIC,
				)
			)

		for f in range(fixtures):
			base = start + timedelta(days=f)
			fixture = f"synthetic-fixture-{f + 1:02d}"
			football = f < fixtures - 2
			families = ["home", "draw", "away"] if football else ["yes"]
			local = []
			for family in families:
				for outcome in ("Yes", "No"):
					contract = Contract(
						asset_id=f"{fixture}:{family}:{outcome}",
						condition_id=f"{fixture}:{family}",
						event_id=fixture,
						fixture_id=fixture,
						market_id=f"{fixture}:{family}",
						label=f"SYNTHETIC {fixture}: {family}",
						outcome=outcome,
						category="football"
						if football
						else ("macro" if f % 2 == 0 else "politics"),
						competition=("EPL" if f % 2 == 0 else "UCL") if football else "",
						known_at=base,
						kickoff=base + timedelta(seconds=300) if football else None,
						rules="SYNTHETIC ONLY. Regulation-time result; extra-time and penalties excluded."
						if football
						else "SYNTHETIC binary resolution.",
						rules_url="https://example.invalid/synthetic-rules",
						rules_reviewed=True,
						family_outcome=family,
						regulation_time=football,
						negative_risk=False,
						tick_size=Decimal("0.01"),
						min_order_size=Decimal("1"),
						fee_rate=Decimal("0.02"),
						seconds_delay=1,
						accepting_orders=True,
						protocol="synthetic-wire-v1",
						provenance=EvidenceClass.SYNTHETIC,
					)
					local.append(contract)
					contracts.append(contract)
					catalog.store_contract(contract)
					emit(
						base,
						{"event_type": "metadata", "contract": contract.model_dump(mode="json")},
						"catalog",
					)
			prices = {"home": 0.43, "draw": 0.27, "away": 0.30, "yes": 0.50}
			session = f"synthetic-{f}-initial"
			for second in range(5, 906, 5):
				at = base + timedelta(seconds=second)
				if second == 400:
					emit(
						at,
						{
							"event_type": "gap",
							"reason": "synthetic_disconnect",
							"assets": [c.asset_id for c in local],
						},
						session=session,
					)
					session = f"synthetic-{f}-reconnected"
					continue
				if second in {405, 410}:
					continue
				if second == 460:
					emit(
						at,
						{
							"fixture_id": fixture,
							"score": "1-0",
							"period": "1H",
							"status": "live",
							"timestamp": (at - timedelta(seconds=12)).isoformat(),
						},
						"sports",
					)
				if second == 475:
					emit(
						at,
						{
							"fixture_id": fixture,
							"score": "0-0",
							"correction": True,
							"status": "live",
						},
						"sports",
					)
				if second == 800:
					emit(
						at,
						{"event_type": "unknown", "synthetic_schema_test": True},
						session=session,
					)
				for family in families:
					step = rng.choice([-1, 0, 0, 1]) * 0.01
					prices[family] = round(min(0.8, max(0.12, prices[family] + step)), 2)
					if second == 450:
						prices[family] = min(0.85, prices[family] + 0.05)
					for outcome in ("Yes", "No"):
						asset = f"{fixture}:{family}:{outcome}"
						mid = prices[family] if outcome == "Yes" else 1 - prices[family]
						wide = 0.02 if 450 <= second < 480 else 0.01
						bid, ask = round(mid - wide, 2), round(mid + wide, 2)
						bq, aq = rng.randint(6, 60), rng.randint(6, 60)
						if 450 <= second < 480:
							bq, aq = 3, 4
						emit(
							at,
							{
								"event_type": "book",
								"asset_id": asset,
								"timestamp": int(at.timestamp() * 1000),
								"bids": [
									{"price": str(bid), "size": str(bq)},
									{"price": str(round(bid - 0.01, 2)), "size": "100"},
								],
								"asks": [
									{"price": str(ask), "size": str(aq)},
									{"price": str(round(ask + 0.01, 2)), "size": "100"},
								],
							},
							session=session,
						)
						if second % 15 == 0:
							emit(
								at,
								{
									"event_type": "last_trade_price",
									"asset_id": asset,
									"trade_id": f"{asset}:{second}",
									"price": str(bid),
									"size": "35",
									"side": "SELL",
									"side_semantics": "verified_aggressor",
								},
								session=session,
							)
				# Distinct first-observed times allow evidence cutoff tests.
				if second in {430, 500}:
					item = Evidence(
						evidence_id=f"{fixture}-e{second}",
						fixture_id=fixture,
						title="Synthetic source annotation (no real match)",
						url="https://example.invalid/synthetic-evidence",
						first_seen=at,
						published_at=at - timedelta(seconds=30),
						excerpt="Demonstration evidence only.",
						allow_redistribution=True,
						provenance=EvidenceClass.SYNTHETIC,
					)
					evidence.append(item)
					emit(at, {"event_type": "evidence", **item.model_dump(mode="json")}, "evidence")
			end = base + timedelta(seconds=910)
			for contract in local:
				wins = contract.family_outcome == "home" if football else True
				if contract.outcome == "No":
					wins = not wins
				emit(
					end,
					{
						"event_type": "settlement",
						"asset_id": contract.asset_id,
						"payout": int(wins),
					},
					"resolution",
				)
	catalog.close()
	atomic_write(root / "contracts.json", canonical([c.model_dump(mode="json") for c in contracts]))
	atomic_write(root / "evidence.json", canonical([e.model_dump(mode="json") for e in evidence]))
	result = {
		"evidence_class": "synthetic",
		"seed": seed,
		"fixtures": fixtures,
		"records": sequence,
		"purpose": "Software demonstration only; prices are NOT a calibrated market model or empirical evidence.",
	}
	atomic_write(root / "synthetic.json", canonical(result))
	return result
