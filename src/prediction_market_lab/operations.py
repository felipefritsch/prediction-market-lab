"""Verified portable backup and explicit storage-budget forecasts."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prediction_market_lab.models import utc_now
from prediction_market_lab.storage import atomic_write, canonical, read_archive

BACKUP_DIR_ENV = "PML_BACKUP_DIR"
VERIFIED = "verified.json"


def backup(source: Path, destination: Path) -> dict[str, Any]:
	if destination.exists() or destination.resolve().is_relative_to(source.resolve()):
		raise ValueError("Backup needs a new directory outside the capture")
	destination.mkdir(parents=True)
	files: dict[str, str] = {}
	# Only committed immutable segments; never copy a live journal or SQLite WAL.
	manifests = sorted((source / "raw").glob("*.manifest.json"))
	for manifest_path in manifests:
		doc = json.loads(manifest_path.read_text())
		segment = source / "raw" / doc["file"]
		if segment.parent != source / "raw" or Path(doc["file"]).name != doc["file"]:
			raise ValueError("Invalid archive path")
		if hashlib.sha256(segment.read_bytes()).hexdigest() != doc["sha256"]:
			raise ValueError("Cannot back up corrupt segment")
		for path in (segment, manifest_path):
			name = str(path.relative_to(source))
			target = destination / name
			target.parent.mkdir(parents=True, exist_ok=True)
			shutil.copyfile(path, target)
			files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
	if (source / "catalog.sqlite").exists():
		with (
			sqlite3.connect(f"file:{source / 'catalog.sqlite'}?mode=ro", uri=True) as original,
			sqlite3.connect(destination / "catalog.sqlite") as copy,
		):
			original.backup(copy)
		files["catalog.sqlite"] = hashlib.sha256(
			(destination / "catalog.sqlite").read_bytes()
		).hexdigest()
	for name in (
		"contracts.json",
		"evidence.json",
		"selection.json",
		"health.json",
		"synthetic.json",
	):
		if (source / name).exists():
			shutil.copyfile(source / name, destination / name)
			files[name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
	for path in sorted((source / "selections").glob("*.json")):
		name = str(path.relative_to(source))
		(destination / name).parent.mkdir(parents=True, exist_ok=True)
		shutil.copyfile(path, destination / name)
		files[name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
	manifest = {
		"files": files,
		"segments": len(manifests),
		"scope": "committed segments; SQLite online backup; pending journal excluded; raw metadata is replay authority",
	}
	atomic_write(destination / "backup.json", canonical(manifest))
	return manifest


def backup_files(destination: Path, sources: list[Path]) -> dict[str, Any]:
	"""Checksummed copy of append-only research files that cannot be re-collected.

	Forecasts, evidence and the spend ledger live outside the capture, so `backup` misses them.
	The result restores and verifies with `restore`.
	"""
	names = [path.name for path in sources]
	if len(set(names)) != len(names):
		raise ValueError("Backed-up files need distinct names")
	if {"backup.json", VERIFIED} & set(names):
		raise ValueError("backup.json and verified.json are reserved for the backup's own records")
	if destination.exists():
		raise ValueError("Backup needs a new directory")
	missing = [str(path) for path in sources if not path.is_file()]
	if missing:
		raise FileNotFoundError(f"Not a file: {', '.join(missing)}")
	destination.mkdir(parents=True)
	files: dict[str, str] = {}
	for path in sources:
		# One read, so the copy and its checksum agree while a watcher keeps appending.
		content = path.read_bytes()
		atomic_write(destination / path.name, content)
		files[path.name] = hashlib.sha256(content).hexdigest()
	manifest = {"files": files, "scope": "point-in-time copies of append-only research files"}
	atomic_write(destination / "backup.json", canonical(manifest))
	return manifest


def backup_root(environ: Mapping[str, str] = os.environ) -> Path:
	"""The directory for timestamped research backups; there is deliberately no local default."""
	value = environ.get(BACKUP_DIR_ENV, "")
	if not value:
		raise ValueError(
			f"Set {BACKUP_DIR_ENV} to a directory on another machine or synced storage"
		)
	root = Path(value).expanduser()
	if not root.is_absolute():
		raise ValueError(f"{BACKUP_DIR_ENV} must be an absolute path, not {value}")
	return root


def timestamped_backup(root: Path, sources: list[Path], *, now: datetime) -> dict[str, Any]:
	"""`backup_files` into root/<UTC time>, verified by a restore before it is marked verified."""
	# Never create the root: an unmounted volume would otherwise become a local directory.
	if not root.is_dir():
		raise FileNotFoundError(f"Backup directory not found: {root}")
	destination = root / now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
	manifest = backup_files(destination, sources)
	with tempfile.TemporaryDirectory(prefix="pml-backup-verify-") as directory:
		restore(destination, Path(directory) / "restored")
	verified = {
		"created_at": now.astimezone(UTC).isoformat(),
		"verified_at": utc_now().isoformat(),
		"files": manifest["files"],
	}
	atomic_write(destination / VERIFIED, canonical(verified))
	return {"backup": str(destination), **verified}


def backup_after_slot(root: Path, sources: list[Path], *, now: datetime) -> str:
	"""One log line; a failed backup is reported, never raised, so forecasting carries on."""
	try:
		result = timestamped_backup(root, [p for p in sources if p.is_file()], now=now)
	except Exception as exc:
		return f"BACKUP FAILED: {type(exc).__name__}: {exc}"
	return json.dumps({"backup": result["backup"], "files": sorted(result["files"])})


def backup_status(root: Path, *, max_age_hours: float, now: datetime) -> dict[str, Any]:
	"""Age of the newest backup that was verified when made and still matches its checksums."""
	if not root.is_dir():
		raise FileNotFoundError(f"Backup directory not found: {root}")
	marked = []
	for path in root.glob(f"*/{VERIFIED}"):
		doc = json.loads(path.read_text())
		marked.append((datetime.fromisoformat(doc["created_at"]), path.parent, doc["files"]))
	corrupt = []
	newest = None
	for created, folder, files in sorted(marked, key=lambda m: m[0], reverse=True):
		if all(
			(folder / name).is_file()
			and hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
			for name, digest in files.items()
		):
			newest = (created, folder)
			break
		corrupt.append(folder.name)
	result: dict[str, Any] = {
		"root": str(root),
		"max_age_hours": max_age_hours,
		"verified_backups": len(marked),
		"failed_checksums": corrupt,
	}
	if newest is None:
		return {**result, "status": "none"}
	age = (now - newest[0]).total_seconds() / 3600
	return {
		**result,
		"status": "ok" if age <= max_age_hours else "stale",
		"newest": str(newest[1]),
		"created_at": newest[0].isoformat(),
		"age_hours": round(age, 2),
	}


def restore(source: Path, destination: Path) -> dict[str, Any]:
	if destination.exists():
		raise FileExistsError("Restore destination must be new")
	manifest = json.loads((source / "backup.json").read_text())
	for name, digest in manifest["files"].items():
		path = (source / name).resolve()
		if not path.is_relative_to(source.resolve()) or path.is_symlink():
			raise ValueError("Unsafe backup member")
		if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
			raise ValueError(f"Backup checksum failed: {name}")
	destination.mkdir(parents=True)
	for name in manifest["files"]:
		target = destination / name
		target.parent.mkdir(parents=True, exist_ok=True)
		shutil.copyfile(source / name, target)
	count = sum(1 for _ in read_archive(destination))
	if (destination / "catalog.sqlite").exists():
		with sqlite3.connect(destination / "catalog.sqlite") as connection:
			if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
				raise ValueError("Restored catalog failed integrity check")
	return {"restored_records": count, "files": len(manifest["files"])}


def restore_check(source: Path) -> dict[str, Any]:
	with tempfile.TemporaryDirectory(prefix="pml-backup-check-") as directory:
		root = Path(directory)
		backup(source, root / "backup")
		return restore(root / "backup", root / "restored")


def budget_forecast(
	raw_bytes: int,
	elapsed_seconds: float,
	*,
	server_usd: float,
	storage_gb_usd: float,
	backup_copies: int = 1,
	cap_usd: float = 30,
) -> dict[str, Any]:
	if elapsed_seconds <= 0 or min(server_usd, storage_gb_usd, raw_bytes, backup_copies) < 0:
		raise ValueError("Provide positive measured duration and nonnegative cost inputs")
	gb = raw_bytes / elapsed_seconds * 30 * 86400 / 1e9
	cost = server_usd + gb * (1 + backup_copies) * storage_gb_usd
	return {
		"measured_raw_bytes": raw_bytes,
		"elapsed_seconds": elapsed_seconds,
		"projected_30d_raw_gb": gb,
		"estimated_30d_usd": cost,
		"cap_usd": cap_usd,
		"within_cap": cost <= cap_usd,
		"assumptions": "User-supplied prices, constant observed rate, excludes derived tables/egress/taxes. Re-measure on matchday.",
	}
