"""Optional RSS/Atom discovery, isolated from market collection and model use."""

import hashlib
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from prediction_market_lab.models import Evidence, timestamp, utc_now


def parse_feed(
	content: bytes, *, source_url: str, fixture_id: str, observed_at: datetime
) -> list[Evidence]:
	if (
		len(content) > 2_000_000
		or b"<!DOCTYPE" in content.upper()
		or b"<!ENTITY" in content.upper()
	):
		raise ValueError("Feed exceeds size limit or contains unsupported XML entities")
	root = ElementTree.fromstring(content)
	namespace = "{http://www.w3.org/2005/Atom}"
	items = list(root.iter("item")) + list(root.iter(namespace + "entry"))
	result = []
	for item in items[:100]:
		title = (
			item.findtext("title") or item.findtext(namespace + "title") or "Untitled source item"
		)
		link = item.findtext("link")
		if not link:
			for candidate in item.findall(namespace + "link"):
				if candidate.get("rel", "alternate") == "alternate":
					link = candidate.get("href")
					break
		if not link or urlparse(link).scheme not in {"https", "http"}:
			continue
		publication = item.findtext("pubDate") or item.findtext(namespace + "published")
		published = None
		if publication:
			try:
				published = (
					timestamp(parsedate_to_datetime(publication))
					if item.findtext("pubDate")
					else timestamp(publication)
				)
			except (ValueError, TypeError):
				published = None
		updated = item.findtext(namespace + "updated")
		result.append(
			Evidence(
				evidence_id=hashlib.sha256(link.encode()).hexdigest()[:20],
				fixture_id=fixture_id,
				title=title[:500],
				url=link,
				published_at=published,
				updated_at=timestamp(updated) if updated else None,
				first_seen=observed_at,
				source=source_url,
				excerpt="",
				allow_model_input=False,
				allow_redistribution=False,
			)
		)
	return result


async def fetch_feed(url: str, fixture_id: str) -> list[dict[str, Any]]:
	if urlparse(url).scheme != "https":
		raise ValueError("Configure an HTTPS feed URL")
	async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
		async with client.stream("GET", url) as response:
			response.raise_for_status()
			content = bytearray()
			async for block in response.aiter_bytes():
				content.extend(block)
				if len(content) > 2_000_000:
					raise ValueError("Feed exceeds 2 MB cap")
	return [
		item.model_dump(mode="json")
		for item in parse_feed(
			bytes(content), source_url=url, fixture_id=fixture_id, observed_at=utc_now()
		)
	]
