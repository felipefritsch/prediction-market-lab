"""Verified portable backup and explicit storage-budget forecasts."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from prediction_market_lab.storage import atomic_write, canonical, read_archive


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
