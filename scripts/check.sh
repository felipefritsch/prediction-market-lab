#!/usr/bin/env sh
set -eu
uv sync --locked --no-editable
uv run --locked --no-editable ruff check src tests scripts notebooks
uv run --locked --no-editable ruff format --check src tests scripts notebooks
uv run --locked --no-editable mypy src/prediction_market_lab
uv run --locked --no-editable pytest -q
