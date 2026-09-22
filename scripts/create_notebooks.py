"""Canonical notebook sources; regenerate with this script instead of editing JSON."""

import subprocess
import sys
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
SETUP = """import os, json
from pathlib import Path
import pandas as pd
import plotly.express as px
from prediction_market_lab.research import load_table, liquidity_study, shock_study, execution_study
from prediction_market_lab.storage import verify_run

workspace = Path(os.environ.get("PML_WORKSPACE", "data/demo")).resolve()
descriptor = json.loads((workspace / "workspace.json").read_text())
run = workspace / descriptor["run"]
manifest = verify_run(run)
features = load_table(run, "features")
provenance = sorted(features.input_provenance.unique())
print({"input_provenance": provenance, "input_sha256": manifest["input_sha256"], "feature_rows": len(features)})
px.defaults.template = "plotly_white"
px.defaults.color_discrete_sequence = ["#315c8c", "#b9812b", "#78619e"]
"""

SPECS = [
	(
		"01_liquidity",
		"Liquidity and price discovery",
		"Does aggregate imbalance help predict price changes after accounting for event dependence?",
		"Fixed 10/60/300-second labels use probability points and ticks. YES assets avoid counting binary complements twice. Training precedes held-out fixtures; overlapping labels are purged. Event bootstrap intervals require at least five events.",
		"""study = liquidity_study(features)
assert study["status"] == "ok"
assert set(study["split"]["train_events"]).isdisjoint(study["split"]["test_events"])
metrics = pd.DataFrame(study["forecast_metrics"])
metrics""",
		"""plot = metrics.dropna(subset=["mean"])
fig = px.bar(plot, x="horizon_seconds", y="mean", color="model", barmode="group",
    title="Held-out event-mean prediction error", labels={"horizon_seconds": "Horizon (seconds)", "mean": "RMSE (probability points)"})
fig.show()""",
		"""display(pd.DataFrame(study["classification"].get("metrics", [])))
display(pd.DataFrame(study["coverage"]))
print("Available labels:", study["label_availability"])
print("\n".join(study["limitations"]))""",
	),
	(
		"02_information_shocks",
		"Information and liquidity shocks",
		"How do spreads and depth change around scheduled kickoff and receipt of scoreboard updates?",
		"A scoreboard update is an observed message, not the instant of a goal. Corrections remain separate anchors. Compare 60-second windows around an anchor with a mechanically selected local period three minutes earlier. This is descriptive, not a causal estimate.",
		"""events = load_table(run, "events")
study = shock_study(features, events)
episodes = pd.DataFrame(study["episodes"])
episodes.head(12)""",
		"""fig = px.scatter(episodes, x="spread_change_pp", y="bid_depth_change_shares", color="anchor",
    hover_data=["fixture_id", "at"], title="Liquidity changes around received information",
    labels={"spread_change_pp": "After minus before spread (pp)", "bid_depth_change_shares": "After minus before bid depth (shares)"})
fig.add_vline(x=0, line_color="#777777")
fig.add_hline(y=0, line_color="#777777")
fig.show()""",
		"""print(json.dumps(study["event_uncertainty"], indent=2))
print("\n".join(study["limitations"]))""",
	),
	(
		"03_execution",
		"Execution and liquidity provision",
		"Which apparent gains remain after liquidity, fees, latency and fully funded inventory accounting?",
		"Compare taker, static and inventory-skew strategies at 0/250/1,000 ms extra latency. The documented market delay is additional for takers. Passive fills join behind displayed depth, require verified trade evidence, and receive no cancellation credit. Queue alternatives are scenarios, not bounds.",
		"""runs = {p.parent.name: p.parent for p in (workspace / "paper").glob("*/manifest.json")}
study = execution_study(runs)
scenarios = pd.DataFrame(study["scenarios"])
display(scenarios[["scenario", "fills", "fees", "net_pnl", "exposure"]])
for row in study["scenarios"]:
    if row["net_pnl"] is not None:
        from decimal import Decimal
        decomposed = Decimal(row["realized_price_pnl"]) + Decimal(row["settlement_pnl"]) + Decimal(row["inventory_price_revaluation"]) - Decimal(row["fees"])
        assert abs(decomposed - Decimal(row["net_pnl"])) < Decimal("0.00000001")""",
		"""scenarios["net_pnl_numeric"] = pd.to_numeric(scenarios.net_pnl)
fig = px.bar(scenarios, x="scenario", y="net_pnl_numeric", color="strategy",
    title="Paper outcome by latency and queue scenario<br><sup>Synthetic inputs are not economic evidence</sup>",
    labels={"scenario": "Declared execution scenario", "net_pnl_numeric": "Simulated net P&L (virtual dollars)"})
fig.add_hline(y=0, line_color="#777777")
fig.show()""",
		"""print("\n".join(study["limitations"]))
display(load_table(run, "ledger").tail(12))""",
	),
]


def main() -> None:
	(ROOT / "notebooks").mkdir(exist_ok=True)
	for name, title, question, method, results, plot, checks in SPECS:
		notebook = nbf.v4.new_notebook()
		notebook.metadata = {
			"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
			"language_info": {"name": "python", "version": "3.12"},
		}
		notebook.cells = [
			nbf.v4.new_markdown_cell(
				f"# {title}\n\n## Summary\n\n{question}\n\nThe default workspace is **synthetic**. Its outputs establish software behavior, not empirical market findings. Run `pml demo` first, then execute this notebook top to bottom. Observed data can replace the workspace without changing the calculation functions."
			),
			nbf.v4.new_markdown_cell(
				f"## Context and methods\n\n{method}\n\n### Key assumptions\n\nNo observed edge is claimed. Unusable books, unknown costs and unavailable labels must stay visible. Results below are derived or simulated; inspect the input provenance before interpreting them."
			),
			nbf.v4.new_markdown_cell(
				"## Data\n\nThe immutable run manifest identifies the exact captured inputs. Set `PML_WORKSPACE` to a workspace with the same layout to analyze another capture."
			),
			nbf.v4.new_code_cell(SETUP),
			nbf.v4.new_markdown_cell(
				"## Results\n\nThese calculations call the same tested functions as the terminal and CLI reports."
			),
			nbf.v4.new_code_cell(results),
			nbf.v4.new_code_cell(plot),
			nbf.v4.new_markdown_cell(
				"## Validation and limitations\n\nThe following tables and checks are part of the result, including insufficient samples and negative findings."
			),
			nbf.v4.new_code_cell(checks.replace('"\n"', '"\\n"')),
			nbf.v4.new_markdown_cell(
				"## Takeaways\n\nUse this experiment to inspect measurement, timing and execution assumptions. For synthetic inputs, differences across models or strategies have no external validity. For observed inputs, report the covered events, exclusions, uncertainty and sensitivity before making a market claim.\n\nMethod and sources: [research protocol](../docs/research-protocol.md), [data dictionary](../docs/data-dictionary.md), [source register](../docs/sources.md)."
			),
		]
		nbf.validate(notebook)
		nbf.write(notebook, ROOT / "notebooks" / f"{name}.ipynb")
	# Keep generated code subject to the same checks as the application.
	subprocess.run(
		[sys.executable, "-m", "ruff", "check", "--fix", "notebooks"], cwd=ROOT, check=True
	)
	subprocess.run([sys.executable, "-m", "ruff", "format", "notebooks"], cwd=ROOT, check=True)


if __name__ == "__main__":
	main()
