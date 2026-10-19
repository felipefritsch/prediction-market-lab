"""Durable raw capture, immutable Parquet runs and a single-writer catalog."""

import fcntl
import gzip
import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from prediction_market_lab.models import Contract, RawRecord, utc_now


def canonical(value: Any) -> bytes:
	return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def atomic_write(path: Path, content: bytes) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
	with temporary.open("xb") as handle:
		handle.write(content)
		handle.flush()
		os.fsync(handle.fileno())
	os.replace(temporary, path)
	fd = os.open(path.parent, os.O_RDONLY)
	try:
		os.fsync(fd)
	finally:
		os.close(fd)


class RawArchive:
	"""Journal each observation before publishing compressed immutable segments.

	The journal is fsynced before append returns. Only committed manifests are
	visible to readers. An interrupted last journal line is recoverable; malformed
	complete lines fail loudly. One writer per archive is enforced by an OS lock.
	"""

	def __init__(self, root: Path, segment_records: int = 1000) -> None:
		self.root = root
		self.raw = root / "raw"
		self.raw.mkdir(parents=True, exist_ok=True)
		self.lock = (root / ".writer.lock").open("a")
		try:
			fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except BlockingIOError:
			self.lock.close()
			raise RuntimeError("Another collector owns this archive") from None
		self.segment_records = segment_records
		self.pending = self.raw / "journal.pending"
		self.rows: list[RawRecord] = []
		self.ordinal = max(
			(json.loads(p.read_text()).get("ordinal", 0) for p in self.raw.glob("*.manifest.json")),
			default=0,
		)
		if self.pending.exists():
			data = self.pending.read_bytes()
			for line in data.splitlines(keepends=True):
				if line.endswith(b"\n"):
					self.rows.append(RawRecord.model_validate_json(line))
			self.flush()
			atomic_write(self.pending, b"")
		self.handle = self.pending.open("ab")

	def append(self, record: RawRecord) -> None:
		self.handle.write(record.model_dump_json().encode() + b"\n")
		self.handle.flush()
		os.fsync(self.handle.fileno())
		self.rows.append(record)
		if len(self.rows) >= self.segment_records:
			self.flush()

	def flush(self) -> None:
		if not self.rows:
			return
		content = b"".join(row.model_dump_json().encode() + b"\n" for row in self.rows)
		digest = hashlib.sha256(content).hexdigest()
		self.ordinal += 1
		name = f"{self.ordinal:012d}-" + digest[:16]
		compressed = gzip.compress(content, mtime=0)
		path = self.raw / (name + ".jsonl.gz")
		manifest = {
			"schema_version": 1,
			"ordinal": self.ordinal,
			"file": path.name,
			"rows": len(self.rows),
			"sha256": hashlib.sha256(compressed).hexdigest(),
			"content_sha256": digest,
			"first_received": self.rows[0].received_at.isoformat(),
			"last_received": self.rows[-1].received_at.isoformat(),
			"provenance": sorted({r.provenance.value for r in self.rows}),
		}
		atomic_write(path, compressed)
		atomic_write(self.raw / (name + ".manifest.json"), canonical(manifest))
		self.rows.clear()
		if hasattr(self, "handle"):
			self.handle.close()
		atomic_write(self.pending, b"")
		if hasattr(self, "handle"):
			self.handle = self.pending.open("ab")

	def close(self) -> None:
		self.flush()
		self.handle.close()
		fcntl.flock(self.lock, fcntl.LOCK_UN)
		self.lock.close()

	def __enter__(self) -> "RawArchive":
		return self

	def __exit__(self, *args: Any) -> None:
		self.close()


def read_archive(root: Path) -> Iterator[RawRecord]:
	seen_segments: set[str] = set()
	for path in sorted((root / "raw").glob("*.manifest.json")):
		manifest = json.loads(path.read_text())
		filename = manifest["file"]
		if Path(filename).name != filename:
			raise ValueError("Invalid segment filename")
		content = (path.parent / filename).read_bytes()
		if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
			raise ValueError(f"Checksum mismatch: {filename}")
		lines = gzip.decompress(content).splitlines()
		if len(lines) != manifest["rows"]:
			raise ValueError("Segment row count mismatch")
		content_digest = hashlib.sha256(gzip.decompress(content)).hexdigest()
		if content_digest != manifest["content_sha256"]:
			raise ValueError("Uncompressed content checksum mismatch")
		if content_digest in seen_segments:
			continue
		seen_segments.add(content_digest)
		for line in lines:
			record = RawRecord.model_validate_json(line)
			yield record


class Catalog:
	def __init__(self, root: Path) -> None:
		root.mkdir(parents=True, exist_ok=True)
		self.connection = sqlite3.connect(root / "catalog.sqlite")
		self.connection.execute("PRAGMA journal_mode=WAL")
		self.connection.execute("PRAGMA synchronous=FULL")
		self.connection.executescript("""
		CREATE TABLE IF NOT EXISTS contracts (
			asset_id TEXT NOT NULL, known_at TEXT NOT NULL, version TEXT NOT NULL,
			document TEXT NOT NULL, PRIMARY KEY(asset_id, version));
		CREATE TABLE IF NOT EXISTS checkpoints (source TEXT PRIMARY KEY, cursor TEXT, committed_at TEXT);
		CREATE TABLE IF NOT EXISTS selections (recorded_at TEXT, document TEXT);
		CREATE TABLE IF NOT EXISTS outbox (incident_id TEXT PRIMARY KEY, document TEXT, status TEXT);
		""")

	def store_contract(self, contract: Contract) -> None:
		with self.connection:
			self.connection.execute(
				"INSERT OR IGNORE INTO contracts VALUES (?, ?, ?, ?)",
				(
					contract.asset_id,
					contract.known_at.isoformat(),
					contract.version,
					contract.model_dump_json(),
				),
			)

	def contracts_asof(self, cutoff: datetime) -> list[Contract]:
		rows = self.connection.execute(
			"""
		SELECT document FROM (
			SELECT document, ROW_NUMBER() OVER (PARTITION BY asset_id ORDER BY known_at DESC, rowid DESC) n
			FROM contracts WHERE known_at <= ?) WHERE n=1
		""",
			(cutoff.isoformat(),),
		).fetchall()
		return [Contract.model_validate_json(row[0]) for row in rows]

	def checkpoint(self, source: str, cursor: str) -> None:
		with self.connection:
			self.connection.execute(
				"INSERT OR REPLACE INTO checkpoints VALUES (?, ?, ?)",
				(source, cursor, utc_now().isoformat()),
			)

	def save_selection(self, document: dict[str, Any]) -> None:
		with self.connection:
			self.connection.execute(
				"INSERT INTO selections VALUES (?, ?)",
				(utc_now().isoformat(), canonical(document).decode()),
			)

	def enqueue(self, incident_id: str, document: dict[str, Any]) -> None:
		with self.connection:
			self.connection.execute(
				"INSERT OR IGNORE INTO outbox VALUES (?, ?, 'pending')",
				(incident_id, canonical(document).decode()),
			)

	def close(self) -> None:
		self.connection.close()


def write_table(root: Path, name: str, rows: list[dict[str, Any]]) -> Path:
	"""Publish immutable per-run tables; empty datasets have an explicit empty schema."""
	path = root / "tables" / name / "part-00000.parquet"
	path.parent.mkdir(parents=True, exist_ok=True)
	if path.exists():
		raise FileExistsError(f"Run table already committed: {path}")
	keys = sorted({key for row in rows for key in row})
	complete_rows = [{key: row.get(key) for key in keys} for row in rows]
	table = (
		pa.Table.from_pylist(complete_rows)
		if rows
		else pa.table({"_empty": pa.array([], pa.bool_())})
	)
	# 64-bit string offsets: a multi-day capture's raw payload column passes 2 GB, which plain
	# strings cannot address when `take` combines its chunks. Parquet stores both the same way.
	table = table.cast(
		pa.schema(
			pa.field(field.name, pa.large_string()) if pa.types.is_string(field.type) else field
			for field in table.schema
		)
	)
	if rows and "received_at" in keys and all(row.get("received_at") is not None for row in rows):
		partitions: dict[str, list[int]] = {}
		for index, row in enumerate(rows):
			day = str(row["received_at"])[:10]
			partitions.setdefault(day, []).append(index)
		for day, indices in sorted(partitions.items()):
			target = path.parent / f"receipt_date={day}" / path.name
			if target.exists():
				raise FileExistsError(f"Run partition already committed: {target}")
			target.parent.mkdir(parents=True, exist_ok=True)
			temporary = target.with_suffix(".tmp")
			pq.write_table(table.take(pa.array(indices)), temporary, compression="zstd")
			os.replace(temporary, target)
		return path.parent
	temporary = path.with_suffix(".tmp")
	pq.write_table(table, temporary, compression="zstd")
	os.replace(temporary, path)
	return path


def verify_run(root: Path) -> dict[str, Any]:
	manifest = json.loads((root / "manifest.json").read_text())
	for name, expected in manifest["files"].items():
		path = (root / name).resolve()
		if not path.is_relative_to(root.resolve()):
			raise ValueError("Manifest path escapes run")
		if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
			raise ValueError(f"Run checksum mismatch: {name}")
	return manifest
