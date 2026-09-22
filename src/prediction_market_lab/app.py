"""Five focused views over immutable runs; launch with `pml dashboard`."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from prediction_market_lab.incidents import append_annotation
from prediction_market_lab.models import utc_now
from prediction_market_lab.research import coverage, load_table

COLORS = ["#315c8c", "#b9812b", "#78619e", "#548078", "#b76576"]


@st.cache_data
def committed_table(run: str, name: str, manifest_digest: str) -> pd.DataFrame:
	return load_table(Path(run), name)


def table(run: str, name: str) -> pd.DataFrame:
	manifest = Path(run) / "manifest.json"
	if not manifest.exists():
		return pd.DataFrame()
	return committed_table(run, name, hashlib.sha256(manifest.read_bytes()).hexdigest())


def chart(fig: Any) -> None:
	fig.update_layout(
		template="plotly_white", font_family="Arial", margin={"l": 20, "r": 20, "t": 45, "b": 25}
	)
	st.plotly_chart(fig, width="stretch")


def main() -> None:
	st.set_page_config(page_title="Prediction Market Lab", page_icon="◉", layout="wide")
	default = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "data/demo"
	workspace = Path(st.sidebar.text_input("Workspace", default)).expanduser()
	st.sidebar.caption("Public observations → reproducible research → simulated execution")
	view = st.sidebar.radio(
		"Workspace view",
		[
			"Universe and coverage",
			"Fixture replay",
			"Microstructure",
			"Incidents",
			"Paper portfolio and health",
		],
	)
	st.title("Prediction Market Lab")
	st.caption("Football microstructure, information and execution")
	if not (workspace / "workspace.json").exists():
		st.info(
			"Create an offline workspace with `pml demo`, or provide a workspace.json pointing to your recorded run."
		)
		return
	descriptor = json.loads((workspace / "workspace.json").read_text())
	run = (workspace / descriptor["run"]).resolve()
	research = workspace / descriptor["research"]
	capture = workspace / descriptor["capture"]
	features, contracts = table(str(run), "features"), table(str(run), "contracts")
	if features.empty:
		st.info(
			"This run has no reconstructed book observations. Inspect capture health and raw schemas."
		)
		return
	provenance = ", ".join(sorted(features.input_provenance.unique()))
	if "synthetic" in provenance:
		st.warning(
			"SYNTHETIC DEMONSTRATION · These observations validate the software. They are not market findings or evidence of a trading edge."
		)
	else:
		st.info(
			"Observed inputs · Derived analysis · Paper fills and P&L are simulated. Feed coverage and execution assumptions remain material."
		)
	studies = (
		json.loads((research / "studies.json").read_text())
		if (research / "studies.json").exists()
		else {}
	)
	if view == "Universe and coverage":
		cov = coverage(features)
		left, middle, right = st.columns(3)
		left.metric("Recorded events", int(features.fixture_id.nunique()))
		middle.metric("Outcome assets", int(features.asset_id.nunique()))
		fraction = (
			cov.usable_asset_seconds.sum() / cov.asset_seconds.sum()
			if cov.asset_seconds.sum()
			else 0
		)
		right.metric("Usable observed asset-time", f"{fraction:.1%}")
		st.caption(
			"Denominator spans each asset's first to last recorded feature. It does not measure time outside the selected recording window."
		)
		st.dataframe(cov, hide_index=True, width="stretch")
		fixture = st.selectbox("Inspect contract family", sorted(contracts.fixture_id.unique()))
		selected = (
			contracts[contracts.fixture_id == fixture]
			.sort_values("known_at")
			.drop_duplicates("asset_id", keep="last")
		)
		st.dataframe(
			selected[
				[
					"asset_id",
					"outcome",
					"family_outcome",
					"competition",
					"kickoff",
					"fee_rate",
					"seconds_delay",
					"rules_reviewed",
					"known_at",
				]
			],
			hide_index=True,
			width="stretch",
		)
		for row in selected.itertuples():
			with st.expander(f"{row.family_outcome} · {row.outcome} · rules version {row.version}"):
				st.write(row.rules)
				st.caption(
					"Latest recorded metadata shown here; replay uses versions known at each decision."
				)
	elif view == "Fixture replay":
		fixture = st.selectbox("Fixture / event", sorted(features.fixture_id.unique()))
		frame = features[features.fixture_id == fixture].sort_values("received_at")
		times = sorted(frame.received_at.unique())
		index = st.slider("Replay observation", 0, len(times) - 1, min(90, len(times) - 1))
		at = times[index]
		st.write(f"Receipt clock: **{pd.Timestamp(at).isoformat()}**")
		visible = frame[(frame.received_at <= at) & (frame.outcome.str.lower() == "yes")]
		chart(
			px.line(
				visible,
				x="received_at",
				y="midpoint",
				color="family_outcome",
				color_discrete_sequence=COLORS,
				labels={
					"received_at": "Receipt time (UTC)",
					"midpoint": "Probability",
					"family_outcome": "Result contract",
				},
				title="Prices available by the replay cursor",
			)
		)
		asset = st.selectbox("Order-book asset", sorted(frame.asset_id.unique()))
		latest = frame[(frame.asset_id == asset) & (frame.received_at <= at)].tail(1)
		st.dataframe(
			latest[
				["quality", "quality_reason", "bid", "ask", "spread_ticks", "quote_age_seconds"]
			],
			hide_index=True,
		)
		depth = table(str(run), "depth")
		depth = (
			depth[(depth.asset_id == asset) & (depth.received_at <= at)]
			if not depth.empty
			else depth
		)
		if not depth.empty and not latest.empty and latest.iloc[0].quality == "usable":
			snapshot = depth[depth.received_at == depth.received_at.max()]
			chart(
				px.bar(
					snapshot,
					x="price",
					y="quantity",
					color="side",
					barmode="group",
					color_discrete_sequence=COLORS,
					labels={"price": "Price per share", "quantity": "Displayed shares"},
					title="Depth checkpoint (sampled every ≥10 seconds)",
				)
			)
			st.caption(
				f"Checkpoint received: {snapshot.received_at.iloc[0]}. Raw replay retains every captured update."
			)
		events = table(str(run), "events")
		events = events[
			(events.received_at <= at)
			& (events.fixture_id == fixture)
			& events.kind.isin(["score", "evidence", "settlement"])
		]
		st.subheader("Context received by this time")
		st.dataframe(
			events[["received_at", "source_at", "kind", "data_json"]],
			hide_index=True,
			width="stretch",
		)
		st.caption(
			"Score receipt is not goal time. Scoreboard status does not establish exchange availability."
		)
	elif view == "Microstructure":
		study = studies.get("liquidity", {})
		phase = pd.DataFrame(study.get("liquidity_by_event_phase", []))
		if not phase.empty:
			chart(
				px.box(
					phase,
					x="phase",
					y="spread_pp",
					color="category",
					points="all",
					color_discrete_sequence=COLORS,
					labels={
						"phase": "Timing bucket",
						"spread_pp": "Mean event spread (probability points)",
					},
					title="Liquidity by event and phase",
				)
			)
		metrics = pd.DataFrame(study.get("forecast_metrics", []))
		st.subheader("Chronologically held-out forecasts")
		st.dataframe(metrics, hide_index=True, width="stretch")
		st.json(study.get("split", {}), expanded=False)
		st.subheader("Next-move direction")
		st.dataframe(
			pd.DataFrame(study.get("classification", {}).get("metrics", [])), hide_index=True
		)
		st.subheader("Information and liquidity shocks")
		st.dataframe(
			pd.DataFrame(studies.get("shocks", {}).get("episodes", [])),
			hide_index=True,
			width="stretch",
		)
		for limitation in study.get("limitations", []):
			st.caption(limitation)
	elif view == "Incidents":
		packets = (
			json.loads((research / "incidents.json").read_text())
			if (research / "incidents.json").exists()
			else []
		)
		if not packets:
			st.info("No incidents met the declared move and quality thresholds.")
			return
		packet_id = st.selectbox(
			"Incident",
			list(range(len(packets))),
			format_func=lambda i: (
				f"{packets[i]['fixture_id']} · {packets[i]['asof']} · {packets[i]['facts']['change_60s_pp']:+.2f} pp"
			),
		)
		packet = packets[packet_id]
		st.subheader("What changed")
		facts = packet["facts"]
		st.metric("60-second price change", f"{facts['change_60s_pp']:+.2f} probability points")
		st.write(
			f"Midpoint: **{facts['midpoint_before']:.2%} → {facts['midpoint_at_alert']:.2%}**. "
			f"Spread: **{facts['spread_before_pp']:.2f} → {facts['spread_at_alert_pp']:.2f} pp**. "
			f"Bid depth: **{facts['bid_depth_before']:,.0f} → {facts['bid_depth_at_alert']:,.0f} shares**. "
			f"Trade notifications in the prior minute: **{facts['trade_notifications_60s']}**."
		)
		with st.expander("Exact calculation fields"):
			st.json(facts)
		st.write("**Explanation status:** " + packet["explanation"]["status"].replace("_", " "))
		st.write(packet["rules"])
		st.subheader("Evidence available at the alert")
		if not packet["evidence"]:
			st.write("No eligible source evidence was available.")
		for item in packet["evidence"]:
			st.write(item["title"])
			st.link_button("Source", item["url"])
			st.caption(f"Published: {item['published_at']} · First observed: {item['first_seen']}")
		with st.expander("Subsequent behavior — unavailable at alert time"):
			st.json(packet["follow_up"])
		with st.form("annotation"):
			author = st.text_input("Researcher")
			note = st.text_area("Annotation")
			if st.form_submit_button("Save local annotation"):
				if author.strip() and note.strip():
					append_annotation(
						workspace / "annotations", packet["incident_id"], note, author, utc_now()
					)
					st.success("Annotation saved with its own knowledge timestamp.")
				else:
					st.error("Enter a researcher name and annotation.")
		for path in sorted((workspace / "annotations").glob("*.json")):
			entry = json.loads(path.read_text())
			if entry["incident_id"] == packet["incident_id"]:
				st.write(f"{entry['author']} · {entry['known_at']}: {entry['text']}")
	else:
		scenarios = studies.get("execution", {}).get("scenarios", [])
		if scenarios:
			st.dataframe(
				pd.DataFrame(scenarios)[
					[
						"scenario",
						"net_pnl",
						"cash",
						"fees",
						"fills",
						"exposure",
						"mean_60s_markout_pp",
					]
				],
				hide_index=True,
				width="stretch",
			)
			selected = st.selectbox("Inspect paper run", [s["scenario"] for s in scenarios])
			paper = workspace / "paper" / selected
			if paper.exists():
				st.subheader("Orders and explicit rejections")
				st.dataframe(table(str(paper), "orders"), hide_index=True, width="stretch")
				st.subheader("Cash and inventory ledger")
				st.dataframe(table(str(paper), "ledger"), hide_index=True, width="stretch")
		st.caption(
			"All fills are simulated; passive queue scenarios are not guaranteed bounds. Rewards/rebates = 0."
		)
		st.subheader("Collector health")
		if (capture / "health.json").exists():
			st.json(json.loads((capture / "health.json").read_text()))
		else:
			st.info("Offline synthetic capture. No live collector or uptime claim.")
		st.json(json.loads((run / "summary.json").read_text())["events"], expanded=False)
		st.caption(
			"Pilot gate: reviewed live mappings, 72 hours plus a matchday, visible gaps, restore test and measured cost forecast. Eight weeks of recording remains a separate research milestone."
		)


if __name__ == "__main__":
	main()
