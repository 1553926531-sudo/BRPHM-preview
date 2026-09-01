"""Single public command line for the BRPHM competition release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent
CONFIG = Path("configs/competition/s22_s21.json")
REPRODUCTION_ROOT = Path("results/reproduction")


class ModelVerificationError(RuntimeError):
    """The recorded default model identity does not match the release files."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _historical_best_record(root: Path) -> dict:
    selector = root / "release" / "historical_best.json"
    try:
        record = json.loads(selector.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelVerificationError("无法读取默认模型说明") from exc
    if not isinstance(record, dict):
        raise ModelVerificationError("默认模型说明格式无效")
    return record


def _recorded_file(root: Path, value: object, expected: object, label: str) -> Path:
    try:
        path = (root / str(value)).resolve()
        path.relative_to(root.resolve())
    except (TypeError, ValueError) as exc:
        raise ModelVerificationError(f"{label}路径无效") from exc
    if not path.is_file():
        raise ModelVerificationError(f"{label}不存在")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ModelVerificationError(f"{label}缺少有效校验值")
    if _sha256(path) != expected:
        raise ModelVerificationError(f"{label}校验值不一致")
    return path


def default_production_manifest(root: Path = ROOT) -> Path:
    """Resolve the production manifest recorded as the historical best."""
    try:
        record = _historical_best_record(root)
        return _recorded_file(
            root,
            record["production_manifest"],
            record["production_manifest_sha256"],
            "默认模型索引",
        )
    except KeyError as exc:
        raise ModelVerificationError("默认模型说明缺少模型索引") from exc


def production_manifest_environment(root: Path = ROOT) -> dict[str, str]:
    """Return the environment binding used by the dashboard service."""
    record = _historical_best_record(root)
    value = record.get("production_manifest")
    if not isinstance(value, str) or not value:
        raise ModelVerificationError("默认模型说明缺少模型索引")
    return {"RUL_DASHBOARD_PRODUCTION_MANIFEST": value}


def _verify_default_model(root: Path) -> dict:
    record = _historical_best_record(root)
    manifest_path = default_production_manifest(root)
    try:
        _recorded_file(
            root,
            record["implementation"],
            record["implementation_sha256"],
            "预测程序",
        )
        _recorded_file(
            root,
            record["config"],
            record["config_sha256"],
            "模型配置",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("implementation_sha256")
            != record["historical_model_implementation_sha256"]
        ):
            raise ModelVerificationError("历史模型与训练实现记录不一致")
        for component in ("bat", "rwa"):
            selected_members = record[component]["members"]
            indexed_members = manifest["routes"][component]["members"]
            selected_identity = [
                (item["path"], item["sha256"]) for item in selected_members
            ]
            indexed_identity = [
                (item["path"], item["sha256"]) for item in indexed_members
            ]
            if selected_identity != indexed_identity or len(selected_identity) != 3:
                raise ModelVerificationError("默认模型成员记录不一致")
            for index, member in enumerate(selected_members, start=1):
                _recorded_file(
                    root,
                    member["path"],
                    member["sha256"],
                    f"模型文件 {component.upper()}-{index}",
                )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ModelVerificationError("默认模型成员记录格式无效") from exc
    return record


def _device(value: str) -> str:
    if value != "auto":
        return value
    try:
        import torch
    except ImportError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _root(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else ROOT


def _common(args: argparse.Namespace, root: Path) -> list[str]:
    return [
        "--root", str(root),
        "--config", str((root / CONFIG).resolve()),
        "--device", _device(args.device),
        "--bat-route", "S22",
        "--rwa-route", "S21",
    ]


def _path(root: Path, value: str) -> str:
    path = Path(value).expanduser()
    return str(path.resolve() if path.is_absolute() else (root / path).resolve())


def _public_cli_value(value: object) -> object:
    """Keep direct-file CLI output readable without exposing internal field names."""
    if isinstance(value, list):
        return [_public_cli_value(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            public_key = {
                "manifest_sha256": "model_version_hash",
                "member_sha256s": "member_hashes",
                "interval_member_seeds": "member_count",
                "production_point_seed": "selected_member",
                "uncertainty_mode": "uncertainty_description",
                "input_contract": "input_requirements",
            }.get(key, key)
            if key == "interval_member_seeds":
                result[public_key] = len(item) if isinstance(item, list) else None
            else:
                result[public_key] = _public_cli_value(item)
        return result
    if isinstance(value, str):
        return value.replace("three_seed", "three_model").replace("contract", "requirements")
    return value


def _component(value: str) -> str:
    return {
        "battery": "bat",
        "reaction-wheel": "rwa",
        "bat": "bat",
        "rwa": "rwa",
        "both": "both",
    }[value]


def _component_argument(value: str) -> str:
    if value not in {"both", "battery", "reaction-wheel", "bat", "rwa"}:
        raise argparse.ArgumentTypeError(
            "部件必须是 both、battery 或 reaction-wheel"
        )
    return value


def _split_argument(value: str) -> str:
    aliases = {
        "validation": "validation",
        "evaluation": "holdout",
        "holdout": "holdout",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(
            "split must be validation or evaluation"
        ) from exc


class _EnglishArgumentParser(argparse.ArgumentParser):
    """Argument parser whose public help surface is explicitly English."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)


def _add_help_aliases(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-h", "--help", "-?", "-help", action="help",
        help="Show this help message and exit.",
    )


def _parser() -> argparse.ArgumentParser:
    parser = _EnglishArgumentParser(
        prog="brphm",
        description=(
            "BRPHM PyTorch remaining-useful-life predictor. "
            "Prediction uses the verified historical-best model by default."
        ),
    )
    _add_help_aliases(parser)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{train,adapt,predict,reproduce,reconstruct,serve,verify}",
        parser_class=_EnglishArgumentParser,
    )

    def stage_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        item = sub.add_parser(name, help=help_text, description=help_text)
        _add_help_aliases(item)
        item.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="Compute device; auto selects CUDA when available, otherwise CPU.")
        item.add_argument("--output-dir", default=None, help="Output directory (default: results/reproduction).")
        return item

    stage_parser("train", "Train the base remaining-useful-life models.")
    adapt = stage_parser("adapt", "Adapt the base models with spacecraft data.")
    adapt.add_argument("--pretrain-dir", default=None, help="Base-model directory (default: the current reproduction output).")

    predict = sub.add_parser("predict", help="Predict remaining useful life for a component.", description="Predict remaining useful life. The verified historical-best model is used when --model is omitted.")
    _add_help_aliases(predict)
    predict.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="Compute device; auto selects CUDA when available, otherwise CPU.")
    predict.add_argument("--input", help="External CSV/Parquet or supported table file; requires --component.")
    predict.add_argument(
        "--component",
        type=_component_argument,
        metavar="{both,battery,reaction-wheel}",
        default="both",
        help="Component to predict (default: both built-in components).",
    )
    predict.add_argument(
        "--split",
        type=_split_argument,
        metavar="{validation,evaluation}",
        default="holdout",
        help="Built-in data split; ignored for external files.",
    )
    predict.add_argument(
        "--time-unit",
        choices=("auto", "cycle", "millisecond", "second", "minute", "hour", "day", "bin"),
        default="auto",
        help="External time-column unit; plain 'time' requires an explicit value.",
    )
    predict.add_argument("--model", help="Model file (default: the verified historical-best model).")
    predict.add_argument("--manifest", dest="model", help=argparse.SUPPRESS)
    predict.add_argument("--output-dir", default=None, help="Output directory (default: results/prediction).")

    reproduce = sub.add_parser("reproduce", help="Run training, adaptation, and validation prediction in order.", description="Reproduce the complete training, adaptation, and validation-prediction workflow.")
    _add_help_aliases(reproduce)
    reproduce.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="Compute device; auto selects CUDA when available, otherwise CPU.")

    reconstruct = sub.add_parser("reconstruct", help="Reconstruct spacecraft simulation records from downloaded data.", description="Reconstruct spacecraft simulation records into interim parquet files.")
    _add_help_aliases(reconstruct)
    reconstruct.add_argument("--dataset-root", required=True, help="Root of the downloaded BRPHM dataset tier.")
    reconstruct.add_argument("--component", choices=("battery", "reaction-wheel", "both"), default="both", help="Component records to reconstruct (default: both).")
    reconstruct.add_argument("--output-root", default=None, help="Output root (default: the downloaded dataset root).")
    reconstruct.add_argument("--workers", default="1", help="Loader workers; 1 is deterministic and portable.")
    reconstruct.add_argument("--verify", action="store_true", help="Read back generated parquet files and verify hashes.")
    reconstruct.add_argument("--files", nargs="*", default=None, help="Optional absolute .mat paths for a bounded reconstruction.")

    serve = sub.add_parser("serve", help="Start the local evaluator cockpit.", description="Start the local prediction and workflow cockpit.")
    _add_help_aliases(serve)
    serve.add_argument("--address", default="127.0.0.1", help="Address to bind.")
    serve.add_argument("--port", type=int, default=8501, help="Port to bind.")
    serve.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=2, help="Number of worker processes.")

    verify = sub.add_parser("verify", help="Verify the default model and release files.", description="Verify that the default model files exist and report their identity.")
    _add_help_aliases(verify)
    verify.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser


def _run_stage(args: argparse.Namespace, root: Path) -> int:
    from src.competition_s22_s21 import pretrain_main, transfer_main

    if args.command == "train":
        output = _path(root, args.output_dir or (REPRODUCTION_ROOT / "pretrain").as_posix())
        return pretrain_main(_common(args, root) + ["--output-dir", output])
    output = _path(root, args.output_dir or (REPRODUCTION_ROOT / "production").as_posix())
    pretrain = _path(root, args.pretrain_dir or (REPRODUCTION_ROOT / "pretrain").as_posix())
    return transfer_main(_common(args, root) + ["--pretrain-dir", pretrain, "--output-dir", output])


def _run_predict(args: argparse.Namespace, root: Path, *, manifest: Path | None = None, output: Path | None = None) -> int:
    if args.input and Path(args.input).suffix.lower() not in {".pt", ".pth"}:
        if args.component == "both":
            raise SystemExit("外部输入一次只能指定一个部件：--component battery 或 --component reaction-wheel")
        try:
            os.environ.update(production_manifest_environment(root))
            from dashboard.telemetry_upload import TelemetryPredictionService
            source = Path(_path(root, args.input))
            if not source.is_file():
                print(f"输入文件不存在：{source}", file=sys.stderr)
                return 2
            service = TelemetryPredictionService(root)
            status, response = service.predict_files(
                [(source.name, source.read_bytes())],
                line=_component(args.component),
                time_unit=args.time_unit,
            )
            target = Path(_path(root, args.output_dir or "results/prediction"))
            target.mkdir(parents=True, exist_ok=True)
            batch_id = response.get("batch_id")
            if batch_id:
                csv_text = service.export(batch_id)
                if csv_text is not None:
                    destination = target / "predictions.csv"
                    if isinstance(csv_text, bytes):
                        destination.write_bytes(csv_text)
                    else:
                        destination.write_text(str(csv_text), encoding="utf-8")
            print(json.dumps(_public_cli_value(response), ensure_ascii=True))
            return 0 if status == 200 else status if status == 207 else 2
        except Exception as exc:
            print(f"外部输入预测失败：{exc}", file=sys.stderr)
            return 2
    from src.competition_s22_s21 import predict_main

    if args.input and args.component == "both":
        raise SystemExit("外部输入一次只能指定一个部件：--component battery 或 --component reaction-wheel")
    selected_manifest = manifest or (
        Path(_path(root, args.model)) if args.model else default_production_manifest(root)
    )
    selected_output = output or Path(_path(root, args.output_dir or "results/prediction"))
    return predict_main(
        _common(args, root)
        + ["--manifest", str(selected_manifest), "--output-dir", str(selected_output),
           "--split", args.split, "--component", _component(args.component)]
        + (["--input", _path(root, args.input)] if args.input else [])
    )


def _run_reconstruct(args: argparse.Namespace, root: Path) -> int:
    from scripts.reconstruct_simulation import main as reconstruct_main

    command = ["--dataset-root", _path(root, args.dataset_root), "--component", args.component, "--workers", str(args.workers)]
    if args.output_root:
        command.extend(("--output-root", _path(root, args.output_root)))
    if args.verify:
        command.append("--verify")
    if args.files:
        command.extend(("--files", *(_path(root, value) for value in args.files)))
    return reconstruct_main(command)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = _root(args.root)
    if args.command in {"train", "adapt"}:
        return _run_stage(args, root)
    if args.command == "predict":
        return _run_predict(args, root)
    if args.command == "reconstruct":
        return _run_reconstruct(args, root)
    if args.command == "reproduce":
        train_values = vars(args).copy()
        train_values.update(command="train", output_dir=None)
        train = argparse.Namespace(**train_values)
        adapt_values = vars(args).copy()
        adapt_values.update(command="adapt", output_dir=None, pretrain_dir=None)
        adapt = argparse.Namespace(**adapt_values)
        result = _run_stage(train, root)
        if result:
            return result
        result = _run_stage(adapt, root)
        if result:
            return result
        predict_values = vars(args).copy()
        predict_values.update(
            command="predict",
            input=None,
            component="both",
            split="validation",
            model=None,
            output_dir="results/reproduction/prediction",
        )
        predict = argparse.Namespace(**predict_values)
        return _run_predict(
            predict, root,
            manifest=root / REPRODUCTION_ROOT / "production" / "manifest.json",
            output=root / REPRODUCTION_ROOT / "prediction",
        )
    if args.command == "serve":
        try:
            os.environ.update(production_manifest_environment(root))
        except ModelVerificationError as exc:
            print(f"默认预测模型校验失败：{exc}", file=sys.stderr)
            return 2
        from dashboard.server import main as server_main
        return server_main(["--address", args.address, "--port", str(args.port), "--workers", str(args.workers)])
    try:
        record = _verify_default_model(root)
    except ModelVerificationError as exc:
        print(f"默认预测模型校验失败：{exc}", file=sys.stderr)
        return 2
    result = {
        "status": "verified",
        "model": "release/historical_best.json",
        "implementation_sha256": record.get("implementation_sha256"),
        "metrics": {
            "battery": {
                "rmse": record["bat"]["target_holdout"]["rmse"],
                "unit": record["bat"]["target_holdout"]["unit"],
            },
            "reaction_wheel": {
                "rmse": record["rwa"]["target_holdout"]["rmse"],
                "unit": record["rwa"]["target_holdout"]["unit"],
            },
        },
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(
            "默认预测模型已核对\n"
            f"电池部件历史 RMSE：{result['metrics']['battery']['rmse']} cycles\n"
            f"反作用轮部件历史 RMSE：{result['metrics']['reaction_wheel']['rmse']} days"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
