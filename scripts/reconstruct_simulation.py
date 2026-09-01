#!/usr/bin/env python3
"""Run the audited simulation-to-interim reconstruction entry point."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


COMPONENT_DATASET = {"battery": "SIM_bat", "reaction-wheel": "SIM_rwa"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        add_help=False,
        description="Reconstruct audited spacecraft simulation records into interim parquet files."
    )
    parser.add_argument("-?", "-help", "--help", action="help", help="Show this help message and exit.")
    parser.add_argument("--dataset-root", required=True, type=Path, help="Root of the downloaded BRPHM dataset tier.")
    parser.add_argument("--component", choices=("battery", "reaction-wheel", "both"), default="both")
    parser.add_argument("--output-root", type=Path, default=None, help="Optional output root; defaults to dataset root.")
    parser.add_argument("--workers", default="1", help="Loader workers; 1 is deterministic and portable.")
    parser.add_argument("--verify", action="store_true", help="Read back generated parquet files and verify hashes.")
    parser.add_argument("--files", nargs="*", default=None, help="Optional .mat names for a bounded smoke reconstruction.")
    return parser


def _loader(dataset_root: Path) -> Path:
    bundled = Path(__file__).resolve().with_name("sim_loader.py")
    candidates = (
        bundled,
        dataset_root / "processing" / "source_snapshot" / "src" / "datasets" / "sim_loader.py",
        dataset_root.parent / "processing" / "source_snapshot" / "src" / "datasets" / "sim_loader.py",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("sim_loader.py is not present in preview or the downloaded dataset")


def _run(loader: Path, dataset_root: Path, dataset: str, output_root: Path, workers: str, verify: bool, files: list[str] | None) -> int:
    command = [sys.executable, str(loader), "--dataset", dataset, "--root", str(dataset_root), "--workers", workers]
    raw_dir = dataset_root / "raw" / "sim" / ("bat" if dataset == "SIM_bat" else "rwa")
    out_dir = output_root / "interim" / dataset
    command.extend(("--raw-dir", str(raw_dir), "--out", str(out_dir)))
    if files:
        command.extend(("--files", *files))
    if verify:
        command.append("--verify")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(Path(__file__).resolve().parent.parent), str(dataset_root), env.get("PYTHONPATH", "")))
    completed = subprocess.run(command, cwd=dataset_root, env=env)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dataset_root = args.dataset_root.expanduser().resolve()
    output_root = (args.output_root or dataset_root).expanduser().resolve()
    try:
        loader = _loader(dataset_root)
        datasets = tuple(COMPONENT_DATASET.values()) if args.component == "both" else (COMPONENT_DATASET[args.component],)
        for dataset in datasets:
            code = _run(loader, dataset_root, dataset, output_root, args.workers, args.verify, args.files)
            if code:
                return code
    except (FileNotFoundError, OSError) as exc:
        print(f"simulation reconstruction unavailable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
