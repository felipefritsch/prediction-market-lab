import asyncio
import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from prediction_market_lab import cli, forecast
from prediction_market_lab.operations import (
	backup_after_slot,
	backup_files,
	backup_root,
	backup_status,
	timestamped_backup,
)

NOW = datetime(2026, 10, 17, 5, 50, 30, tzinfo=UTC)


def research_files(tmp_path):
	files = []
	for name, text in (("forecasts.jsonl", '{"sha256": "a"}\n'), ("spend.jsonl", '{"usd": 1}\n')):
		path = tmp_path / "live" / name
		path.parent.mkdir(exist_ok=True)
		path.write_text(text)
		files.append(path)
	return files


def test_backup_root_needs_an_absolute_path_and_has_no_default(tmp_path):
	with pytest.raises(ValueError, match="PML_BACKUP_DIR"):
		backup_root({})
	with pytest.raises(ValueError, match="absolute"):
		backup_root({"PML_BACKUP_DIR": "backups"})
	assert backup_root({"PML_BACKUP_DIR": str(tmp_path)}) == tmp_path


def test_timestamped_backup_is_verified_and_never_creates_its_root(tmp_path):
	files = research_files(tmp_path)
	root = tmp_path / "offsite"
	with pytest.raises(FileNotFoundError):
		timestamped_backup(root, files, now=NOW)
	assert not root.exists()
	root.mkdir()
	result = timestamped_backup(root, files, now=NOW - timedelta(hours=3))
	folder = root / "20261017T025030Z"
	assert result["backup"] == str(folder)
	assert (folder / "forecasts.jsonl").read_text() == files[0].read_text()
	assert json.loads((folder / "verified.json").read_text())["created_at"].startswith(
		"2026-10-17T02:50:30"
	)
	with pytest.raises(ValueError, match="reserved"):
		backup_files(tmp_path / "other", [folder / "verified.json"])


def test_slot_backup_reports_failures_instead_of_raising(tmp_path):
	files = research_files(tmp_path)
	assert backup_after_slot(tmp_path / "unmounted", files, now=NOW).startswith("BACKUP FAILED")
	(tmp_path / "offsite").mkdir()
	line = json.loads(backup_after_slot(tmp_path / "offsite", [*files, tmp_path / "no"], now=NOW))
	assert line["files"] == ["forecasts.jsonl", "spend.jsonl"]


def test_backup_status_ages_the_newest_backup_that_still_verifies(tmp_path):
	files = research_files(tmp_path)
	root = tmp_path / "offsite"
	root.mkdir()
	assert backup_status(root, max_age_hours=6, now=NOW)["status"] == "none"
	timestamped_backup(root, files, now=NOW - timedelta(hours=8))
	stale = backup_status(root, max_age_hours=6, now=NOW)
	assert (stale["status"], stale["age_hours"]) == ("stale", 8)
	timestamped_backup(root, files, now=NOW - timedelta(hours=1))
	assert backup_status(root, max_age_hours=6, now=NOW)["status"] == "ok"
	# An unverified folder (no verified.json) never counts, and a damaged newest one falls back.
	(root / "partial").mkdir()
	(root / "20261017T045030Z" / "spend.jsonl").write_text("{}\n")
	damaged = backup_status(root, max_age_hours=6, now=NOW)
	assert damaged["status"] == "stale"
	assert damaged["failed_checksums"] == ["20261017T045030Z"]
	assert damaged["verified_backups"] == 2


def test_cli_backup_status_exits_nonzero_when_stale(tmp_path, monkeypatch, capsys):
	files = research_files(tmp_path)
	monkeypatch.setenv("PML_BACKUP_DIR", str(tmp_path / "offsite"))
	(tmp_path / "offsite").mkdir()
	timestamped_backup(tmp_path / "offsite", files, now=datetime.now(UTC) - timedelta(hours=3))
	assert cli.run(cli.parser().parse_args(["backup-status"]))["status"] == "ok"
	with pytest.raises(SystemExit) as exit_:
		cli.run(cli.parser().parse_args(["backup-status", "--max-age-hours", "2"]))
	assert exit_.value.code == 1
	assert "WARNING" in capsys.readouterr().err


def test_cli_backup_files_timestamped_and_explicit_forms(tmp_path, monkeypatch):
	files = [str(p) for p in research_files(tmp_path)]
	monkeypatch.delenv("PML_BACKUP_DIR", raising=False)
	with pytest.raises(ValueError, match="PML_BACKUP_DIR"):
		cli.run(cli.parser().parse_args(["backup-files", "--timestamped", *files]))
	monkeypatch.setenv("PML_BACKUP_DIR", str(tmp_path))
	result = cli.run(cli.parser().parse_args(["backup-files", "--timestamped", *files]))
	assert (tmp_path / result["backup"].rsplit("/", 1)[1] / "verified.json").exists()
	explicit = cli.run(cli.parser().parse_args(["backup-files", str(tmp_path / "copy"), *files]))
	assert set(explicit["files"]) == {"forecasts.jsonl", "spend.jsonl"}
	with pytest.raises(ValueError, match="DESTINATION"):
		cli.run(cli.parser().parse_args(["backup-files", files[0]]))


def watch_args(tmp_path, *extra):
	return cli.parser().parse_args(
		[
			"forecast-watch",
			"--watchlist",
			str(tmp_path / "watchlist.json"),
			"--evidence",
			str(tmp_path / "evidence" / "evidence.json"),
			"--out",
			str(tmp_path / "forecasts.jsonl"),
			"--spend-ledger",
			str(tmp_path / "evidence" / "spend.jsonl"),
			"--until",
			"2099-01-01T00:00:00Z",
			"--models",
			"",
			"--aware-models",
			"",
			*extra,
		]
	)


def test_forecast_watch_refuses_to_start_without_a_backup_dir(tmp_path, monkeypatch):
	monkeypatch.delenv("PML_BACKUP_DIR", raising=False)
	# The watchlist does not exist: the backup check must come first.
	with pytest.raises(ValueError, match="PML_BACKUP_DIR"):
		asyncio.run(cli.forecast_watch(watch_args(tmp_path, "--backup-after-slot")))


class Stop(Exception):
	pass


def test_forecast_watch_backs_up_after_each_slot(tmp_path, monkeypatch, capsys):
	root = tmp_path / "offsite"
	root.mkdir()
	monkeypatch.setenv("PML_BACKUP_DIR", str(root))
	(tmp_path / "watchlist.json").write_text('{"contracts": []}')
	(tmp_path / "evidence").mkdir()
	(tmp_path / "evidence" / "evidence.json").write_text("[]")
	(tmp_path / "evidence" / "state.json").write_text("{}")
	kickoff = datetime.now(UTC) + timedelta(minutes=40)
	fixture = forecast.Fixture("f", "Everton FC", "Chelsea FC", kickoff, {}, 0.05)

	class Adapter:
		async def close(self):
			pass

	async def fake_forecast(fixture, slot, *args, **kwargs):
		return {"fixture_id": fixture.fixture_id, "slot": slot, "ensemble": {}}

	async def stop(seconds):
		raise Stop

	monkeypatch.setattr(forecast, "fixtures_from_watchlist", lambda contracts: [fixture])
	monkeypatch.setattr(forecast, "load_matches", lambda *a, **k: pd.DataFrame())
	monkeypatch.setattr(forecast, "forecast_once", fake_forecast)
	monkeypatch.setattr(cli, "PolymarketAdapter", Adapter)
	monkeypatch.setattr(cli.asyncio, "sleep", stop)
	with pytest.raises(Stop):
		asyncio.run(cli.forecast_watch(watch_args(tmp_path, "--backup-after-slot")))
	# t-6h and t-1h are both due 40 minutes before kickoff: one backup after the batch.
	(folder,) = [p for p in root.iterdir() if p.is_dir()]
	assert sorted(p.name for p in folder.iterdir()) == [
		"backup.json",
		"evidence.json",
		"forecasts.jsonl",
		"state.json",
		"verified.json",
	]
	assert len((folder / "forecasts.jsonl").read_text().splitlines()) == 2
	assert '"backup"' in capsys.readouterr().out
