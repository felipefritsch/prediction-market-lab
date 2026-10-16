"""Self-contained research reports with numerical outputs beside limitations."""

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px

from prediction_market_lab.incidents import Summarizer, build_incidents, read_evidence
from prediction_market_lab.research import execution_study, liquidity_study, load_table, shock_study
from prediction_market_lab.storage import atomic_write, canonical

STYLE = """body{font:16px/1.6 system-ui;margin:40px auto;max-width:1120px;padding:0 24px;color:#182431;background:#fafbfc}h1,h2{line-height:1.2}h1{font-size:36px}h2{margin-top:44px}table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}th,td{padding:8px 12px;border-bottom:1px solid #ccd3da;text-align:left}.badge{background:#e8eef8;padding:12px;border-left:4px solid #345f94}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf1f5;padding:16px;font-size:13px}a{color:#295a91}.muted{color:#526171}li{margin:8px 0}"""


def table(rows: list[dict[str, Any]]) -> str:
	if not rows:
		return "<p>No eligible observations.</p>"
	return pd.DataFrame(rows).to_html(
		index=False, escape=True, float_format=lambda x: f"{x:.4f}", border=0
	)


def report(
	run: Path,
	output: Path,
	*,
	paper_runs: dict[str, Path] | None = None,
	evidence_path: Path | None = None,
	research_config: dict[str, Any] | None = None,
	summarizer: Summarizer | None = None,
) -> dict[str, Any]:
	if output.exists():
		raise FileExistsError("Choose a new report directory")
	output.mkdir(parents=True)
	features, events, contracts = (
		load_table(run, "features"),
		load_table(run, "events"),
		load_table(run, "contracts"),
	)
	studies = {
		"liquidity": liquidity_study(features, research_config),
		"shocks": shock_study(features, events),
		"execution": execution_study(paper_runs or {}),
	}
	incidents = build_incidents(
		features, contracts, read_evidence(evidence_path), summarizer=summarizer
	)
	atomic_write(output / "studies.json", canonical(studies))
	atomic_write(output / "incidents.json", canonical(incidents))
	provenance = (
		", ".join(sorted(features.input_provenance.unique()))
		if not features.empty
		else "no features"
	)
	parts = [
		f"<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Prediction Market Lab — research</title><style>{STYLE}</style><body>",
		"<h1>Prediction Market Lab</h1><p>How does information enter prices, and what survives execution costs?</p>",
		f"<p class='badge'><strong>Input provenance: {html.escape(provenance)}.</strong> Derived research and simulated paper outcomes are labeled separately. Synthetic results validate the pipeline; they establish no market edge.</p>",
		"<p><a href='#liquidity'>Liquidity</a> · <a href='#shocks'>Information shocks</a> · <a href='#execution'>Execution</a> · <a href='#incidents'>Incidents</a></p>",
	]
	if not features.empty and "midpoint" in features:
		first = str(features.fixture_id.iloc[0])
		plot = features[
			(features.fixture_id == first)
			& (features.outcome.str.lower() == "yes")
			& (features.quality == "usable")
		]
		fig = px.line(
			plot,
			x="received_at",
			y="midpoint",
			color="family_outcome",
			labels={
				"received_at": "Local receipt time (UTC)",
				"midpoint": "Probability",
				"family_outcome": "Contract",
			},
			title=f"Recorded midpoint observations · {first}",
			color_discrete_sequence=["#315c8c", "#b9812b", "#78619e"],
		)
		fig.update_layout(template="plotly_white", font_family="Arial", height=380)
		parts.append(fig.to_html(full_html=False, include_plotlyjs=True, div_id="fixture-prices"))
	for name, title in (
		("liquidity", "Liquidity and price discovery"),
		("shocks", "Information and liquidity shocks"),
		("execution", "Execution and liquidity provision"),
	):
		study = studies[name]
		parts.append(f"<h2 id='{name}'>{title}</h2><p>Status: {html.escape(study['status'])}</p>")
		if name == "liquidity":
			parts.extend(
				[
					"<h3>Coverage</h3>",
					table(study.get("coverage", [])),
					"<h3>Held-out forecasts</h3>",
					table(study.get("forecast_metrics", [])),
					"<h3>Next-move classification</h3>",
					table(study.get("classification", {}).get("metrics", [])),
					"<h3>Chronological split</h3>",
					"<pre>" + html.escape(json.dumps(study.get("split", {}), indent=2)) + "</pre>",
				]
			)
		elif name == "shocks":
			parts.append(table(study.get("episodes", [])))
		else:
			rows = [
				{
					k: row[k]
					for k in (
						"scenario",
						"latency_ms",
						"queue_multiplier",
						"net_pnl",
						"fees",
						"fills",
						"exposure",
						"mean_60s_markout_pp",
					)
				}
				for row in study.get("scenarios", [])
			]
			parts.append(table(rows))
		parts.append(
			"<ul>"
			+ "".join("<li>" + html.escape(text) + "</li>" for text in study.get("limitations", []))
			+ "</ul>"
		)
		memo = f"# {title}\n\nInput provenance: **{provenance}**. Results are {study.get('evidence_class', 'unavailable')}.\n\n"
		questions = {
			"liquidity": "Does imbalance/OFI improve forecasts over simple price baselines?",
			"shocks": "How do spread, depth and prices change around scheduled kickoff and receipt of information?",
			"execution": "Which simulated outcomes survive liquidity, fees, latency and queue assumptions?",
		}
		memo += f"## Hypothesis and implementation\n\n{questions[name]} Calculations use the shared research/replay engine. The full protocol is in docs/research-protocol.md. This memo establishes no trading edge.\n\n"
		if name == "liquidity":
			digest = {
				key: study.get(key)
				for key in (
					"status",
					"label_availability",
					"split",
					"forecast_metrics",
					"classification",
				)
			}
		elif name == "shocks":
			digest = {
				"status": study["status"],
				"eligible_episodes": len(study.get("episodes", [])),
				"event_uncertainty": study.get("event_uncertainty", {}),
			}
		else:
			digest = {
				"status": study["status"],
				"scenarios": [
					{
						k: row[k]
						for k in ("scenario", "fills", "fees", "net_pnl", "mean_60s_markout_pp")
					}
					for row in study.get("scenarios", [])
				],
			}
		memo += (
			"## Findings and validation\n\nThese values are generated from the input run, not hand-entered. Missing estimates and small-event uncertainty remain explicit. Synthetic results validate software only. Full coverage, exclusions and numerical outputs are in studies.json and index.html.\n\n```json\n"
			+ json.dumps(digest, indent=2, default=str)
			+ "\n```\n\n## Limitations\n\n"
			+ "\n".join("- " + x for x in study.get("limitations", []))
			+ "\n"
		)
		atomic_write(output / f"{name}-memo.md", memo.encode())
	parts.append(
		"<h2 id='incidents'>Incident dossiers</h2><p>Price-triggered cases are descriptive. Later observations never enter the original explanation packet.</p>"
	)
	for packet in incidents[:12]:
		parts.append(
			f"<details><summary>{html.escape(packet['fixture_id'])} · {html.escape(packet['asof'])} · {packet['facts']['change_60s_pp']:+.2f} pp</summary><pre>{html.escape(json.dumps(packet, indent=2, default=str))}</pre></details>"
		)
	parts.append(
		"<p class='muted'>Source lineage, exclusions, all incident packets and machine-readable outputs accompany this report. Live capture qualification requires the documented pilot and prospective recording milestones.</p></body></html>"
	)
	atomic_write(output / "index.html", "\n".join(parts).encode())
	return {"studies": studies, "incidents": len(incidents), "report": str(output / "index.html")}
