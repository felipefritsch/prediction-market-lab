"""Execute all notebook companions in fresh kernels and save reviewed artifacts."""

import argparse
import os
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--workspace", type=Path, default=Path("data/demo"))
	parser.add_argument("--out", type=Path, default=Path("artifacts/notebooks"))
	args = parser.parse_args()
	root = Path(__file__).resolve().parents[1]
	os.environ["PML_WORKSPACE"] = str(args.workspace.resolve())
	args.out.mkdir(parents=True, exist_ok=True)
	for path in sorted((root / "notebooks").glob("*.ipynb")):
		notebook = nbformat.read(path, as_version=4)
		nbformat.validate(notebook)
		NotebookClient(
			notebook,
			timeout=180,
			kernel_name="python3",
			resources={"metadata": {"path": str(root)}},
		).execute()
		nbformat.write(notebook, args.out / path.name)
		print(f"Executed: {path.name}")


if __name__ == "__main__":
	main()
