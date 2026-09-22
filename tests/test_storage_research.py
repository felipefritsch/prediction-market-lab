from datetime import timedelta

import pandas as pd
import pytest

from prediction_market_lab.config import CaptureConfig
from prediction_market_lab.incidents import evidence_asof, explain
from prediction_market_lab.models import Evidence
from prediction_market_lab.operations import backup, restore
from prediction_market_lab.replay import replay, update_metadata
from prediction_market_lab.research import chronological_split, labels
from prediction_market_lab.storage import Catalog, RawArchive, read_archive, verify_run
from prediction_market_lab.universe import select_universe
from tests.conftest import START, raw, snapshot


def test_journal_partial_tail_recovery_and_single_writer(tmp_path):
	archive = RawArchive(tmp_path)
	archive.append(snapshot())
	with pytest.raises(RuntimeError):
		RawArchive(tmp_path)
	# Emulate a crash after a committed line and a torn last write.
	archive.handle.write(b'{"partial":')
	archive.handle.flush()
	archive.handle.close()
	archive.lock.close()
	with RawArchive(tmp_path) as recovered:
		recovered.append(snapshot(1))
	assert len(list(read_archive(tmp_path))) == 2


def test_only_torn_line_can_recover(tmp_path):
	(tmp_path / "raw").mkdir()
	(tmp_path / "raw" / "journal.pending").write_bytes(b'{"incomplete":')
	with RawArchive(tmp_path) as archive:
		archive.append(snapshot())
	assert len(list(read_archive(tmp_path))) == 1


def test_checksum_tamper_detected(tmp_path):
	with RawArchive(tmp_path) as archive:
		archive.append(snapshot())
	next((tmp_path / "raw").glob("*.gz")).write_bytes(b"bad")
	with pytest.raises(ValueError, match="Checksum"):
		list(read_archive(tmp_path))


def test_receipt_clock_reversal_keeps_journal_order(tmp_path):
	with RawArchive(tmp_path, 1) as archive:
		archive.append(snapshot(2))
		archive.append(snapshot(1))
	assert [r.received_at for r in read_archive(tmp_path)] == [
		START + timedelta(seconds=2),
		START + timedelta(seconds=1),
	]


def test_backup_restore_catalog_asof_and_repeatability(tmp_path, contract):
	root = tmp_path / "capture"
	catalog = Catalog(root)
	catalog.store_contract(contract)
	future = contract.model_copy(update={"known_at": START + timedelta(days=1), "rules": "new"})
	catalog.store_contract(future)
	assert catalog.contracts_asof(START)[0].rules == contract.rules
	with RawArchive(root) as archive:
		archive.append(
			raw(
				{"event_type": "metadata", "contract": contract.model_dump(mode="json")},
				source="catalog",
			)
		)
		archive.append(snapshot(1))
	catalog.close()
	backup(root, tmp_path / "backup")
	assert restore(tmp_path / "backup", tmp_path / "restored")["restored_records"] == 2
	for name in ("one", "two"):
		replay(root, tmp_path / name)
	first, second = verify_run(tmp_path / "one"), verify_run(tmp_path / "two")
	assert first["input_sha256"] == second["input_sha256"]
	assert first["files"] == second["files"]
	with pytest.raises(FileExistsError):
		replay(root, tmp_path / "one")


def test_evidence_revision_and_future_cutoffs():
	base = dict(
		evidence_id="news",
		fixture_id="f",
		title="Original",
		url="https://example.org",
		first_seen=START,
		published_at=START - timedelta(seconds=10),
	)
	old = Evidence(**base)
	revision = old.model_copy(
		update={
			"title": "Corrected",
			"first_seen": START + timedelta(seconds=30),
			"updated_at": START + timedelta(seconds=20),
		}
	)
	assert evidence_asof([old, revision], "f", START + timedelta(seconds=10))[0].title == "Original"
	assert (
		evidence_asof([old, revision], "f", START + timedelta(seconds=31))[0].title == "Corrected"
	)
	assert evidence_asof([old], "f", START - timedelta(seconds=1)) == []


def test_optional_summarizer_failure_and_unsupported_citations():
	packet = {"incident_id": "i", "facts": {}, "evidence": []}

	def fail(_):
		raise RuntimeError("provider failed")

	assert explain(packet, fail)["method"] == "template"
	assert (
		explain(
			packet,
			lambda _: {
				"status": "candidate_explanations",
				"claims": [
					{
						"text": "Made up",
						"source_ids": ["missing"],
						"status": "candidate_explanation",
					}
				],
			},
		)["method"]
		== "template"
	)


def test_label_does_not_cross_recovery_or_fill_future_gap():
	frame = pd.DataFrame(
		{
			"asset_id": ["a"] * 5,
			"received_at": [START + timedelta(seconds=s) for s in (0, 5, 10, 15, 20)],
			"midpoint": [0.5, 0.51, None, 0.7, 0.71],
			"quality": ["usable", "usable", "recovering", "usable", "usable"],
			"epoch": [1, 1, 1, 2, 2],
		}
	)
	result = labels(frame, horizons=(10,))
	assert result.delta_10s_pp.isna().all()
	assert result.next_move_up.iloc[0] == 1
	assert pd.isna(result.next_move_up.iloc[1])


def test_fixed_horizon_labels_handle_microsecond_datetime_storage():
	frame = pd.DataFrame(
		{
			"asset_id": ["a"] * 4,
			"received_at": pd.to_datetime(
				[START + timedelta(seconds=s) for s in (0, 5, 10, 15)]
			).as_unit("us"),
			"midpoint": [0.5, 0.51, 0.52, 0.53],
			"quality": ["usable"] * 4,
			"epoch": [1] * 4,
			"tick_size": [0.01] * 4,
		}
	)
	result = labels(frame, horizons=(10,))
	assert result.delta_10s_pp.iloc[0] == pytest.approx(2)
	assert result.delta_10s_ticks.iloc[0] == pytest.approx(2)
	assert result.label_end_10s.iloc[0] == START + timedelta(seconds=10)


def test_chronological_fixture_split_purges_overlapping_labels():
	frame = pd.DataFrame(
		{
			"fixture_id": [f"f{i}" for i in range(6)],
			"received_at": [START + timedelta(minutes=i * 10) for i in range(6)],
			"label_end_300s": [START + timedelta(minutes=i * 10 + 5) for i in range(6)],
		}
	)
	train, test, audit = chronological_split(frame)
	assert set(train.fixture_id).isdisjoint(set(test.fixture_id))
	assert train.label_end_300s.max() < pd.Timestamp(audit["train_label_cutoff"])


def test_rule_change_and_postponement_do_not_rewrite_old_contract(contract):
	updated = update_metadata(
		contract,
		{
			"description": "Revised rules",
			"gameStartTime": "2026-02-01T12:00:00Z",
			"feesEnabled": False,
		},
		START + timedelta(seconds=10),
	)
	assert not updated.rules_reviewed and updated.fee_rate == 0
	assert updated.kickoff > START and contract.kickoff is None
	assert contract.rules == "Synthetic rules"


def test_complete_football_families_and_cap(contract):
	group = []
	for family in ("home", "draw", "away"):
		for outcome in ("Yes", "No"):
			group.append(
				contract.model_copy(
					update={
						"asset_id": f"{family}-{outcome}",
						"condition_id": family,
						"family_outcome": family,
						"outcome": outcome,
						"category": "football",
						"competition": "EPL",
						"regulation_time": True,
						"kickoff": START + timedelta(hours=1),
					}
				)
			)
	assert len(select_universe(group, START, CaptureConfig())[0]) == 6
	assert not select_universe(group[:-1], START, CaptureConfig())[0]
	assert not select_universe(group, START, CaptureConfig(max_assets=5))[0]
	assert not select_universe(
		[c.model_copy(update={"known_at": START + timedelta(seconds=1)}) for c in group],
		START,
		CaptureConfig(),
	)[0]
