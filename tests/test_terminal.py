import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from prediction_market_lab.replay import replay
from prediction_market_lab.reporting import report
from prediction_market_lab.synthetic import generate


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
	root = tmp_path_factory.mktemp("terminal")
	generate(root / "capture", fixtures=4)
	primary = root / "paper" / "taker-250ms-q1"
	replay(root / "capture", primary, strategy="taker")
	report(
		primary,
		root / "research",
		paper_runs={"taker-250ms-q1": primary},
		evidence_path=root / "capture" / "evidence.json",
	)
	(root / "workspace.json").write_text(
		json.dumps({"run": "paper/taker-250ms-q1", "research": "research", "capture": "capture"})
	)
	return root


def test_all_terminal_views_and_replay_cursor(workspace, monkeypatch):
	monkeypatch.setattr(sys, "argv", ["app.py", str(workspace)])
	path = Path(__file__).parents[1] / "src" / "prediction_market_lab" / "app.py"
	app = AppTest.from_file(str(path), default_timeout=30).run()
	assert not app.exception
	for view in (
		"Fixture replay",
		"Microstructure",
		"Incidents",
		"Paper portfolio and health",
		"Universe and coverage",
	):
		app.sidebar.radio[0].set_value(view).run()
		assert not app.exception, (view, app.exception)
		if view == "Fixture replay":
			app.slider[0].set_value(2).run()
			assert not app.exception
