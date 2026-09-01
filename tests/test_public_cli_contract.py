import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(os.environ.get("BRPHM_PREVIEW_ROOT", Path(__file__).resolve().parents[1]))


def test_public_help_lists_only_evaluator_workflows() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "brphm", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    commands = ("train", "adapt", "predict", "reproduce", "reconstruct", "serve", "verify")
    assert all(command in result.stdout for command in commands)
    assert "{train,adapt,predict,reproduce,reconstruct,serve,verify}" in result.stdout
    assert "competition_s22_s21" not in result.stdout
    assert "S22" not in result.stdout
    assert "S21" not in result.stdout


def test_prediction_help_uses_public_model_name() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "brphm", "predict", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--model" in result.stdout
    assert "--manifest" not in result.stdout
    assert "{both,battery,reaction-wheel}" in result.stdout
    assert "{validation,evaluation}" in result.stdout
    assert "holdout" not in result.stdout


def test_verify_reports_public_model_identity_and_metrics() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "brphm", "verify", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "status": "verified",
        "model": "release/historical_best.json",
        "implementation_sha256": "91cdac9538c621cb4f070c7b13f09bd30efcbf010bebcd1cf904edf5e96bf5cc",
        "metrics": {
            "battery": {"rmse": 1.565145748990264, "unit": "cycle"},
            "reaction_wheel": {"rmse": 0.018440836607000497, "unit": "day"},
        },
    }
    public_output = result.stdout.lower()
    assert all(token not in public_output for token in ("s21", "s22", "manifest", "checkpoint", "holdout"))


def test_verify_rejects_a_modified_default_model_without_traceback(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (tmp_path / "model.json").write_text("{}", encoding="utf-8")
    selection = {
        "production_manifest": "model.json",
        "production_manifest_sha256": "0" * 64,
        "implementation": "implementation.py",
        "implementation_sha256": "0" * 64,
        "config": "config.json",
        "config_sha256": "0" * 64,
        "bat": {
            "target_holdout": {"rmse": 1.0, "unit": "cycle"},
            "members": [],
        },
        "rwa": {
            "target_holdout": {"rmse": 1.0, "unit": "day"},
            "members": [],
        },
    }
    (release_dir / "historical_best.json").write_text(
        json.dumps(selection),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "brphm",
            "--root",
            str(tmp_path),
            "verify",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "默认预测模型校验失败" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher test")
def test_posix_launcher_prefers_the_active_python_environment(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    active_python = bin_dir / "python"
    active_python.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    active_python.chmod(0o755)
    fallback_python = bin_dir / "python3"
    fallback_python.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    fallback_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(ROOT / "brphm"), "verify", "--json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "verified"


@pytest.mark.parametrize(
    ("public_name", "subcommand", "values", "confirmed"),
    [
        ("核对默认预测模型", "verify", {}, False),
        ("完整复现预测流程", "reproduce", {"device": "cpu"}, True),
        (
            "重建航天仿真数据",
            "reconstruct",
            {
                "dataset_root": str(ROOT / "data"),
                "component": "both",
                "workers": "1",
            },
            True,
        ),
        ("训练部件寿命模型", "train", {"device": "cpu"}, True),
        ("用航天数据调整模型", "adapt", {"device": "cpu"}, True),
        ("预测部件剩余寿命", "predict", {"device": "cpu"}, True),
    ],
)
def test_dashboard_uses_the_same_public_entrypoint(
    public_name: str,
    subcommand: str,
    values: dict[str, str],
    confirmed: bool,
) -> None:
    from dashboard import operations

    operation, argv = operations.build_command(public_name, values, confirmed=confirmed)
    assert operation["label"]
    assert argv[1:3] == ["-m", "brphm"]
    assert subcommand in argv
    assert "competition_s22_s21" not in " ".join(argv)


def test_dashboard_exposes_only_runnable_evaluator_workflows() -> None:
    from dashboard import operations

    payload = operations.operation_payload()
    public_operations = payload["user_operations"]
    assert [item["id"] for item in public_operations] == [
        "核对默认预测模型",
        "完整复现预测流程",
        "重建航天仿真数据",
        "训练部件寿命模型",
        "用航天数据调整模型",
        "预测部件剩余寿命",
    ]
    assert all(item["availability"]["available"] is True for item in public_operations)


def test_simulation_reconstruction_has_english_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "brphm", "reconstruct", "-?"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Reconstruct spacecraft simulation records" in result.stdout
    assert "--dataset-root" in result.stdout
    assert "--verify" in result.stdout


def test_prediction_defaults_to_historical_best_manifest() -> None:
    from brphm import default_production_manifest

    historical = json.loads((ROOT / "release" / "historical_best.json").read_text(encoding="utf-8"))
    expected = (ROOT / historical["production_manifest"]).resolve()
    assert default_production_manifest(ROOT) == expected


def test_serve_uses_the_verified_default_model_location() -> None:
    from brphm import production_manifest_environment

    historical = json.loads((ROOT / "release" / "historical_best.json").read_text(encoding="utf-8"))
    assert production_manifest_environment(ROOT) == {
        "RUL_DASHBOARD_PRODUCTION_MANIFEST": historical["production_manifest"],
    }


def test_external_prediction_uses_the_same_upload_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import brphm

    input_path = tmp_path / "input.csv"
    input_path.write_bytes(b"cycle,bat.capacity_ah,bat.temp_mean_c,bat.charge_time_s\n0,2.0,20,3000\n")

    class Service:
        def __init__(self, root):
            self.root = root

        def predict_files(self, uploads, *, line, time_unit):
            assert uploads[0][0] == "input.csv"
            assert line == "bat"
            assert time_unit == "auto"
            return 200, {"status": "complete", "counts": {"submitted": 1, "predicted": 1, "rejected": 0}, "batch_id": "a" * 32}

        def export(self, batch_id):
            assert batch_id == "a" * 32
            return b"prediction\n1\n"

    monkeypatch.setattr("dashboard.telemetry_upload.TelemetryPredictionService", Service)
    monkeypatch.setattr(brphm, "production_manifest_environment", lambda _root: {})
    assert brphm.main(["--root", str(tmp_path), "predict", "--input", str(input_path), "--component", "battery"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    assert (tmp_path / "results" / "prediction" / "predictions.csv").read_bytes() == b"prediction\n1\n"


def test_external_prediction_json_is_ascii_safe_for_legacy_windows_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import brphm

    input_path = tmp_path / "input.csv"
    input_path.write_bytes(b"cycle,bat.capacity_ah,bat.temp_mean_c,bat.charge_time_s\n0,2.0,20,3000\n")

    class Service:
        def __init__(self, root):
            self.root = root

        def predict_files(self, uploads, *, line, time_unit):
            return 200, {"status": "complete", "model_name": "电池部件剩余寿命预测模型"}

        def export(self, batch_id):
            return None

    monkeypatch.setattr("dashboard.telemetry_upload.TelemetryPredictionService", Service)
    monkeypatch.setattr(brphm, "production_manifest_environment", lambda _root: {})
    assert brphm.main(["--root", str(tmp_path), "predict", "--input", str(input_path), "--component", "battery"]) == 0
    output = capsys.readouterr().out
    assert output.isascii()
    assert json.loads(output)["model_name"] == "电池部件剩余寿命预测模型"


@pytest.mark.parametrize(
    ("failed_stage", "expected_calls", "failure_code"),
    [
        ("train", ["train"], 17),
        ("adapt", ["train", "adapt"], 23),
    ],
)
def test_reproduce_stops_at_the_first_failed_stage(
    monkeypatch: pytest.MonkeyPatch,
    failed_stage: str,
    expected_calls: list[str],
    failure_code: int,
) -> None:
    import brphm

    calls: list[str] = []

    def run_stage(args, _root):
        calls.append(args.command)
        return failure_code if args.command == failed_stage else 0

    def run_predict(*_args, **_kwargs):
        calls.append("predict")
        return 0

    monkeypatch.setattr(brphm, "_run_stage", run_stage)
    monkeypatch.setattr(brphm, "_run_predict", run_predict)

    assert brphm.main(["reproduce", "--device", "cpu"]) == failure_code
    assert calls == expected_calls
