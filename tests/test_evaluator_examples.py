import subprocess
import sys
from pathlib import Path

import pytest

from dashboard import telemetry_upload


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("command", ("", "train", "adapt", "predict", "reproduce", "serve", "verify"))
@pytest.mark.parametrize("alias", ("-?", "-help", "--help"))
def test_cli_help_aliases(command: str, alias: str) -> None:
    arguments = [sys.executable, "-m", "brphm"]
    if command:
        arguments.append(command)
    arguments.append(alias)
    result = subprocess.run(arguments, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage:")


def test_examples_have_six_shapes_and_nonempty_downloads() -> None:
    catalog = telemetry_upload.telemetry_example_catalog()
    assert len(catalog["examples"]) == 6
    for example in catalog["examples"]:
        for layout in ("wide", "long"):
            content, filename, _ = telemetry_upload.telemetry_example_content(example["id"], "csv", layout)
            assert content and filename.endswith(".csv")
            if example["kind"] != "empty_template":
                parsed = telemetry_upload.parse_upload(filename, content)
                assert parsed.table_layout == layout
                assert parsed.records


def test_empty_jsonl_template_is_a_downloadable_nonempty_file() -> None:
    content, filename, media_type = telemetry_upload.telemetry_example_content("battery-empty", "jsonl", "wide")
    assert content == b"\n"
    assert filename.endswith(".jsonl")
    assert media_type.startswith("application/json")


def test_production_predictor_resolves_registered_posix_assets_inside_package(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "data" / "processed" / "norm.json"
    expected.parent.mkdir(parents=True)
    expected.write_text("{}", encoding="utf-8")
    manifest = {"artifact_aliases": {"/mnt/data/BRPHM/rul-space": "."}}

    resolved = telemetry_upload.ProductionPredictor(tmp_path)._manifest_asset_path(
        manifest,
        "/mnt/data/BRPHM/rul-space/data/processed/norm.json",
        "normalization",
    )

    assert resolved == expected.resolve()


@pytest.mark.parametrize("alias", ("-?", "-help", "--help"))
def test_simulation_entrypoint_help_aliases(alias: str) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/reconstruct_simulation.py", alias],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Reconstruct audited spacecraft simulation records" in result.stdout
