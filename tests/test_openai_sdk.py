"""Offline checks for the optional OpenAI summarizer dependency."""

import importlib.metadata

from openai import OpenAI


def test_openai_sdk_is_installed() -> None:
	"""The locked project environment contains the official Python SDK."""
	assert importlib.metadata.version("openai")


def test_openai_client_reads_api_key_from_environment(monkeypatch) -> None:
	"""Client configuration uses the standard environment variable without a network call."""
	placeholder = "sk-test-placeholder-not-a-real-key"
	monkeypatch.setenv("OPENAI_API_KEY", placeholder)

	client = OpenAI()

	assert client.api_key == placeholder
