import json
from pathlib import Path, PureWindowsPath

import pytest

from src import competition_s22_s21 as module


def test_manifest_artifact_path_resolves_declared_absolute_alias(tmp_path: Path):
    local = tmp_path / "data" / "processed" / "bat_target_train.pt"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"payload")
    legacy_root = (tmp_path / "legacy" / "BRPHM" / "rul-space").resolve()
    legacy_path = legacy_root / "data" / "processed" / "bat_target_train.pt"

    resolved = module._manifest_artifact_path(
        tmp_path,
        str(legacy_path),
        "target tensor",
        artifact_aliases={str(legacy_root): "."},
    )

    assert resolved == local.resolve()


def test_manifest_artifact_path_resolves_posix_alias_with_windows_path_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    local = tmp_path / "data" / "processed" / "bat_target_train.pt"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"payload")
    source = "/mnt/data/BRPHM/rul-space"
    value = f"{source}/data/processed/bat_target_train.pt"
    native_path = module.Path

    assert not PureWindowsPath(value).is_absolute()

    def windows_path_for_manifest_value(path: str | Path):
        if str(path) == value:
            return PureWindowsPath(value)
        return native_path(path)

    monkeypatch.setattr(module, "Path", windows_path_for_manifest_value)

    resolved = module._manifest_artifact_path(
        tmp_path, value, "target tensor", artifact_aliases={source: "."}
    )

    assert resolved == local.resolve()


def test_manifest_artifact_path_rejects_unregistered_posix_path(tmp_path: Path):
    value = "/mnt/unregistered/bat_target_train.pt"

    with pytest.raises(module.CompetitionContractError, match="escapes the project root"):
        module._manifest_artifact_path(tmp_path, value, "target tensor")


def test_historical_implementation_hash_is_only_accepted_as_legacy_contract(tmp_path: Path):
    artifact = {
        "implementation": "src/competition_s22_s21.py",
        "implementation_sha256": "a0c20be5ecdf33075705941fed48a57ce2cdd659ad5d6b4502a11a6faf92a0cd",
    }

    module._validate_implementation_contract(artifact, tmp_path, "historical checkpoint")

    with pytest.raises(module.CompetitionContractError):
        module._validate_implementation_contract(
            {**artifact, "implementation_sha256": "0" * 64}, tmp_path, "untrusted artifact"
        )


def test_package_root_is_used_when_frozen_data_root_is_not_materialized(tmp_path: Path):
    (tmp_path / "data" / "processed").mkdir(parents=True)
    config = {"data_root": "paper/ieee_bilingual/reproduction/holdout_plan_b"}

    assert module.resolve_data_root(tmp_path, config) == tmp_path.resolve()
