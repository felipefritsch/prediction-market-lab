"""Validated, credential-free run configuration."""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaptureConfig(BaseModel):
	model_config = ConfigDict(extra="forbid")
	max_assets: int = Field(80, ge=1, le=80)
	max_fixtures: int = Field(10, ge=0, le=10)
	max_comparators: int = Field(6, ge=0, le=6)
	queue_size: int = Field(4096, ge=1)
	segment_records: int = Field(1000, ge=1)
	flush_seconds: float = Field(5, gt=0)
	poll_seconds: float = Field(60, ge=1)
	max_raw_gb: float = Field(20, gt=0)
	max_clock_skew_seconds: float = Field(5, gt=0)


class Settings(BaseModel):
	model_config = ConfigDict(extra="forbid")
	capture: CaptureConfig = Field(default_factory=CaptureConfig)
	paper: dict[str, str | int] = Field(default_factory=dict)
	research: dict[str, Any] = Field(default_factory=dict)
	budget: dict[str, float] = Field(default_factory=dict)

	@classmethod
	def load(cls, path: Path | None = None) -> "Settings":
		if path is None:
			return cls()
		with path.open("rb") as handle:
			return cls.model_validate(tomllib.load(handle))
