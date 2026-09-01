# -*- coding: utf-8 -*-
"""Machine-auditable command registry for the offline BRPHM cockpit.

The browser selects a registered operation and supplies typed values.  It can
never supply an executable or a command string.  Commands are always compiled
to a list of literal argv tokens and the control service executes that list
with ``shell=False``.

Two layers intentionally coexist:

* curated operations provide typed, Chinese forms for the project's common
  workflows;
* repository discovery exposes every remaining project CLI entry point, with
  a bounded ``argv_tokens`` array for arguments that cannot be inferred
  reliably.

Each discovered surface has exactly one coverage owner in the public catalog.
Curated shortcuts that share one entry point are marked as variants and do not
claim the surface twice.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
MMDD_RE = re.compile(r"^\d{4}$")

DISCOVERY_DIRECTORIES = ("scripts", "src", "dashboard", "sim")
PYTHON_SUFFIX = ".py"
NODE_SUFFIXES = {".js", ".mjs", ".cjs", ".ts"}
LAUNCHER_SUFFIXES = {".sh", ".ps1", ".bat", ".cmd"}
MATLAB_SUFFIX = ".m"
MAKEFILE_NAMES = {"Makefile", "makefile", "GNUmakefile"}
PACKAGE_FILE = "package.json"
IGNORED_SEGMENTS = {
    ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "__pycache__", "node_modules", "site-packages", "dist", "build",
    "vendor", "vendors", "third_party", "third-party", "fixtures",
    "testdata", "test_data", "tests",
}
IGNORED_FILE_PREFIXES = ("test_", ".")
INTERNAL_HELPERS = {"_cli_exec.py", "_serveropt.py"}
MAX_ARGV_TOKENS = 128
MAX_ARGV_TOKEN_LENGTH = 2048
MAX_ARGV_TOTAL_LENGTH = 16384
CONFIRMED_RISKS = {"write", "heavy", "production"}

COMPETITION_CONFIG = os.environ.get("RUL_DASHBOARD_COMPETITION_CONFIG", "configs/competition/s22_s21.json")
_LEGACY_COMPETITION_RESULT_ROOT = "results/competition/s22_s21_20260828"
_DEFAULT_COMPETITION_RESULT_ROOT = "results/competition/s22_s21_gpu_pathfix35_rwa_torch_hgb_parity_20260830"
if os.environ.get("RUL_DASHBOARD_COMPETITION_RESULT_ROOT", "").strip():
    COMPETITION_RESULT_ROOT = os.environ["RUL_DASHBOARD_COMPETITION_RESULT_ROOT"].strip()
elif (ROOT / _DEFAULT_COMPETITION_RESULT_ROOT / "validation" / "prediction_validation_receipt.json").is_file():
    COMPETITION_RESULT_ROOT = _DEFAULT_COMPETITION_RESULT_ROOT
else:
    # Keep legacy local fixtures usable; Rack snapshots select pathfix35 above.
    COMPETITION_RESULT_ROOT = _LEGACY_COMPETITION_RESULT_ROOT
COMPETITION_PRETRAIN_OUTPUT = f"{COMPETITION_RESULT_ROOT}/pretrain"
COMPETITION_PRODUCTION_OUTPUT = f"{COMPETITION_RESULT_ROOT}/production"
COMPETITION_MANIFEST = f"{COMPETITION_PRODUCTION_OUTPUT}/manifest.json"
COMPETITION_PREDICTION_OUTPUT = f"{COMPETITION_RESULT_ROOT}/prediction"
COMPETITION_ROUTES = {"BAT": "S22", "RWA": "S21"}
COMPETITION_LINE_CONTRACT: dict[str, Any] = {
    "framework": "PyTorch",
    "routes": dict(COMPETITION_ROUTES),
    "production_default": True,
    "route_overrides_allowed": False,
}


class OperationError(ValueError):
    """Raised when an operation request violates the machine contract."""


def _posix(path: Path) -> str:
    return path.as_posix()


def _relative_choices(pattern: str) -> list[str]:
    return [
        _posix(path.relative_to(ROOT))
        for path in sorted(ROOT.glob(pattern))
        if path.is_file()
    ]


def _run_choices() -> list[str]:
    folder = ROOT / "results" / "runs"
    return sorted(path.name for path in folder.iterdir() if path.is_dir()) if folder.is_dir() else []


def _field(key: str, label: str, kind: str, **values: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, **values}


PARAMETER_GUIDE: dict[str, tuple[str, str, Any]] = {
    "config": ("配置文件", "选择本次运行采用的受控配置文件。", "configs/example.yaml"),
    "manifest": ("流程配置", "选择本次运行采用的受控流程配置。", "configs/example.json"),
    "seed": ("复现编号", "用于保持同一处理结果可重复的编号。", 0),
    "epochs": ("训练轮数", "模型遍历训练数据的轮数。", 20),
    "device": ("计算设备", "选择自动、CPU 或可用加速设备；预检会核对当前环境。", "auto"),
    "workers": ("并行任务数", "控制并行数据处理或仿真进程数量。", "auto"),
    "dataset_root": ("已下载数据目录", "填写已下载 BRPHM 数据档的根目录；仅在该目录内存在原始仿真记录时运行。", "/path/to/BRPHM_RUL_standard"),
    "files": ("指定原始记录", "可选。用分号分隔绝对 .mat 路径，留空则重建所选部件的全部原始记录。", "/path/to/record.mat"),
    "mmdd": ("结果批次标识", "四位月日标识，仅用于区分本次生成的结果批次。", "0727"),
    "mode": ("运行模式", "选择该流程支持的执行模式。", "full"),
    "phase": ("执行阶段", "选择校准、独立评估或完整流程。", "all"),
    "run": ("已有结果批次", "选择需要评估的既有结果批次。", "已生成的结果批次"),
    "split": ("数据划分", "选择模型选择数据、独立评估数据或全部数据。", "all"),
    "domain": ("数据来源范围", "选择公开退化数据、航天数据或全部数据。", "all"),
    "figures": ("生成图件", "是否同时生成与本次结果绑定的图件。", True),
    "dry_run": ("只检查不写入", "启用后仅解析和核对计划，不生成正式产物。", True),
    "debug": ("附加检查子集", "仅在明确需要时启用，用于缩小检查范围。", False),
    "root": ("项目根目录", "指定命令使用的项目根目录；留空时采用登记默认值。", "."),
    "output": ("输出路径", "指定该工具写入的受控结果路径。", "results/qa/output.json"),
    "output_dir": ("输出目录", "指定该工具写入产物的目录。", "results/"),
    "url": ("服务地址", "指定本机或经 SSH 转发后的驾驶舱地址。", "http://127.0.0.1:8501/"),
    "address": ("监听地址", "指定控制服务监听地址；默认只允许回环地址。", "127.0.0.1"),
    "port": ("监听端口", "指定本机控制服务端口。", 8501),
    "argv_tokens": (
        "高级附加参数",
        "按顺序逐项添加受控表单未覆盖的参数；每项独立传入，绝不经过 Shell。",
        ["--help"],
    ),
}


def _parameter_name(field: dict[str, Any]) -> str:
    key = str(field.get("key", ""))
    return key[4:] if key.startswith("arg_") else key


def _decorate_field(field: dict[str, Any]) -> dict[str, Any]:
    """Add public Chinese parameter documentation without changing argv semantics."""
    item = dict(field)
    name = _parameter_name(item)
    guide = PARAMETER_GUIDE.get(name)
    label = str(item.get("label") or "").strip()
    if guide and (not label or label.startswith("-") or label.lower() == name.lower()):
        item["label"] = guide[0]
    item["required"] = item.get("required") is True
    if not item.get("description"):
        item["description"] = guide[1] if guide else (
            "按字段说明填写，系统会在提交前检查取值。"
        )
    default = item.get("default")
    item["default_display"] = "不传入" if default in (None, "", []) else default
    if guide:
        example = guide[2]
    elif default not in (None, "", []):
        example = default
    else:
        example = next((value for value in item.get("choices", []) if value != ""), "按脚本说明填写")
    item["example"] = example
    if item.get("choices"):
        item["allowed_values"] = list(item["choices"])
    elif item.get("min") is not None or item.get("max") is not None:
        item["allowed_values"] = {
            "min": item.get("min"), "max": item.get("max"), "step": item.get("step"),
        }
    elif item.get("pattern"):
        item["allowed_values"] = {"pattern": item["pattern"]}
    else:
        item["allowed_values"] = "按字段说明填写"
    return item


USER_CHOICE_TEXT = {
    "auto": "自动选择",
    "cpu": "CPU",
    "cuda": "可用加速设备",
    "mps": "Apple Silicon 加速设备",
    "freeze": "冻结主体参数",
    "full": "全量微调",
    "calibrate": "仅执行校准",
    "holdout": "仅执行独立评估数据推理",
    "all": "完整流程",
    "val": "模型选择集",
    "src": "公开退化数据",
    "tgt": "航天数据",
    "validation": "模型选择集",
}


# The browser is a business control surface, not a mirror of the development
# registry.  These IDs are intentionally Chinese and stable: the value sent by
# a user remains meaningful in a saved browser request, while the internal
# command identifier stays private and is resolved only in this module.
PUBLIC_OPERATION_METADATA: dict[str, dict[str, str]] = {
    "brphm_verify": {
        "id": "核对默认预测模型", "label": "核对默认预测模型",
        "description": "核对默认预测模型的文件身份，并显示两类部件的历史评估指标。",
    },
    "brphm_reproduce": {
        "id": "完整复现预测流程", "label": "完整复现预测流程",
        "description": "依次执行基础训练、航天数据调整和验证预测；任一步失败都会立即停止。",
    },
    "brphm_reconstruct": {
        "id": "重建航天仿真数据", "label": "重建航天仿真数据",
        "description": "把已下载的原始航天仿真记录重建为中间表格，并可在写后回读核对。缺少原始记录时会明确停止。",
    },
    "competition_s22_s21_train": {
        "id": "训练部件寿命模型", "label": "训练部件寿命模型（PyTorch）",
        "description": "使用 PyTorch 分别训练储能系统电池部件与姿态控制执行器反作用轮部件的剩余寿命模型。",
    },
    "competition_s22_s21_transfer": {
        "id": "用航天数据调整模型", "label": "用航天数据调整模型（PyTorch）",
        "description": "在 PyTorch 中用航天数据分别校准两类部件的输入尺度和退化规律。",
    },
    "competition_s22_s21_predict": {
        "id": "预测部件剩余寿命", "label": "预测部件剩余寿命（PyTorch）",
        "description": "加载经模型版本核验的两套 PyTorch 部件模型，分别生成独立评估数据的剩余寿命预测。",
    },
}

PUBLIC_HIDDEN_FIELD_KEYS = frozenset({
    "argv_tokens", "debug", "mmdd", "root", "output", "output_dir", "url", "address", "port",
})
PUBLIC_FIELD_KEYS = frozenset({
    "key", "label", "kind", "choices", "default", "required", "description",
    "default_display", "example", "allowed_values", "min", "max", "step",
    "placeholder", "max_length",
})


def _user_choice_label(name: str, value: Any, index: int) -> str:
    text = str(value)
    if name in {"split", "domain"}:
        if text == "all":
            return "全部数据"
        if name == "split" and text == "holdout":
            return "独立评估数据"
        if name == "split" and text == "val":
            return "模型选择数据"
    if text in USER_CHOICE_TEXT:
        return USER_CHOICE_TEXT[text]
    if name == "config":
        return f"受控配置方案 {index + 1}"
    if name == "manifest":
        return f"受控流程配置 {index + 1}"
    if name == "run":
        return f"已登记运行记录 {index + 1}"
    if name in {"workers", "seed", "epochs"}:
        return text
    return f"可选方案 {index + 1}" if "/" in text or "_" in text else text


def _decorate_user_field(field: dict[str, Any]) -> dict[str, Any]:
    """Expose a user-safe field while retaining the original token server-side."""
    item = _decorate_field(field)
    name = _parameter_name(item)
    guide = PARAMETER_GUIDE.get(name)
    if guide:
        item["label"] = guide[0]
    if name == "argv_tokens":
        item["label"] = "高级附加参数（通常无需填写）"
        item["example"] = "按脚本帮助逐项填写"
        item["description"] = "仅在受控表单未覆盖参数时逐项填写；每项独立传入，不会经过 Shell 解释。"
        return item
    if item.get("kind") != "select":
        return item
    raw_choices = list(item.get("choices") or [])
    labels: list[str] = []
    token_by_label: dict[str, Any] = {}
    label_by_token: dict[str, str] = {}
    for index, value in enumerate(raw_choices):
        if value == "":
            label = "未指定"
        else:
            label = _user_choice_label(name, value, index)
        base = label
        suffix = 2
        while label in token_by_label:
            label = f"{base} {suffix}"
            suffix += 1
        labels.append(label)
        token_by_label[label] = value
        label_by_token[str(value)] = label
    raw_default = item.get("default")
    item["_token_choices"] = raw_choices
    item["_choice_value_map"] = token_by_label
    item["choices"] = labels
    item["default"] = label_by_token.get(str(raw_default), "未指定" if raw_default == "" else labels[0] if labels else "")
    item["default_display"] = item["default"] or "不传入"
    item["allowed_values"] = labels
    item["example"] = next((value for value in labels if value != "未指定"), "按受控方案选择")
    return item


def _argv_tokens_field(
    *,
    label: str = "高级附加参数",
    description: str = "每行填写一个独立参数；不会经过 Shell 解释。",
) -> dict[str, Any]:
    return _decorate_field(_field(
        "argv_tokens",
        label,
        "argv_tokens",
        default=[],
        max_items=MAX_ARGV_TOKENS,
        max_length=MAX_ARGV_TOKEN_LENGTH,
        max_total_length=MAX_ARGV_TOTAL_LENGTH,
        description=description,
        required=False,
    ))


def _safe_literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    comparison = node.test
    if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
        return False
    values = [comparison.left, *comparison.comparators]
    has_name = any(isinstance(value, ast.Name) and value.id == "__name__" for value in values)
    has_main = any(isinstance(value, ast.Constant) and value.value == "__main__" for value in values)
    return has_name and has_main


def _static_names(tree: ast.AST) -> dict[str, Any]:
    """Resolve simple module constants used by argparse (for example DEVICES)."""
    names: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = _safe_literal(node.value)
            if value is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = _safe_literal(node.value)
            if value is not None:
                names[node.target.id] = value
    return names


def _static_value(node: ast.AST | None, names: dict[str, Any]) -> Any:
    """Evaluate literals plus previously found module constants."""
    value = _safe_literal(node)
    if value is not None:
        return value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_static_value(item, names) for item in node.elts]
        if all(value is not None for value in values):
            return values
    return None


def _argparse_type_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _subcommand_names(
    tree: ast.AST, names: dict[str, Any]
) -> tuple[str | None, list[str], bool]:
    """Return only safe subparser metadata without executing the target CLI.

    A source-wide AST walk cannot prove that an ``add_parser`` call belongs to
    the ``add_subparsers`` object, nor can it resolve dynamically generated
    branches. Guessing command names would let the browser compile an argv that
    looks valid while selecting the wrong grammar, so branch CLIs use the
    bounded raw-token editor and intentionally expose no guessed choices.
    """
    destination: str | None = None
    has_subparsers = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_subparsers":
                has_subparsers = True
                keywords = {item.arg: item.value for item in node.keywords if item.arg}
                value = _static_value(keywords.get("dest"), names)
                if isinstance(value, str):
                    destination = value
    return destination, [], has_subparsers


def _has_positional_arguments(tree: ast.AST, names: dict[str, Any]) -> bool:
    """Return whether the source declares an argument without an option flag.

    Positionals have implicit required/nargs semantics that a source-wide AST
    walk cannot safely reconstruct. Treat an opaque ``add_argument`` call as
    positional too; the raw token editor remains the only honest interface.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        values = [_static_value(arg, names) for arg in node.args]
        strings = [value for value in values if isinstance(value, str)]
        if not any(value.startswith("-") for value in strings):
            return True
    return False


def _has_mutually_exclusive_group(tree: ast.AST) -> bool:
    """Return whether argparse mutual-exclusion constraints are present."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_mutually_exclusive_group"
        for node in ast.walk(tree)
    )


def _argument_hints(tree: ast.AST) -> list[dict[str, Any]]:
    """Infer only scalar argparse fields whose argv semantics are unambiguous.

    Repeated, variable-length, dynamic, positional, subcommand, and otherwise
    opaque parameters deliberately use the bounded literal token editor instead
    of a form that could change argv.
    """
    names = _static_names(tree)
    sub_dest, subcommands, has_subparsers = _subcommand_names(tree, names)
    hints: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    order = 0

    # A single AST walk cannot safely associate add_argument() calls with the
    # parser branch that owns them.  Showing those fields together would make
    # unrelated required options appear on every subcommand and could compile
    # an invalid argv. The same conservative fallback applies to positionals
    # and mutually-exclusive groups, whose argparse semantics are not captured
    # by independent fields.
    if has_subparsers or _has_positional_arguments(tree, names) or _has_mutually_exclusive_group(tree):
        return hints

    for call in (node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "add_argument"):
        order += 1
        values = [_static_value(arg, names) for arg in call.args]
        strings = [value for value in values if isinstance(value, str)]
        options = [value for value in strings if value.startswith("-")]
        keywords = {item.arg: item.value for item in call.keywords if item.arg}
        action_node = keywords.get("action")
        action = _static_value(action_node, names) if action_node is not None else "store"
        if action not in {"store", "store_true", "store_false"}:
            continue
        nargs_node = keywords.get("nargs")
        # A UI scalar cannot faithfully stand for append/extend, REMAINDER,
        # or even an optional value that is valid only when its flag appears.
        # Keep all explicitly declared nargs forms in the raw token editor.
        if nargs_node is not None:
            continue
        dest = _static_value(keywords.get("dest"), names)
        if not isinstance(dest, str):
            if options:
                dest = max(options, key=lambda value: (value.startswith("--"), len(value)))
                dest = dest.lstrip("-").replace("-", "_")
            elif strings:
                dest = strings[0]
            else:
                continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", dest):
            continue
        key = f"arg_{dest}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        flag = max(options, key=lambda value: (value.startswith("--"), len(value))) if options else None
        positional = flag is None
        default_node = keywords.get("default")
        default = _static_value(default_node, names) if default_node is not None else None
        if default_node is not None and default is None and not (
            isinstance(default_node, ast.Constant) and default_node.value is None
        ):
            continue
        required = _static_value(keywords.get("required"), names) is True
        help_text = _static_value(keywords.get("help"), names)
        label = str(help_text).strip() if isinstance(help_text, str) and help_text.strip() else (flag or dest)
        choices_node = keywords.get("choices")
        choices = _static_value(choices_node, names) if choices_node is not None else None
        if choices_node is not None and choices is None:
            continue
        if isinstance(choices, (set, tuple, list)):
            choices = [value for value in choices if isinstance(value, (str, int, float)) and not isinstance(value, bool)]
        else:
            choices = []
        type_name = _argparse_type_name(keywords.get("type"))
        if action in {"store_true", "store_false"}:
            if default is None:
                default = action == "store_false"
            if not isinstance(default, bool):
                default = action == "store_false"
            kind = "boolean"
        elif choices:
            kind = "select"
            choice_values = [str(value) for value in choices]
            if default is None:
                default = ""
                # Keep a blank value in the machine schema even for required
                # choices so the catalog's compile-only default remains a
                # valid representation; the browser still marks it required.
                choice_values = ["", *choice_values]
            else:
                default = str(default)
                if default not in choice_values:
                    choice_values = [default, *choice_values]
            choices = choice_values
        elif type_name in {"int", "integer"}:
            kind = "integer"
            # None is semantically meaningful for argparse: it means the
            # optional flag is absent.  Do not coerce it to 0, otherwise a
            # browser preview silently changes the command being represented.
            if default is not None and (not isinstance(default, int) or isinstance(default, bool)):
                default = None
        elif type_name in {"float", "number"}:
            kind = "number"
            if default is not None:
                if not isinstance(default, (int, float)) or isinstance(default, bool):
                    default = None
                else:
                    default = float(default)
        else:
            kind = "text"
            if default is None:
                default = ""
            elif not isinstance(default, str):
                default = str(default)

        spec: dict[str, Any] = {
            "key": key, "flag": flag, "action": action, "default": default,
            "required": required, "position": positional, "nargs": None,
            "order": order, "source_dest": dest,
        }
        if kind == "select":
            field = _field(key, label, kind, choices=choices, default=default,
                           cli_flag=flag, required=required, omit_empty=not required)
        elif kind == "integer":
            field = _field(key, label, kind, default=default,
                           min=-2147483648, max=2147483647, cli_flag=flag,
                           required=required, omit_empty=not required)
        elif kind == "number":
            field = _field(key, label, kind, default=default,
                           min=-1e12, max=1e12, step="any", cli_flag=flag,
                           required=required, omit_empty=not required)
        else:
            field = _field(key, label, kind, default=default,
                           pattern=r"^[^\x00\r\n]*$", max_length=MAX_ARGV_TOKEN_LENGTH,
                           cli_flag=flag, required=required, omit_empty=not required)
        hints.append({"field": field, "spec": spec})
    return sorted(hints, key=lambda item: item["spec"].get("order", 0))


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding=sys.getfilesystemencoding(), errors="replace")


def _argument_schema_info(tree: ast.AST, hints: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize static argparse coverage without claiming dynamic certainty."""
    names = _static_names(tree)
    sub_dest, subcommands, has_subparsers = _subcommand_names(tree, names)
    declared: set[str] = set()
    unresolved = 0
    for call in (node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "add_argument"):
        values = [_static_value(arg, names) for arg in call.args]
        strings = [value for value in values if isinstance(value, str)]
        options = [value for value in strings if value.startswith("-")]
        keywords = {item.arg: item.value for item in call.keywords if item.arg}
        dest = _static_value(keywords.get("dest"), names)
        if not isinstance(dest, str):
            if options:
                flag = max(options, key=lambda value: (value.startswith("--"), len(value)))
                dest = flag.lstrip("-").replace("-", "_")
            elif strings:
                dest = strings[0]
        if isinstance(dest, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", dest):
            declared.add(dest)
        else:
            unresolved += 1
    structured = {item["spec"].get("source_dest") for item in hints
                  if item["spec"].get("source_dest")}
    missing = sorted(declared - structured)
    manual_argv = any(
        isinstance(node, ast.Attribute)
        and node.attr == "argv"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        for node in ast.walk(tree)
    )
    has_positionals = _has_positional_arguments(tree, names)
    has_mutually_exclusive = _has_mutually_exclusive_group(tree)
    if has_subparsers:
        # Branch-local argparse fields are intentionally represented by the
        # safe token editor; this is complete executable coverage, but not a
        # claim that one flat typed form understands every branch.
        status = "branch-token-only"
    elif has_positionals or has_mutually_exclusive:
        # Implicit positional requirements and group constraints are not
        # representable as independent browser fields.
        status = "token-only"
    elif manual_argv and not declared:
        status = "token-only"
    elif not declared and not unresolved:
        status = "no-arguments"
    elif not unresolved and not missing:
        status = "complete"
    else:
        status = "partial"
    return {
        "status": status,
        "declared": len(declared),
        "structured": len(structured),
        "unresolved": unresolved,
        "missing": missing,
        "branching": has_subparsers,
        "positionals": has_positionals,
        "mutually_exclusive": has_mutually_exclusive,
        "subcommand_dest": sub_dest,
        "subcommands": subcommands,
    }


def _python_analysis(path: Path) -> tuple[bool, list[dict[str, Any]], str, dict[str, Any]]:
    source = _read_source(path)
    try:
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError, TypeError):
        textual = bool(re.search(r"__name__\s*==\s*['\"]__main__['\"]", source))
        return textual or path.name == "__main__.py", [], "text-fallback", {
            "status": "unavailable", "declared": 0, "structured": 0,
            "unresolved": 0, "missing": [], "branching": False,
            "positionals": False, "mutually_exclusive": False,
            "subcommand_dest": None, "subcommands": [],
        }
    has_main = path.name == "__main__.py" or any(_is_main_guard(node) for node in ast.walk(tree))
    hints = _argument_hints(tree) if has_main else []
    return has_main, hints, "ast", _argument_schema_info(tree, hints) if has_main else {
        "status": "not-cli", "declared": 0, "structured": 0,
        "unresolved": 0, "missing": [], "branching": False,
        "positionals": False, "mutually_exclusive": False,
        "subcommand_dest": None, "subcommands": [],
    }


def _ignored(relative: Path) -> bool:
    lowered = {part.lower() for part in relative.parts[:-1]}
    if lowered & IGNORED_SEGMENTS:
        return True
    name = relative.name
    return name in INTERNAL_HELPERS or name.startswith(IGNORED_FILE_PREFIXES)


def _candidate_files(root: Path) -> list[Path]:
    candidates: set[Path] = set()
    suffixes = {PYTHON_SUFFIX, *NODE_SUFFIXES, *LAUNCHER_SUFFIXES, MATLAB_SUFFIX}
    for path in root.iterdir() if root.is_dir() else []:
        if path.is_file() and (path.suffix.lower() in suffixes or path.name in MAKEFILE_NAMES or path.name == PACKAGE_FILE):
            candidates.add(path)
    for folder_name in DISCOVERY_DIRECTORIES:
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if _ignored(relative):
                continue
            if path.suffix.lower() in suffixes or path.name in MAKEFILE_NAMES or path.name == PACKAGE_FILE:
                candidates.add(path)
    return sorted(candidates, key=lambda value: _posix(value.relative_to(root)))


def _python_base_argv(root: Path, path: Path) -> list[str]:
    relative = path.relative_to(root)
    parents = list(relative.parent.parts)
    is_package = bool(parents) and all((root.joinpath(*parents[:index]) / "__init__.py").is_file()
                                       for index in range(1, len(parents) + 1))
    if is_package:
        module_parts = list(relative.with_suffix("").parts)
        if module_parts[-1] == "__main__":
            module_parts.pop()
        return [sys.executable, "-m", ".".join(module_parts)]
    return [sys.executable, _posix(relative)]


def _launcher_availability(command: str) -> dict[str, Any]:
    if os.path.isabs(command) and Path(command).is_file():
        resolved = command
    else:
        resolved = shutil.which(command)
    return {
        "available": bool(resolved),
        "command": command,
        "resolved": resolved,
        "platform": sys.platform,
        "reason": None if resolved else f"当前环境未找到解释器：{command}",
    }


def _adapter_argv(launcher: str, *fixed: str) -> list[str]:
    return [sys.executable, "dashboard/_cli_exec.py", launcher, *fixed]


def _choose_powershell() -> str:
    for candidate in ("pwsh", "powershell"):
        if shutil.which(candidate):
            return candidate
    return "pwsh"


def _make_surface(
    surface_id: str,
    kind: str,
    relative: str,
    launcher: str,
    base_argv: list[str],
    *,
    argument_hints: list[dict[str, Any]] | None = None,
    argument_schema: dict[str, Any] | None = None,
    parser: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    availability = _launcher_availability(launcher)
    return {
        "surface_id": surface_id,
        "kind": kind,
        "path": relative,
        "launcher": launcher,
        "base_argv": base_argv,
        "availability": availability,
        "argument_hints": argument_hints or [],
        "argument_schema": argument_schema or {
            "status": "token-only", "declared": 0, "structured": 0,
            "unresolved": 0, "missing": [], "branching": False,
            "positionals": False, "mutually_exclusive": False,
            "subcommand_dest": None, "subcommands": [],
        },
        "parser": parser,
        "detail": detail,
    }


def _discover_make_targets(root: Path, path: Path) -> Iterable[dict[str, Any]]:
    relative = _posix(path.relative_to(root))
    launcher = shutil.which("make") and "make" or ("gmake" if shutil.which("gmake") else "make")
    target_re = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)")
    seen: set[str] = set()
    for line in _read_source(path).splitlines():
        if line[:1].isspace() or line.startswith("#"):
            continue
        match = target_re.match(line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith(".") or target in seen:
            continue
        seen.add(target)
        parent = _posix(path.parent.relative_to(root)) or "."
        fixed = ["-C", parent, "-f", path.name, target]
        yield _make_surface(
            f"make:{relative}#{target}", "make", relative, launcher,
            _adapter_argv(launcher, *fixed), detail=target,
        )


def _discover_package_scripts(root: Path, path: Path) -> Iterable[dict[str, Any]]:
    relative = _posix(path.relative_to(root))
    try:
        package = json.loads(_read_source(path))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return
    if not isinstance(package, dict):
        return
    launcher = "npm"
    prefix = _posix(path.parent.relative_to(root)) or "."
    scripts = package.get("scripts", {})
    if isinstance(scripts, dict):
        for name in sorted(value for value in scripts if isinstance(value, str) and value):
            yield _make_surface(
                f"npm:{relative}#{name}", "npm", relative, launcher,
                _adapter_argv(launcher, "--prefix", prefix, "run", name, "--"), detail=name,
            )


@lru_cache(maxsize=8)
def _discover_cached(root_text: str) -> tuple[dict[str, Any], ...]:
    root = Path(root_text)
    surfaces: list[dict[str, Any]] = []
    for path in _candidate_files(root):
        relative_path = path.relative_to(root)
        relative = _posix(relative_path)
        suffix = path.suffix.lower()
        if suffix == PYTHON_SUFFIX:
            has_main, hints, parser, argument_schema = _python_analysis(path)
            if has_main:
                surfaces.append(_make_surface(
                    f"python:{relative}", "python", relative, sys.executable,
                    _python_base_argv(root, path), argument_hints=hints,
                    argument_schema=argument_schema, parser=parser,
                ))
        elif suffix in NODE_SUFFIXES:
            source = _read_source(path)
            if re.search(r"process\.argv|#!/usr/bin/env\s+node|\bcommander\b|\byargs\b", source):
                launcher = "node"
                surfaces.append(_make_surface(
                    f"node:{relative}", "node", relative, launcher,
                    [shutil.which(launcher) or launcher, relative], parser="text",
                ))
        elif suffix == ".sh":
            launcher = "bash"
            surfaces.append(_make_surface(
                f"shell:{relative}", "shell", relative, launcher,
                _adapter_argv(launcher, relative),
            ))
        elif suffix == ".ps1":
            launcher = _choose_powershell()
            surfaces.append(_make_surface(
                f"powershell:{relative}", "powershell", relative, launcher,
                _adapter_argv(launcher, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", relative),
            ))
        elif suffix in {".bat", ".cmd"}:
            launcher = "cmd"
            surfaces.append(_make_surface(
                f"batch:{relative}", "batch", relative, launcher,
                _adapter_argv(launcher, "/d", "/c", relative),
            ))
        elif suffix == MATLAB_SUFFIX:
            launcher = "matlab"
            expression = "run('" + relative.replace("'", "''") + "')"
            surfaces.append(_make_surface(
                f"matlab:{relative}", "matlab", relative, launcher,
                _adapter_argv(launcher, "-batch", expression),
            ))
        elif path.name in MAKEFILE_NAMES:
            surfaces.extend(_discover_make_targets(root, path))
        elif path.name == PACKAGE_FILE:
            surfaces.extend(_discover_package_scripts(root, path))

    tests = root / "tests"
    if tests.is_dir():
        py = sys.executable
        availability = _launcher_availability(py)
        surfaces.extend([
            {
                "surface_id": "pytest:dashboard", "kind": "pytest", "path": "tests",
                "launcher": py, "base_argv": [py, "-m", "pytest", "-q", "tests/test_dashboard_app.py", "-p", "no:cacheprovider"],
                "availability": availability, "argument_hints": [], "argument_schema": {
                    "status": "token-only", "declared": 0, "structured": 0,
                    "unresolved": 0, "missing": [], "branching": False,
                    "positionals": False, "mutually_exclusive": False,
                    "subcommand_dest": None, "subcommands": [],
                }, "parser": "group", "detail": "dashboard",
            },
            {
                "surface_id": "pytest:full", "kind": "pytest", "path": "tests",
                "launcher": py, "base_argv": [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                "availability": availability, "argument_hints": [], "argument_schema": {
                    "status": "token-only", "declared": 0, "structured": 0,
                    "unresolved": 0, "missing": [], "branching": False,
                    "positionals": False, "mutually_exclusive": False,
                    "subcommand_dest": None, "subcommands": [],
                }, "parser": "group", "detail": "full",
            },
        ])
    unique: dict[str, dict[str, Any]] = {}
    for surface in surfaces:
        if surface["surface_id"] in unique:
            raise OperationError(f"重复的 CLI surface_id：{surface['surface_id']}")
        unique[surface["surface_id"]] = surface
    return tuple(unique[key] for key in sorted(unique))


def discover_cli_surfaces(root: Path = ROOT) -> list[dict[str, Any]]:
    """Return the deterministic machine inventory of repository CLI surfaces."""
    return [dict(item) for item in _discover_cached(str(root.resolve()))]


def refresh_discovery_cache() -> None:
    """Invalidate discovery after repository files are intentionally changed."""
    _discover_cached.cache_clear()
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def _curated_operations() -> list[dict[str, Any]]:
    py = sys.executable
    node = shutil.which("node") or "node"
    train_configs = _relative_choices("configs/t1/*.yaml")
    finetune_configs = [value for value in train_configs if "/ft" in value]
    preprocess_configs = _relative_choices("configs/preprocess/*.yaml")
    sim_configs = _relative_choices("configs/sim/batch_*.yaml")
    t2_manifests = _relative_choices("configs/t2/*.json")
    t3_configs = _relative_choices("configs/t3/*.yaml")
    distill_configs = [value for value in t3_configs if "distill" in value]
    patch_configs = [value for value in t3_configs if "patch" in value]
    safe_t2_manifests = _relative_choices("configs/t2/*safe*.json")
    common_device = ["auto", "cpu", "cuda"]
    run_field = (
        _field("run", "已有结果批次", "select", choices=_run_choices())
        if _run_choices()
        else _field(
            "run", "已有结果批次（当前未发现）", "text", default="",
            pattern=r"^[^\x00\r\n]*$", max_length=MAX_ARGV_TOKEN_LENGTH,
            placeholder="结果产物就绪后选择批次",
        )
    )
    operations = [
        {
            "id": "w3_status", "category": "验收", "label": "项目交付状态扫描",
            "description": "读取机器配置与真实产物，输出当前交付状态；不读取 Markdown 判定。",
            "risk": "read", "resource": "cpu", "fields": [],
            "argv": [py, "scripts/w3_w5_tasks.py", "status", "--json"],
        },
        {
            "id": "w3_accept", "category": "验收", "label": "项目机器验收",
            "description": "按机器配置和真实产物执行完整验收，不读取 Markdown 判定。",
            "risk": "write", "resource": "cpu", "fields": [],
            "argv": [py, "scripts/w3_w5_tasks.py", "accept", "--json"],
        },
        {
            "id": "dashboard_tests", "category": "验收", "label": "驾驶舱专项测试",
            "description": "运行接口、事实链、离线与响应式机器门禁。",
            "risk": "read", "resource": "cpu", "fields": [],
            "argv": [py, "-m", "pytest", "-q", "tests/test_dashboard_app.py", "-p", "no:cacheprovider"],
        },
        {
            "id": "full_tests", "category": "验收", "label": "全仓测试",
            "description": "运行完整 pytest；耗时较长并保留全部失败。",
            "risk": "heavy", "resource": "cpu", "fields": [],
            "argv": [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        },
        {
            "id": "reproduce_results", "category": "复现", "label": "结果复现核对",
            "description": "执行 reproduce_all results 模式，对现有结果做确定性核对。",
            "risk": "write", "resource": "cpu", "fields": [
                _field("seed", "随机种子", "integer", default=0, min=0, max=999999),
            ], "argv_builder": "reproduce_results",
        },
        {
            "id": "reproduce_pipeline", "category": "复现", "label": "完整流水线复现",
            "description": "从流水线入口重跑主要步骤；属于长时写入任务。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("seed", "随机种子", "integer", default=0, min=0, max=999999),
            ], "argv_builder": "reproduce_pipeline",
        },
        {
            "id": "preprocess", "category": "数据", "label": "统一预处理",
            "description": "从现有 interim 输入生成受控 processed 产物。",
            "risk": "write", "resource": "cpu", "fields": [
                _field("config", "预处理配置", "select", choices=preprocess_configs,
                       default=preprocess_configs[0] if preprocess_configs else None),
                _field("workers", "并行度", "select", choices=["auto", "0", "1", "2", "4", "8"], default="auto"),
            ], "argv_builder": "preprocess",
        },
        {
            "id": "sim_plan", "category": "仿真", "label": "仿真批次计划",
            "description": "解析批次卡并生成或核对计划；默认 dry-run。",
            "risk": "write", "resource": "cpu", "fields": [
                _field("config", "批次配置", "select", choices=sim_configs,
                       default=sim_configs[0] if sim_configs else None),
                _field("workers", "并行度", "select", choices=["auto", "0", "1", "2", "4", "8"], default="auto"),
                _field("dry_run", "仅检查，不落盘", "boolean", default=True),
            ], "argv_builder": "sim_plan",
        },
        {
            "id": "sim_status", "category": "仿真", "label": "仿真批次状态",
            "description": "读取批次日志和样本状态，不启动 MATLAB。",
            "risk": "read", "resource": "cpu", "fields": [
                _field("config", "批次配置", "select", choices=sim_configs,
                       default=sim_configs[0] if sim_configs else None),
            ], "argv_builder": "sim_status",
        },
        {
            "id": "brphm_verify", "category": "模型",
            "label": "核对默认预测模型",
            "description": "核对默认预测模型的文件身份与历史评估指标。",
            "risk": "read", "resource": "cpu", "fields": [],
            "argv_builder": "brphm_verify",
        },
        {
            "id": "brphm_reproduce", "category": "模型",
            "label": "完整复现预测流程",
            "description": "依次执行基础训练、航天数据调整和验证预测。",
            "risk": "heavy", "resource": "gpu",
            "fields": [
                _field(
                    "device", "计算设备", "select", choices=["auto", "cpu", "cuda"], default="auto",
                    description="选择 PyTorch 复现流程使用的计算设备；默认自动选择可用加速设备。",
                ),
            ],
            "argv_builder": "brphm_reproduce",
        },
        {
            "id": "brphm_reconstruct", "category": "数据",
            "label": "重建航天仿真数据",
            "description": "把已下载的原始航天仿真记录重建为中间表格，并可在写后回读核对。",
            "risk": "write", "resource": "cpu",
            "fields": [
                _field("dataset_root", "已下载数据目录", "text", required=True, max_length=MAX_ARGV_TOKEN_LENGTH, placeholder="/path/to/BRPHM_RUL_standard"),
                _field("component", "部件", "select", choices=["battery", "reaction-wheel", "both"], default="both"),
                _field("workers", "并行任务数", "select", choices=["1", "2", "4"], default="1"),
                _field("verify", "写后回读核对", "boolean", default=True, description="完成重建后读取生成的 Parquet 并核对写入结果。"),
                _field("files", "指定原始记录", "text", default="", max_length=MAX_ARGV_TOTAL_LENGTH, placeholder="/absolute/path/record.mat; /absolute/path/another.mat"),
            ],
            "argv_builder": "brphm_reconstruct",
        },
        {
            "id": "competition_s22_s21_train", "category": "模型",
            "label": "电池部件（储能系统）与反作用轮部件（姿态控制执行器）模型训练（PyTorch）",
            "description": "使用 PyTorch 分别训练电池部件剩余寿命预测模型（储能系统）与反作用轮部件剩余寿命预测模型（姿态控制执行器）的基础模型。",
            "risk": "heavy", "resource": "gpu",
            "competition_line": COMPETITION_LINE_CONTRACT,
            "fields": [
                _field(
                    "device", "计算设备", "select", choices=["auto", "cpu", "cuda"], default="auto",
                    description="选择 PyTorch 训练使用的计算设备；默认自动选择可用加速设备。",
                ),
            ],
            "argv_builder": "competition_s22_s21_train",
        },
        {
            "id": "competition_s22_s21_transfer", "category": "模型",
            "label": "电池部件（储能系统）与反作用轮部件（姿态控制执行器）目标域迁移适配（PyTorch）",
            "description": "在 PyTorch 中分别将电池部件（储能系统）与反作用轮部件（姿态控制执行器）的基础模型适配到航天目标数据，并按各自部件的输入语义重新拟合。",
            "risk": "heavy", "resource": "gpu",
            "competition_line": COMPETITION_LINE_CONTRACT,
            "fields": [
                _field(
                    "device", "计算设备", "select", choices=["auto", "cpu", "cuda"], default="auto",
                    description="选择 PyTorch 航天数据调整使用的计算设备；默认自动选择可用加速设备。",
                ),
            ],
            "argv_builder": "competition_s22_s21_transfer",
        },
        {
            "id": "competition_s22_s21_predict", "category": "模型",
            "label": "电池部件（储能系统）与反作用轮部件（姿态控制执行器）剩余寿命预测（PyTorch）",
            "description": "加载经清单核验的两套 PyTorch 部件模型，分别生成电池部件（储能系统）与反作用轮部件（姿态控制执行器）的独立留出集剩余寿命预测。",
            "risk": "heavy", "resource": "gpu",
            "competition_line": COMPETITION_LINE_CONTRACT,
            "fields": [
                _field(
                    "device", "计算设备", "select", choices=["auto", "cpu", "cuda"], default="auto",
                    description="选择 PyTorch 推理使用的计算设备；默认自动选择可用加速设备。",
                ),
            ],
            "argv_builder": "competition_s22_s21_predict",
        },
        {
            "id": "train", "category": "模型", "label": "公开退化数据预训练",
            "description": "使用受控配置在公开退化数据上训练可迁移基础模型。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("config", "训练配置", "select", choices=train_configs,
                       default=train_configs[0] if train_configs else None),
                _field("seed", "随机种子", "integer", default=0, min=0, max=999999),
                _field("epochs", "训练轮数", "integer", default=20, min=1, max=500),
                _field("device", "设备", "select", choices=common_device + ["mps"], default="auto"),
                _field("mmdd", "结果批次标识", "text", default="0727", pattern="^\\d{4}$"),
            ], "argv_builder": "train",
        },
        {
            "id": "finetune", "category": "模型", "label": "航天仿真数据微调",
            "description": "使用受控配置在航天仿真数据上执行冻结层或全量微调。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("config", "微调配置", "select", choices=finetune_configs,
                       default=finetune_configs[0] if finetune_configs else None),
                _field("mode", "微调模式", "select", choices=["freeze", "full"], default="full"),
                _field("seed", "随机种子", "integer", default=0, min=0, max=999999),
                _field("epochs", "训练轮数", "integer", default=20, min=1, max=500),
                _field("device", "设备", "select", choices=common_device, default="auto"),
                _field("mmdd", "结果批次标识", "text", default="0727", pattern="^\\d{4}$"),
            ], "argv_builder": "finetune",
        },
        {
            "id": "t2a_matrix", "category": "模型", "label": "迁移适配实验矩阵",
            "description": "按受控任务清单运行跨领域迁移适配实验矩阵。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("manifest", "矩阵 Manifest", "select", choices=t2_manifests,
                       default=t2_manifests[0] if t2_manifests else None),
                _field("device", "设备", "select", choices=common_device + ["mps"], default="auto"),
            ], "argv_builder": "t2a_matrix",
        },
        {
            "id": "t2c", "category": "模型", "label": "跨域时序集成与区间预测",
            "description": "运行预测区间校准、独立评估推理或完整流程。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("manifest", "集成推理方案", "select", choices=t2_manifests,
                       default=next((v for v in t2_manifests if "ensemble" in v), t2_manifests[0] if t2_manifests else None)),
                _field("phase", "阶段", "select", choices=["calibrate", "holdout", "all"], default="all"),
                _field("device", "设备", "select", choices=common_device, default="auto"),
            ], "argv_builder": "t2c",
        },
        {
            "id": "t3a", "category": "模型", "label": "物理约束时序实验",
            "description": "运行物理约束时序方案，并如实保留本次运行的完整结果与对照记录。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("config", "物理约束方案", "select", choices=patch_configs,
                       default=patch_configs[0] if patch_configs else None),
                _field("device", "设备", "select", choices=common_device, default="auto"),
                _field("mmdd", "结果批次标识", "text", default="0727", pattern="^\\d{4}$"),
                _field("debug", "内部调试子集", "boolean", default=False, user_visible=False),
            ], "argv_builder": "t3a",
        },
        {
            "id": "t3c", "category": "模型", "label": "轻量模型蒸馏部署",
            "description": "将冻结教师模型蒸馏为轻量时序模型并生成部署产物。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("config", "蒸馏配置", "select", choices=distill_configs,
                       default=distill_configs[0] if distill_configs else None),
                _field("device", "设备", "select", choices=common_device + ["mps"], default="auto"),
                _field("epochs", "训练轮数", "integer", default=20, min=1, max=500),
            ], "argv_builder": "t3c",
        },
        {
            "id": "evaluate_run", "category": "评测", "label": "单次运行结果评估",
            "description": "评估既有完整运行产物，并保留真实指标和图表。",
            "risk": "write", "resource": "cpu", "fields": [
                run_field,
                _field("split", "数据划分", "select", choices=["val", "holdout", "all"], default="all"),
                _field("domain", "域", "select", choices=["src", "tgt", "all"], default="all"),
                _field("figures", "生成图表", "boolean", default=True),
            ], "argv_builder": "evaluate_run",
        },
        {
            "id": "gate2", "category": "评测", "label": "迁移效果验收收据",
            "description": "从既有基线、迁移和校准产物重建机器可读的效果验收收据。",
            "risk": "write", "resource": "cpu", "fields": [],
            "argv": [py, "scripts/build_gate2_receipt.py"],
        },
        {
            "id": "t2c_safe_holdout", "category": "证据", "label": "安全留出集推理",
            "description": "使用冻结模型生成独立 raw/safe/clamp 字段，不覆盖既有留出集产物。",
            "risk": "heavy", "resource": "gpu", "fields": [
                _field("manifest", "安全 Manifest", "select", choices=safe_t2_manifests,
                       default=safe_t2_manifests[0] if safe_t2_manifests else None),
                _field("device", "设备", "select", choices=common_device, default="auto"),
            ], "argv_builder": "t2c_safe_holdout",
        },
        {
            "id": "t2c_safety_audit", "category": "证据", "label": "预测输出安全审计",
            "description": "校验冻结 rmax、raw 越界、safe 封顶、clamp 标志和区间顺序。",
            "risk": "write", "resource": "cpu", "fields": [],
            "argv": [py, "scripts/audit_t2c_output_safety.py"],
        },
        {
            "id": "frontend_figures", "category": "证据", "label": "生成中文图件",
            "description": "从真实安全 holdout 生成 RUL 区间、模型对照和 raw/safe 审计图。",
            "risk": "write", "resource": "cpu", "fields": [],
            "argv": [py, "scripts/build_frontend_figures.py"],
        },
        {
            "id": "frontend_encoding", "category": "验收", "label": "中文编码与离线审计",
            "description": "检查 UTF-8、疑似乱码标记和 HTML/CSS/JS 外部网络引用。",
            "risk": "write", "resource": "cpu", "fields": [],
            "argv": [py, "scripts/audit_frontend_encoding.py"],
        },
        {
            "id": "frontend_browser", "category": "验收", "label": "浏览器兼容性审计",
            "description": "运行 HTTP、Chromium、Firefox 与可用 WebKit 的桌面/移动收据。",
            "risk": "write", "resource": "browser", "fields": [],
            "argv": [py, "scripts/audit_frontend_browser.py"],
        },
        {
            "id": "frontend_interaction", "category": "验收", "label": "前端交互回归",
            "description": "验证响应式视图、轨道控件、搜索和受控作业目录。",
            "risk": "write", "resource": "browser", "fields": [],
            "argv": [node, "scripts/frontend_interaction_probe.js"],
        },
    ]
    user_operation_ids = {
        "brphm_verify", "brphm_reproduce", "brphm_reconstruct", "competition_s22_s21_train",
        "competition_s22_s21_transfer", "competition_s22_s21_predict",
    }
    for operation in operations:
        operation["workflow_group"] = operation["category"]
        operation["audience"] = "user" if operation["id"] in user_operation_ids else "developer"
        if operation["audience"] == "user":
            metadata = PUBLIC_OPERATION_METADATA.get(operation["id"])
            if metadata is None:
                raise OperationError(f"用户流程缺少公开业务映射：{operation['id']}")
            # These are also used by public job receipts after a submission.
            # The immutable command ID remains operation["id"].
            operation["label"] = metadata["label"]
            operation["description"] = metadata["description"]
        operation["purpose_zh"] = operation["description"]
        operation["fields"] = [
            _decorate_user_field(field) for field in operation.get("fields", [])
        ]
        if operation["audience"] == "developer":
            operation["fields"].append(_decorate_field(_argv_tokens_field()))
        operation["curated"] = True
    missing_metadata = user_operation_ids - set(PUBLIC_OPERATION_METADATA)
    stale_metadata = set(PUBLIC_OPERATION_METADATA) - user_operation_ids
    if missing_metadata or stale_metadata:
        raise OperationError(
            "公开业务映射与用户流程不一致："
            f"missing={sorted(missing_metadata)}, stale={sorted(stale_metadata)}"
        )
    return operations


CURATED_SURFACES = {
    "w3_status": "python:scripts/w3_w5_tasks.py",
    "w3_accept": "python:scripts/w3_w5_tasks.py",
    "dashboard_tests": "pytest:dashboard",
    "full_tests": "pytest:full",
    "reproduce_results": "python:scripts/reproduce_all.py",
    "reproduce_pipeline": "python:scripts/reproduce_all.py",
    "preprocess": "python:src/datasets/preprocess.py",
    "sim_plan": "python:scripts/sim_batch.py",
    "sim_status": "python:scripts/sim_batch.py",
    "brphm_verify": "python:brphm.py",
    "brphm_reproduce": "python:brphm.py",
    "brphm_reconstruct": "python:brphm.py",
    "competition_s22_s21_train": "python:brphm.py",
    "competition_s22_s21_transfer": "python:brphm.py",
    "competition_s22_s21_predict": "python:brphm.py",
    "train": "python:src/train.py",
    "finetune": "python:src/finetune.py",
    "t2a_matrix": "python:scripts/run_t2a_matrix.py",
    "t2c": "python:scripts/run_t2c.py",
    "t2c_safe_holdout": "python:scripts/run_t2c.py",
    "t3a": "python:scripts/run_t3a_formal.py",
    "t3c": "python:scripts/run_t3c_distill.py",
    "evaluate_run": "python:src/evaluate.py",
    "gate2": "python:scripts/build_gate2_receipt.py",
    "t2c_safety_audit": "python:scripts/audit_t2c_output_safety.py",
    "frontend_figures": "python:scripts/build_frontend_figures.py",
    "frontend_encoding": "python:scripts/audit_frontend_encoding.py",
    "frontend_browser": "python:scripts/audit_frontend_browser.py",
    "frontend_interaction": "node:scripts/frontend_interaction_probe.js",
}


def _surface_category(surface: dict[str, Any]) -> str:
    text = f"{surface['path']} {surface.get('detail') or ''}".lower()
    if surface["kind"] == "matlab" or "sim" in text or "gmat" in text:
        return "仿真"
    if "frontend" in text or "dashboard" in text or "cockpit" in text:
        return "驾驶舱"
    if any(word in text for word in ("train", "finetune", "pretrain", "distill", "transfer")):
        return "训练与迁移"
    if any(word in text for word in ("evaluate", "eval_", "predict", "inference", "forecast")):
        return "评估与推理"
    if any(word in text for word in ("audit", "check", "verify", "validate", "review", "inspect", "receipt")):
        return "审计与验收"
    if any(word in text for word in ("plot", "render", "figure", "build", "generate", "export", "package")):
        return "产物与图表"
    if any(word in text for word in ("fetch", "download", "clone", "vendor", "data", "dataset")):
        return "数据与依赖"
    return "流程与工具"


def _surface_risk(surface: dict[str, Any]) -> str:
    text = f"{surface['path']} {surface.get('detail') or ''}".lower()
    if any(word in text for word in ("production", "deploy", "publish", "switch_", "switch-")):
        return "production"
    if any(word in text for word in (
        "train", "pretrain", "finetune", "distill", "benchmark", "matrix", "mass_",
        "remediation", "reproduce", "batch", "pipeline", "multiseed", "shard_",
    )):
        return "heavy"
    if any(Path(surface["path"]).stem.lower().startswith(prefix) for prefix in (
        "audit_", "check_", "verify_", "validate_", "inspect_", "review_",
        "print_", "analyze_", "diagnose_", "compare_", "summarize_", "inventory_",
    )):
        return "read"
    if surface["kind"] == "pytest":
        return "heavy" if surface.get("detail") == "full" else "read"
    return "write"


def _surface_resource(surface: dict[str, Any]) -> str:
    text = f"{surface['path']} {surface.get('detail') or ''}".lower()
    if surface["kind"] == "matlab":
        return "matlab"
    if surface["kind"] == "node" or "browser" in text or "webkit" in text or "frontend" in text:
        return "browser"
    if any(word in text for word in ("fetch", "download", "clone", "github", "lfs")):
        return "network"
    if any(word in text for word in (
        "gpu", "cuda", "train", "finetune", "distill", "t2a", "t2b", "t2c", "t3a", "t3c",
        "remediation", "inference", "backbone_matrix",
    )):
        return "gpu"
    return "cpu"


def _surface_purpose_zh(surface: dict[str, Any]) -> str:
    """Describe a discovered engineering entry in Chinese without inventing behavior."""
    stem = Path(surface["path"]).stem.lower()
    actions = (
        (("audit", "check", "verify", "validate", "review"), "核验"),
        (("inspect", "diagnose", "analyze", "compare"), "检查分析"),
        (("build", "generate", "gen_", "make_"), "生成产物"),
        (("plot", "render", "figure"), "生成图表"),
        (("train", "finetune", "distill", "pretrain"), "训练或适配模型"),
        (("evaluate", "predict", "forecast", "inference"), "评估或推理"),
        (("fetch", "download", "clone", "vendor"), "获取数据或依赖"),
        (("merge", "consolidate", "aggregate", "summarize"), "汇总结果"),
        (("export", "package", "finalize"), "导出交付产物"),
        (("run", "launch", "pipeline", "batch"), "执行工程流程"),
    )
    action = next((label for words, label in actions if any(word in stem for word in words)), "运行项目工具")
    group = _surface_category(surface)
    detail = f"（{surface['detail']}）" if surface.get("detail") else ""
    return f"{action}，归属“{group}”工程工具{detail}；具体行为以登记源码和预检 argv 为准。"


def _surface_label(surface: dict[str, Any]) -> str:
    kind_names = {
        "python": "Python", "node": "Node", "shell": "Shell", "powershell": "PowerShell",
        "batch": "批处理", "matlab": "MATLAB", "make": "Make", "npm": "npm", "pytest": "pytest",
    }
    if surface["kind"] in {"make", "npm", "pytest"} and surface.get("detail"):
        name = str(surface["detail"])
    else:
        name = Path(surface["path"]).stem
    readable = re.sub(r"[_-]+", " ", name).strip()
    return f"{kind_names.get(surface['kind'], surface['kind'])} · {readable}"


def _generic_operation(surface: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(surface["surface_id"].encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "_", surface["surface_id"].lower()).strip("_")[-54:]
    fields = [_decorate_field(item["field"]) for item in surface.get("argument_hints", [])]
    schema = surface.get("argument_schema") or {}
    branch_tokens = schema.get("status") == "branch-token-only"
    token_only = schema.get("status") == "token-only"
    fields.append(_argv_tokens_field(
        label="分支命令参数" if branch_tokens else "附加命令行参数",
        description=(
            "该 CLI 含子命令分支；前端不猜测分支字段，请按目标 CLI 顺序逐项填写完整 argv；"
            "不会经过 Shell 解释。"
            if branch_tokens else
            "该 CLI 含位置参数或互斥约束；前端不猜测其结构，请按目标 CLI 顺序逐项填写完整 argv；"
            "不会经过 Shell 解释。"
            if token_only else "每行一个原样参数；不会经过 Shell 解释。"
        ),
    ))
    return {
        "id": f"cli_{slug}_{digest}",
        "category": "开发工具",
        "tool_group": _surface_category(surface),
        "audience": "developer",
        "label": _surface_label(surface),
        "purpose_zh": _surface_purpose_zh(surface),
        "description": (
            f"全仓机器发现入口：{surface['path']}。"
            + (
                "该 CLI 含子命令分支；前端不猜测分支字段，完整参数使用受限 argv token，"
                "避免把不同分支的语义混为一张表单。"
                if branch_tokens else
                "该 CLI 的位置参数或互斥约束无法安全静态合并；完整参数使用受限 argv token。"
                if token_only else
                "已解析的参数均以表单字段呈现；仅动态构造且无法从源码可靠确定的参数"
                "保留为受限 argv token。"
            )
        ),
        "risk": _surface_risk(surface),
        "resource": _surface_resource(surface),
        "fields": fields,
        "argv": list(surface["base_argv"]),
        "_argument_specs": [item["spec"] for item in surface.get("argument_hints", [])],
        "curated": False,
    }


def _attach_surface_metadata(operation: dict[str, Any], surface: dict[str, Any]) -> None:
    operation.update({
        "surface_kind": surface["kind"],
        "source_path": surface["path"],
        "interpreter": {
            "kind": surface["kind"],
            "command": surface["launcher"],
            "resolved": surface["availability"]["resolved"],
        },
        "availability": dict(surface["availability"]),
        "discovery_parser": surface.get("parser"),
        "argument_schema": dict(surface.get("argument_schema") or {}),
    })


def _runtime_availability(operation: dict[str, Any]) -> dict[str, Any]:
    """Recheck the registered interpreter and source path at request time."""
    recorded = dict(operation.get("availability", {}))
    interpreter = operation.get("interpreter", {})
    command = interpreter.get("command") if isinstance(interpreter, dict) else None
    command = command or recorded.get("command")
    source_path = operation.get("source_path")

    if command:
        current = _launcher_availability(str(command))
    else:
        current = {
            "available": False,
            "command": None,
            "resolved": None,
            "platform": sys.platform,
            "reason": recorded.get("reason") or "操作未登记解释器",
        }

    source_exists = bool(source_path and (ROOT / str(source_path)).exists())
    reason = current.get("reason")
    if not source_exists:
        reason = recorded.get("reason") or f"仓库中未发现入口：{source_path or operation.get('entry_surface_id')}"
    empty_select = next(
        (
            field for field in operation.get("fields", [])
            if field.get("kind") == "select" and not list(field.get("choices") or [])
        ),
        None,
    )
    if empty_select is not None:
        field_name = str(empty_select.get("label") or empty_select.get("key") or "下拉字段")
        reason = f"{field_name}当前没有可用选项"
    available = current.get("available") is True and source_exists and empty_select is None
    return {
        **current,
        "available": available,
        "reason": None if available else reason,
        "source_path": source_path,
        "source_exists": source_exists,
    }


_CATALOG_CACHE: tuple[dict[str, Any], ...] | None = None


def _build_operation_catalog() -> list[dict[str, Any]]:
    surfaces = discover_cli_surfaces()
    by_id = {surface["surface_id"]: surface for surface in surfaces}
    operations = _curated_operations()

    # Curated cards are shortcuts, not the only representation of a surface.
    # A full-schema machine-discovered card is always the coverage owner.  This
    # prevents a fixed shortcut (for example `status`) from hiding sibling
    # subcommands or source-defined switches from the browser.
    for operation in operations:
        wanted = CURATED_SURFACES.get(operation["id"])
        if not wanted or wanted not in by_id:
            operation["entry_surface_id"] = wanted
            operation["availability"] = {
                "available": False, "command": None, "resolved": None,
                "platform": sys.platform, "reason": f"仓库中未发现入口：{wanted}",
            }
            operation["interpreter"] = {"kind": "missing", "command": None, "resolved": None}
            continue
        surface = by_id[wanted]
        _attach_surface_metadata(operation, surface)
        operation["entry_surface_id"] = wanted

    owners: dict[str, str] = {}
    for surface in surfaces:
        operation = _generic_operation(surface)
        _attach_surface_metadata(operation, surface)
        operation["surface_id"] = surface["surface_id"]
        operation["entry_surface_id"] = surface["surface_id"]
        operation["coverage_owner"] = True
        owners[surface["surface_id"]] = operation["id"]
        operations.append(operation)

    for operation in operations:
        if operation.get("coverage_owner"):
            continue
        surface_id = operation.get("entry_surface_id")
        if surface_id in owners:
            operation["surface_id"] = surface_id
            operation["variant_of"] = owners[surface_id]
            operation["coverage_owner"] = False

    ids = [operation["id"] for operation in operations]
    if len(ids) != len(set(ids)):
        raise OperationError("操作目录包含重复 id")
    claimed = [operation["surface_id"] for operation in operations if operation.get("coverage_owner")]
    discovered = [surface["surface_id"] for surface in surfaces]
    if Counter(claimed) != Counter(discovered):
        raise OperationError("操作目录与机器发现 CLI 表面不一致")
    return operations


def operation_catalog() -> list[dict[str, Any]]:
    """Return the deterministic operation catalog, cached per process."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        _CATALOG_CACHE = tuple(_build_operation_catalog())
    return [dict(item) for item in _CATALOG_CACHE]


def _catalog_map() -> dict[str, dict[str, Any]]:
    return {operation["id"]: operation for operation in operation_catalog()}


def _public_metadata(operation: dict[str, Any]) -> dict[str, str]:
    """Return the stable public business identity for a user workflow."""
    if operation.get("audience") != "user":
        raise OperationError("非用户流程没有公开业务标识")
    metadata = PUBLIC_OPERATION_METADATA.get(str(operation.get("id", "")))
    if metadata is None:
        raise OperationError("用户流程缺少公开业务映射")
    return metadata


def _resolve_operation_id(operation_id: str) -> str:
    """Resolve a public business ID to the immutable internal command ID.

    Direct internal IDs remain accepted by this Python module for the machine
    registry, legacy automation, and unit tests.  They are never emitted by
    ``operation_payload`` and therefore cannot be learned from the cockpit.
    """
    requested = str(operation_id or "")
    catalog = _catalog_map()
    if requested in catalog:
        return requested
    public_to_internal: dict[str, str] = {}
    for internal_id, metadata in PUBLIC_OPERATION_METADATA.items():
        for public_name in (metadata["id"], metadata["label"]):
            previous = public_to_internal.setdefault(public_name, internal_id)
            if previous != internal_id:
                raise OperationError("公开业务标识或名称重复")
    resolved = public_to_internal.get(requested)
    if resolved is None or resolved not in catalog:
        raise OperationError("未知操作")
    return resolved


def public_catalog() -> list[dict[str, Any]]:
    result = []
    private = {"argv", "argv_builder", "_argument_specs"}
    for operation in operation_catalog():
        item = {key: value for key, value in operation.items() if key not in private}
        item["fields"] = [
            {key: value for key, value in field.items() if not key.startswith("_")}
            for field in operation.get("fields", [])
        ]
        item["availability"] = _runtime_availability(operation)
        technical_entrypoint = operation.get("entry_surface_id")
        item["technical_entrypoint"] = technical_entrypoint
        item["entrypoint"] = (
            f"受控用户流程 · {operation.get('workflow_group', '工程')}"
            if operation.get("audience") == "user" else technical_entrypoint
        )
        item["source"] = "curated" if operation.get("curated") else "discovered"
        item["requires_confirmation"] = operation["risk"] in CONFIRMED_RISKS
        item["working_directory"] = "."
        result.append(item)
    return result


def catalog_inventory(catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return the complete discovered-surface ledger and parity counts."""
    items = catalog if catalog is not None else public_catalog()
    owners = [item for item in items if item.get("coverage_owner")]
    owner_by_surface = {item.get("surface_id"): item for item in owners}
    discovered = discover_cli_surfaces()
    discovered_ids = {surface["surface_id"] for surface in discovered}
    claimed_ids = [item.get("surface_id") for item in owners]
    duplicate_claims = sum(count - 1 for count in Counter(claimed_ids).values() if count > 1)
    entries = []
    for surface in discovered:
        owner = owner_by_surface.get(surface["surface_id"])
        if owner is None:
            availability = _launcher_availability(surface["launcher"])
            source_exists = (ROOT / surface["path"]).exists()
            availability.update({
                "available": availability["available"] is True and source_exists,
                "source_path": surface["path"],
                "source_exists": source_exists,
            })
            if not source_exists:
                availability["reason"] = f"仓库中未发现入口：{surface['path']}"
        else:
            availability = _runtime_availability(owner)
        entries.append({
            "surface_id": surface["surface_id"],
            "operation_id": owner.get("id") if owner else None,
            "label": owner.get("label") if owner else None,
            "kind": surface["kind"],
            "path": surface["path"],
            "risk": owner.get("risk") if owner else None,
            "resource": owner.get("resource") if owner else None,
            "mapped": owner is not None,
            "available": availability.get("available") is True,
            "status": "available" if availability.get("available") is True else "unavailable",
            "argument_schema": dict(owner.get("argument_schema") or surface.get("argument_schema") or {}),
            "availability": availability,
        })
    unmapped_ids = sorted(discovered_ids - set(claimed_ids))
    orphan_claims = sorted(set(claimed_ids) - discovered_ids)
    available_count = sum(entry["available"] for entry in entries)
    coverage_complete = not unmapped_ids and not orphan_claims and duplicate_claims == 0
    return {
        "operations": len(items),
        "discovered_surfaces": len(discovered),
        "curated_operations": sum(item.get("curated") is True for item in items),
        "user_operation_count": sum(item.get("audience") == "user" for item in items),
        "developer_operation_count": sum(item.get("audience") == "developer" for item in items),
        "curated_variants": sum(bool(item.get("variant_of")) for item in items),
        "available_surfaces": available_count,
        "unavailable_surfaces": len(entries) - available_count,
        "by_kind": dict(sorted(Counter(entry["kind"] for entry in entries).items())),
        "by_risk": dict(sorted(Counter(item["risk"] for item in items).items())),
        "by_resource": dict(sorted(Counter(item["resource"] for item in items).items())),
        "typed_schema_complete_count": sum(
            entry.get("argument_schema", {}).get("status") in {
                "complete", "no-arguments", "token-only",
            }
            for entry in entries
        ),
        "branch_token_only_count": sum(
            entry.get("argument_schema", {}).get("status") == "branch-token-only"
            for entry in entries
        ),
        "typed_schema_partial_count": sum(
            entry.get("argument_schema", {}).get("status") == "partial" for entry in entries
        ),
        # Stable API names consumed by the cockpit and machine receipts.
        "discovered_count": len(discovered),
        "registered_count": len(owners),
        "unmapped_count": len(unmapped_ids),
        "duplicate_mapping_count": duplicate_claims,
        "orphan_mapping_count": len(orphan_claims),
        "coverage_complete": coverage_complete,
        "status": "complete" if coverage_complete else "incomplete",
        "unmapped_surface_ids": unmapped_ids,
        "orphan_surface_ids": orphan_claims,
        "entries": entries,
    }


def operation_payload() -> dict[str, Any]:
    """Build the public user-task payload for the cockpit control surface."""
    catalog = public_catalog()
    hidden_user_metadata = {
        "technical_entrypoint", "source_path", "surface_id", "entry_surface_id",
        "variant_of", "coverage_owner", "interpreter", "discovery_parser",
        "argument_schema", "entrypoint", "source", "working_directory",
        "workflow_group", "surface_kind", "curated",
    }
    user_operations = []
    for item in catalog:
        if item.get("audience") != "user":
            continue
        metadata = _public_metadata(item)
        public_item = {key: value for key, value in item.items() if key not in hidden_user_metadata}
        public_item["id"] = metadata["id"]
        public_item["label"] = metadata["label"]
        public_item["description"] = metadata["description"]
        public_item["purpose_zh"] = metadata["description"]
        if "competition_line" in public_item:
            # The browser needs the public component contract, not the
            # immutable development route IDs used by the command compiler.
            public_item["competition_line"] = {
                "framework": "PyTorch",
                "components": {
                    "battery": "电池部件剩余寿命预测模型（储能系统）",
                    "reaction_wheel": "反作用轮部件剩余寿命预测模型（姿态控制执行器）",
                },
                "production_default": True,
                "route_overrides_allowed": False,
            }
        # Runtime command compilers retain raw choice maps and conservative
        # switches.  Only the typed business form is sent to the browser.
        public_item["fields"] = [
            {key: value for key, value in field.items() if key in PUBLIC_FIELD_KEYS}
            for field in item.get("fields", [])
            if field.get("user_visible", True) is not False
            and field.get("key") not in PUBLIC_HIDDEN_FIELD_KEYS
            and not str(field.get("key", "")).startswith("arg_")
        ]
        availability = item.get("availability")
        public_item["availability"] = {
            "available": isinstance(availability, dict) and availability.get("available") is True,
        }
        user_operations.append(public_item)
    ids = [item["id"] for item in user_operations]
    if len(ids) != len(set(ids)):
        raise OperationError("公开业务标识重复")
    return {
        # Keep the legacy key user-only while the cockpit migrates to the
        # explicit name.  The complete machine inventory remains an internal
        # test/audit function and is never emitted by this front-end endpoint.
        "operations": user_operations,
        "user_operations": user_operations,
        "default_audience": "user",
    }


def _validate_argv_tokens(value: Any, key: str) -> list[str]:
    if not isinstance(value, list):
        raise OperationError(f"{key} 必须是 JSON 数组")
    if len(value) > MAX_ARGV_TOKENS:
        raise OperationError(f"{key} 最多允许 {MAX_ARGV_TOKENS} 项")
    total = 0
    result: list[str] = []
    for index, token in enumerate(value):
        if not isinstance(token, str):
            raise OperationError(f"{key}[{index}] 必须是字符串")
        if len(token) > MAX_ARGV_TOKEN_LENGTH:
            raise OperationError(f"{key}[{index}] 超过 {MAX_ARGV_TOKEN_LENGTH} 字符")
        if "\x00" in token or "\r" in token or "\n" in token:
            raise OperationError(f"{key}[{index}] 含 NUL 或换行")
        total += len(token)
        if total > MAX_ARGV_TOTAL_LENGTH:
            raise OperationError(f"{key} 总长度超过 {MAX_ARGV_TOTAL_LENGTH} 字符")
        result.append(token)
    return result


def _validated_params(
    operation: dict[str, Any],
    supplied: dict[str, Any],
    *,
    enforce_required: bool = False,
) -> dict[str, Any]:
    if not isinstance(supplied, dict):
        raise OperationError("params 必须是对象")
    fields = {field["key"]: field for field in operation.get("fields", [])}
    extras = set(supplied) - set(fields)
    if extras:
        raise OperationError(f"出现未登记参数：{sorted(extras)}")
    values: dict[str, Any] = {}
    for key, field in fields.items():
        value = supplied.get(key, field.get("default"))
        kind = field["kind"]
        if enforce_required and field.get("required") is True:
            empty = value is None or value == "" or (isinstance(value, list) and not value)
            if empty:
                raise OperationError(f"{key} 为必填参数，请在前端表单中补全")
        # An omitted optional numeric argparse value is represented as JSON
        # null.  Preserve that omission through validation and argv
        # compilation; converting it with int()/float() would either fail or
        # (in the browser) turn an empty input into an unintended zero.
        if value is None and kind in {"integer", "number"}:
            values[key] = None
            continue
        if kind == "select":
            choice_map = field.get("_choice_value_map")
            if isinstance(choice_map, dict) and value in choice_map:
                value = choice_map[value]
            allowed = field.get("_token_choices", field.get("choices", []))
            if value not in allowed:
                raise OperationError(f"{key} 不是允许的选项")
        elif kind == "integer":
            if isinstance(value, bool):
                raise OperationError(f"{key} 必须是整数")
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise OperationError(f"{key} 必须是整数") from exc
            if value < field["min"] or value > field["max"]:
                raise OperationError(f"{key} 超出 [{field['min']}, {field['max']}]")
        elif kind == "number":
            if isinstance(value, bool):
                raise OperationError(f"{key} 必须是数字")
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise OperationError(f"{key} 必须是数字") from exc
            if not math.isfinite(value):
                raise OperationError(f"{key} 必须是有限数字")
            if value < field["min"] or value > field["max"]:
                raise OperationError(f"{key} 超出 [{field['min']}, {field['max']}]")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise OperationError(f"{key} 必须是布尔值")
        elif kind == "text":
            value = str(value or "")
            if len(value) > int(field.get("max_length", MAX_ARGV_TOKEN_LENGTH)):
                raise OperationError(f"{key} 过长")
            if not re.fullmatch(field.get("pattern", r".*"), value):
                raise OperationError(f"{key} 格式无效")
        elif kind == "argv_tokens":
            value = _validate_argv_tokens(value, key)
        else:
            raise OperationError(f"不支持的字段类型：{kind}")
        values[key] = value
    return values


def _inferred_argv(operation: dict[str, Any], values: dict[str, Any]) -> list[str]:
    result: list[str] = []
    specs = sorted(operation.get("_argument_specs", []), key=lambda item: item.get("order", 0))

    def supplied(value: Any, default: Any, spec: dict[str, Any]) -> bool:
        if value is None or value == "":
            return False
        if isinstance(value, list) and not value:
            return False
        return value != default or bool(spec.get("required"))

    # argparse requires a subcommand before options owned by that subparser.
    for spec in specs:
        if not spec.get("position") or spec.get("action") != "subcommand":
            continue
        value = values.get(spec["key"])
        if supplied(value, spec.get("default"), spec):
            result.append(str(value))

    # Positional values retain declaration order and never receive a flag.
    for spec in specs:
        if not spec.get("position") or spec.get("action") == "subcommand":
            continue
        value = values.get(spec["key"])
        default = spec.get("default")
        if not supplied(value, default, spec):
            continue
        if isinstance(value, list):
            result.extend(str(token) for token in value)
        else:
            result.append(str(value))

    # Optional flags can be emitted after positionals; argparse accepts this
    # ordering and it keeps the generated argv deterministic.
    for spec in specs:
        if spec.get("position"):
            continue
        value = values.get(spec["key"])
        default = spec.get("default")
        flag = spec.get("flag")
        action = spec.get("action")
        if not flag:
            continue
        if action == "store_true":
            if value is True and value != default:
                result.append(flag)
        elif action == "store_false":
            if value is False and value != default:
                result.append(flag)
        elif isinstance(value, list):
            if supplied(value, default, spec):
                result.append(flag)
                result.extend(str(token) for token in value)
        elif supplied(value, default, spec):
            result.extend([flag, str(value)])
    return result


def _append_user_tokens(operation: dict[str, Any], values: dict[str, Any], argv: list[str]) -> list[str]:
    return [*argv, *_inferred_argv(operation, values), *values.get("argv_tokens", [])]


def _validated_compiled_argv(argv: list[str]) -> list[str]:
    if not argv:
        raise OperationError("编译后的 argv 为空")
    total = 0
    for index, token in enumerate(argv):
        if not isinstance(token, str):
            raise OperationError(f"编译后的 argv[{index}] 不是字符串")
        if "\x00" in token or "\r" in token or "\n" in token:
            raise OperationError(f"编译后的 argv[{index}] 含控制字符")
        if len(token) > MAX_ARGV_TOKEN_LENGTH:
            raise OperationError(f"编译后的 argv[{index}] 超过 {MAX_ARGV_TOKEN_LENGTH} 字符")
        total += len(token)
        if total > MAX_ARGV_TOTAL_LENGTH:
            raise OperationError(f"编译后的 argv 总长度超过 {MAX_ARGV_TOTAL_LENGTH} 字符")
    return list(argv)


def _compile_command(
    operation_id: str,
    supplied: dict[str, Any],
    confirmed: bool = False,
    *,
    enforce_required: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    internal_operation_id = _resolve_operation_id(operation_id)
    operation = _catalog_map().get(internal_operation_id)
    if operation is None:
        raise OperationError("未知操作")
    if operation["risk"] in CONFIRMED_RISKS and not confirmed:
        raise OperationError("该操作需要明确二次确认")
    values = _validated_params(operation, supplied, enforce_required=enforce_required)
    if "argv" in operation:
        argv = _append_user_tokens(operation, values, list(operation["argv"]))
        return operation, values, _validated_compiled_argv(argv)

    py = sys.executable
    builders: dict[str, Callable[[], list[str]]] = {
        "reproduce_results": lambda: [py, "scripts/reproduce_all.py", "--mode", "results", "--python", py, "--seed", str(values["seed"])],
        "reproduce_pipeline": lambda: [py, "scripts/reproduce_all.py", "--mode", "pipeline", "--python", py, "--seed", str(values["seed"])],
        "preprocess": lambda: [py, "-m", "src.datasets.preprocess", "--config", values["config"], "--workers", values["workers"]],
        "sim_plan": lambda: [py, "scripts/sim_batch.py", "plan", "--config", values["config"], "--workers", values["workers"], "--json"] + (["--dry-run"] if values["dry_run"] else []),
        "sim_status": lambda: [py, "scripts/sim_batch.py", "status", "--config", values["config"], "--json"],
        "brphm_verify": lambda: [py, "-m", "brphm", "verify"],
        "brphm_reproduce": lambda: [py, "-m", "brphm", "reproduce", "--device", values["device"]],
        "brphm_reconstruct": lambda: [
            py, "-m", "brphm", "reconstruct", "--dataset-root", values["dataset_root"],
            "--component", values["component"], "--workers", values["workers"],
        ]
        + (["--verify"] if values["verify"] else [])
        + (["--files", *[item.strip() for item in values["files"].split(";") if item.strip()]] if values["files"].strip() else []),
        "competition_s22_s21_train": lambda: [py, "-m", "brphm", "train", "--device", values["device"]],
        "competition_s22_s21_transfer": lambda: [py, "-m", "brphm", "adapt", "--device", values["device"]],
        "competition_s22_s21_predict": lambda: [py, "-m", "brphm", "predict", "--device", values["device"], "--split", "evaluation", "--component", "both"],
        "train": lambda: [py, "-m", "src.train", "--config", values["config"], "--seed", str(values["seed"]), "--epochs", str(values["epochs"]), "--device", values["device"], "--mmdd", values["mmdd"]],
        "finetune": lambda: [py, "-m", "src.finetune", "--config", values["config"], "--mode", values["mode"], "--seed", str(values["seed"]), "--epochs", str(values["epochs"]), "--device", values["device"], "--mmdd", values["mmdd"]],
        "t2a_matrix": lambda: [py, "scripts/run_t2a_matrix.py", "--manifest", values["manifest"], "--python", py, "--device-override", values["device"]],
        "t2c": lambda: [py, "scripts/run_t2c.py", "--manifest", values["manifest"], "--phase", values["phase"], "--device", values["device"]],
        "t2c_safe_holdout": lambda: [py, "scripts/run_t2c.py", "--manifest", values["manifest"], "--phase", "holdout", "--device", values["device"]],
        "t3a": lambda: [py, "scripts/run_t3a_formal.py", "--config", values["config"], "--device", values["device"], "--mmdd", values["mmdd"]] + (["--debug"] if values["debug"] else []),
        "t3c": lambda: [py, "scripts/run_t3c_distill.py", "--config", values["config"], "--device", values["device"], "--epochs", str(values["epochs"])],
        "evaluate_run": lambda: [py, "-m", "src.evaluate"]
            + (["--run", values["run"]] if values["run"] else [])
            + ["--split", values["split"], "--domain", values["domain"]]
            + ([] if values["figures"] else ["--no-figure"]),
    }
    builder_name = operation.get("argv_builder")
    if builder_name not in builders:
        raise OperationError(f"操作缺少命令构建器：{internal_operation_id}")
    argv = _append_user_tokens(operation, values, builders[builder_name]())
    return operation, values, _validated_compiled_argv(argv)


def build_command(
    operation_id: str,
    supplied: dict[str, Any],
    confirmed: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Compile a registered operation without starting a process."""
    operation, _values, argv = _compile_command(operation_id, supplied, confirmed)
    return operation, argv


def _competition_artifact_availability(operation_id: str) -> dict[str, Any]:
    """Validate immutable competition prerequisites without starting a stage."""
    if operation_id not in {
        "competition_s22_s21_train",
        "competition_s22_s21_transfer",
        "competition_s22_s21_predict",
    }:
        return {"available": True, "reason": None}
    try:
        from src.competition_s22_s21 import (
            CompetitionContractError,
            EXPECTED_ROUTES,
            _manifest_artifact_path,
            _read_manifest,
            _validate_pretrain_receipt_contract,
            load_config,
            sha256,
        )

        config_path = ROOT / COMPETITION_CONFIG
        config = load_config(config_path)
        if operation_id == "competition_s22_s21_train":
            return {"available": True, "reason": None}

        if operation_id == "competition_s22_s21_transfer":
            receipt_path = ROOT / COMPETITION_PRETRAIN_OUTPUT / "pretrain_receipt.json"
            if not receipt_path.is_file():
                return {"available": False, "reason": "有效的公开预训练收据尚不存在"}
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"available": False, "reason": "公开预训练收据无法读取"}
            _validate_pretrain_receipt_contract(receipt, config, ROOT)
            return {"available": True, "reason": None}

        manifest_path = ROOT / COMPETITION_MANIFEST
        if not manifest_path.is_file():
            return {"available": False, "reason": "有效的生产模型清单尚不存在"}
        manifest, _resolved_manifest = _read_manifest(ROOT, manifest_path)
        for component, route_id in EXPECTED_ROUTES.items():
            route = manifest["routes"][component]
            if route.get("route_id") != route_id:
                raise CompetitionContractError("production route identity drift")
            members = route.get("members")
            if (
                not isinstance(members, list)
                or len(members) != 3
                or {item.get("seed") for item in members if isinstance(item, dict)} != {17, 42, 73}
            ):
                raise CompetitionContractError("production member ledger drift")
            for member in members:
                checkpoint = _manifest_artifact_path(
                    ROOT, member.get("path"), "route checkpoint",
                )
                if not checkpoint.is_file() or sha256(checkpoint) != member.get("sha256"):
                    raise CompetitionContractError("production checkpoint hash drift")
        return {"available": True, "reason": None}
    except (ImportError, OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        label = "公开预训练收据" if operation_id == "competition_s22_s21_transfer" else (
            "生产模型清单" if operation_id == "competition_s22_s21_predict" else "竞赛配置"
        )
        return {"available": False, "reason": f"{label}未通过身份与哈希核验：{exc}"}


def _operation_availability(operation: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime_availability(operation)
    if runtime.get("available") is not True:
        return runtime
    artifact = _competition_artifact_availability(str(operation.get("id", "")))
    if artifact.get("available") is not True:
        return {**runtime, "available": False, "reason": artifact.get("reason")}
    return runtime


def preflight_operation(
    operation_id: str,
    supplied: dict[str, Any],
    confirmed: bool = False,
) -> dict[str, Any]:
    """Validate and preview an operation without starting a process."""
    operation, values, argv = _compile_command(
        operation_id, supplied, confirmed, enforce_required=True,
    )
    availability = _operation_availability(operation)
    available = availability.get("available") is True
    reason = availability.get("reason")
    return {
        "ok": available,
        "available": available,
        "reason": reason,
        "operation_id": (
            _public_metadata(operation)["id"]
            if operation.get("audience") == "user" and str(operation_id) != operation["id"]
            else operation["id"]
        ),
        "surface_id": operation.get("entry_surface_id"),
        "risk": operation["risk"],
        "resource": operation["resource"],
        "requires_confirmation": operation["risk"] in CONFIRMED_RISKS,
        "availability": availability,
        "argv": argv,
        "params": values,
        "working_directory": str(ROOT),
        "errors": [] if available else [reason or "解释器不可用"],
    }


def prepare_execution(
    operation_id: str,
    supplied: dict[str, Any],
    confirmed: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Compile an operation and fail closed if its runtime entry is unavailable."""
    operation, values, argv = _compile_command(
        operation_id, supplied, confirmed, enforce_required=True,
    )
    availability = _operation_availability(operation)
    if availability.get("available") is not True:
        raise OperationError(availability.get("reason") or "操作入口当前不可用")
    return operation, values, argv
