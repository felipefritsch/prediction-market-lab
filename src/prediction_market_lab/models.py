"""Small domain contracts; venue SDK types never cross this boundary."""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
	return datetime.now(UTC)


def timestamp(value: Any) -> datetime | None:
	if value is None or value == "":
		return None
	if isinstance(value, datetime):
		result = value
	elif isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
		number = float(value)
		result = datetime.fromtimestamp(number / 1000 if number > 1e11 else number, UTC)
	else:
		result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	if result.tzinfo is None:
		raise ValueError("A timestamp must include its timezone")
	return result.astimezone(UTC)


class EvidenceClass(StrEnum):
	OBSERVED = "observed"
	SYNTHETIC = "synthetic"
	DERIVED = "derived"
	SIMULATED = "simulated"


class Quality(StrEnum):
	USABLE = "usable"
	RECOVERING = "recovering"
	DEGRADED = "degraded"


class Contract(BaseModel):
	model_config = ConfigDict(extra="forbid")
	asset_id: str
	condition_id: str
	event_id: str
	fixture_id: str
	market_id: str
	label: str
	outcome: str
	category: str
	competition: str = ""
	sports_fixture_id: str = ""
	protocol: str = "unknown"
	venue: str = "polymarket"
	known_at: datetime
	kickoff: datetime | None = None
	rules: str
	rules_url: str = ""
	rules_reviewed: bool = False
	family_outcome: str = ""
	regulation_time: bool = False
	negative_risk: bool | None = None
	tick_size: Decimal | None = Field(None, gt=0, le=1)
	min_order_size: Decimal | None = Field(None, gt=0)
	fee_rate: Decimal | None = Field(None, ge=0, le=1)
	seconds_delay: float | None = Field(None, ge=0)
	accepting_orders: bool | None = None
	clear_book_on_start: bool | None = None
	volume_24h: Decimal = Field(Decimal(0), ge=0)
	provenance: EvidenceClass = EvidenceClass.OBSERVED

	@field_validator("known_at", "kickoff")
	@classmethod
	def dates(cls, value: datetime | None) -> datetime | None:
		return timestamp(value)

	@property
	def version(self) -> str:
		return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:20]


class RawRecord(BaseModel):
	model_config = ConfigDict(extra="forbid")
	schema_version: int = 1
	record_id: str
	source: str
	session_id: str
	sequence: int = Field(ge=0)
	received_at: datetime
	monotonic_ns: int = Field(ge=0)
	payload: dict[str, Any] | list[Any] | str
	provenance: EvidenceClass = EvidenceClass.OBSERVED
	adapter_version: str = "pml-1"

	@field_validator("received_at")
	@classmethod
	def dates(cls, value: datetime) -> datetime:
		parsed = timestamp(value)
		assert parsed is not None
		return parsed


class Event(BaseModel):
	"""Normalized observation, with knowledge time separate from source event time."""

	record_id: str
	kind: str
	asset_id: str = ""
	fixture_id: str = ""
	source: str
	session_id: str
	received_at: datetime
	source_at: datetime | None = None
	sequence: int
	data: dict[str, Any]
	provenance: EvidenceClass


class Evidence(BaseModel):
	model_config = ConfigDict(extra="forbid")
	evidence_id: str
	fixture_id: str
	title: str
	url: str
	first_seen: datetime
	published_at: datetime | None = None
	updated_at: datetime | None = None
	excerpt: str = ""
	allow_model_input: bool = False
	allow_redistribution: bool = False
	source: str = "manual"
	provenance: EvidenceClass = EvidenceClass.OBSERVED

	@field_validator("url")
	@classmethod
	def public_link(cls, value: str) -> str:
		from urllib.parse import urlparse

		parsed = urlparse(value)
		if (
			parsed.scheme not in {"http", "https"}
			or not parsed.hostname
			or parsed.username
			or parsed.password
		):
			raise ValueError("Evidence must use an HTTP(S) source URL without credentials")
		return value

	@field_validator("first_seen", "published_at", "updated_at")
	@classmethod
	def dates(cls, value: datetime | None) -> datetime | None:
		return timestamp(value)
