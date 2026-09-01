import json
import os
from pathlib import Path

import pytest


HISTORICAL_IMPLEMENTATION_SHA256 = (
    "a0c20be5ecdf33075705941fed48a57ce2cdd659ad5d6b4502a11a6faf92a0cd"
)
HISTORICAL_BAT_RMSE = 1.565145748990264
HISTORICAL_RWA_RMSE = 0.018440836607000497


def _preview_root() -> Path:
    configured = os.environ.get("BRPHM_PREVIEW_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1]


def test_preview_contains_historical_best_runtime_and_release_scaffolding():
    root = _preview_root()
    results_root = root / "results" / "competition" / "s22_s21_gpu_pathfix35_rwa_torch_hgb_parity_20260830"
    manifest_path = results_root / "production" / "manifest.json"
    selection_path = root / "release" / "historical_best.json"

    if not os.environ.get("BRPHM_PREVIEW_ROOT") and not manifest_path.is_file():
        pytest.skip("Rack preview artifacts are audited in the explicit preview root")

    assert manifest_path.is_file(), "historical production manifest is required"
    assert selection_path.is_file(), "historical model selection evidence is required"
    assert (root / "Dockerfile").is_file()
    assert (root / ".gitattributes").is_file()
    assert (root / ".gitignore").is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert manifest["implementation_sha256"] == HISTORICAL_IMPLEMENTATION_SHA256
    assert selection["bat"]["target_holdout"]["rmse"] == HISTORICAL_BAT_RMSE
    assert selection["rwa"]["target_holdout"]["rmse"] == HISTORICAL_RWA_RMSE

    required = [
        root / "src" / "competition_s22_s21.py",
        results_root / "production" / "production_seal_receipt.json",
        results_root / "transfer" / "transfer_receipt.json",
        results_root / "pretrain" / "pretrain_receipt.json",
    ]
    required.extend(root / item["path"] for route in manifest["routes"].values() for item in route["members"])
    required.extend(
        root / item["path"]
        for line in json.loads((results_root / "pretrain" / "pretrain_receipt.json").read_text(encoding="utf-8"))["lines"].values()
        for item in line["members"]
    )
    assert all(path.is_file() for path in required), [str(path) for path in required if not path.is_file()]

    package_manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    package_paths = {entry["path"] for entry in package_manifest["files"]}
    assert not {
        "data/README.md",
        "dashboard/CONTRACT.md",
        "docs/evaluation.md",
        "docs/final_audit.md",
        "docs/technical_report.md",
    } & package_paths
    forbidden = {"__pycache__", ".pyc", ".bak", ".pytest_cache"}
    assert not any(
        any(token in part for token in forbidden)
        for path in package_paths
        for part in Path(path).parts
    )
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "__pycache__/" in ignored
    assert "*.py[cod]" in ignored


def test_preview_uses_three_consolidated_reviewer_documents():
    root = _preview_root()
    required = {
        "README.md",
        "docs/reproduction.md",
        "docs/data_and_simulation.md",
    }
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.md")
    }
    assert required <= actual
    assert not {
        "data/README.md",
        "dashboard/CONTRACT.md",
        "docs/evaluation.md",
        "docs/final_audit.md",
        "docs/technical_report.md",
    } & actual

    readme = (root / "README.md").read_text(encoding="utf-8")
    reproduction = (root / "docs" / "reproduction.md").read_text(encoding="utf-8")
    data_and_simulation = (root / "docs" / "data_and_simulation.md").read_text(
        encoding="utf-8"
    )
    assert "{train,adapt,predict,reproduce,reconstruct,serve,verify}" in readme
    assert "python -m brphm reconstruct" in readme
    assert "python -m brphm reconstruct" in reproduction
    assert "dashboard/CONTRACT.md" not in readme
    assert "dashboard/CONTRACT.md" not in reproduction
    assert "data/README.md" not in readme
    assert "data/README.md" not in reproduction
    assert "BRPHM_RUL_standard" in data_and_simulation
