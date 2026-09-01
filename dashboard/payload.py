# -*- coding: utf-8 -*-
"""Read-only data adapter for the RUL-SPACE operations cockpit.

The adapter joins replay-ready holdout telemetry with the backend's holdout
prediction artifacts. It never imports training modules and never writes files.
Mock data is available only through the explicit RUL_DASHBOARD_ALLOW_MOCK=1
escape hatch and is always labelled as mock in the returned payload.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from numbers import Real
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
CONFIG_PATH = HERE / "config.json"
MOCK_JSON = HERE / "mock" / "mock_results.json"
RESULTS = REPO_ROOT / "results"
HOLDOUT = REPO_ROOT / "data" / "holdout"
_LEGACY_COMPETITION_RESULT_ROOT = RESULTS / "competition" / "s22_s21_20260828"
_DEFAULT_COMPETITION_RESULT_ROOT = RESULTS / "competition" / "s22_s21_gpu_pathfix35_rwa_torch_hgb_parity_20260830"
LUT_MANIFEST = REPO_ROOT / "sim" / "lut" / "lut_manifest.json"
GMAT_ROOT = REPO_ROOT / "sim" / "gmat"
EARTH_EQUATORIAL_RADIUS_KM = 6378.137
EARTH_MU_KM3_S2 = 398600.4418

DISCIPLINE = "本应用只读 results/，永不触碰训练代码与数据。"

PREDICTION_FILES = {
    "bat": {
        "interval": RESULTS / "runs" / "t2c_bat_holdout" / "preds.parquet",
        "safe_interval": RESULTS / "runs" / "t2c_bat_holdout_safe" / "preds.parquet",
        "t1": RESULTS / "holdout" / "t1" / "t1ft_bat_0723_s0.parquet",
        "t2": RESULTS / "holdout" / "t2" / "t2a_bat_full_optimized_s0.parquet",
        "time_unit": "cycles",
    },
    "rw": {
        "interval": RESULTS / "runs" / "t2c_rwa_holdout" / "preds.parquet",
        "safe_interval": RESULTS / "runs" / "t2c_rwa_holdout_safe" / "preds.parquet",
        "t1": RESULTS / "holdout" / "t1" / "t1ft_rw_0717_s0.parquet",
        "t2": RESULTS / "holdout" / "t2" / "t2a_rw_full_optimized_s0.parquet",
        "time_unit": "days",
    },
}

QA_FILES = {
    "gate2": RESULTS / "qa" / "gate2.json",
    "gate3": RESULTS / "qa" / "gate3.json",
    "t3c": RESULTS / "qa" / "t3c_deploy.json",
    "data_version": RESULTS / "qa" / "data_version.json",
    "route_ledger": RESULTS / "research" / "s3_ledger_20260805.json",
    "output_safety": RESULTS / "qa" / "t2c_output_safety_final.json",
    "data_freeze": RESULTS / "qa" / "data_freeze.json",
    "rwa_plan_a_selection": RESULTS / "qa" / "rwa_plan_a_candidate_selection.json",
    "compatibility": REPO_ROOT / "qa" / "compatibility_evidence" / "backend_nonfrontend_full_pytest_20260805T052956_AsiaShanghai.junit.xml",
}

T1_EVIDENCE_TABLES = {
    "bat": RESULTS / "tables" / "t1_bat_full_matrix.csv",
    "rwa": RESULTS / "tables" / "t1_rw_full_matrix.csv",
}


def _competition_result_root() -> Path:
    """Resolve one versioned competition snapshot for both results and evidence.

    Rack deployments set ``RUL_DASHBOARD_COMPETITION_RESULT_ROOT`` when the
    snapshot is outside the checkout.  In a checked-out snapshot the current
    pathfix35 directory is preferred automatically.  The legacy path remains a
    read-only fallback solely for older local fixtures and existing tests.
    """
    configured = os.environ.get("RUL_DASHBOARD_COMPETITION_RESULT_ROOT", "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        return candidate
    if (_DEFAULT_COMPETITION_RESULT_ROOT / "validation" / "prediction_validation_receipt.json").is_file():
        return _DEFAULT_COMPETITION_RESULT_ROOT
    return _LEGACY_COMPETITION_RESULT_ROOT


def _competition_receipt_paths(root: Path) -> dict[str, Path]:
    return {
        "manifest": root / "transfer" / "manifest.json",
        "transfer": root / "transfer" / "transfer_receipt.json",
        "pretrain": root / "pretrain" / "pretrain_receipt.json",
        "validation": root / "validation" / "prediction_validation_receipt.json",
        "holdout": root / "holdout" / "prediction_holdout_receipt.json",
        "validation_bat": root / "validation" / "bat_validation_predictions.csv",
        "validation_rwa": root / "validation" / "rwa_validation_predictions.csv",
        "holdout_bat": root / "holdout" / "bat_holdout_predictions.csv",
        "holdout_rwa": root / "holdout" / "rwa_holdout_predictions.csv",
    }

CHANNELS = {
    "bat": ("voltage_v", "current_a", "temp_c", "soc"),
    "rw": ("speed_rpm", "motor_current_a", "cmd_torque_nm", "bearing_temp_c"),
}

MECHANISM_CHANNELS = {
    "bat": ("soh", "lli"),
    "rw": ("friction_torque_nm", "wear_volume_mm3", "lube_level"),
}

CHANNEL_META = {
    "bat.voltage_v": {"label": "端电压", "unit": "V"},
    "bat.current_a": {"label": "电流", "unit": "A"},
    "bat.temp_c": {"label": "电池温度", "unit": "°C"},
    "bat.soc": {"label": "荷电状态", "unit": "ratio"},
    "rw.speed_rpm": {"label": "轮速", "unit": "rpm"},
    "rw.motor_current_a": {"label": "电机电流", "unit": "A"},
    "rw.cmd_torque_nm": {"label": "指令力矩", "unit": "N·m"},
    "rw.bearing_temp_c": {"label": "轴承温度", "unit": "°C"},
    "bat.soh": {"label": "SOH", "unit": "ratio"},
    "bat.lli": {"label": "锂库存损失", "unit": "ratio"},
    "rw.friction_torque_nm": {"label": "摩擦力矩", "unit": "N·m"},
    "rw.wear_volume_mm3": {"label": "磨损体积", "unit": "mm³"},
    "rw.lube_level": {"label": "润滑水平", "unit": "ratio"},
}

# Public component wording is deliberately independent from the internal
# route IDs used by the production contract.  Every evaluator-facing payload
# label must identify both the physical component and its system role.
_PUBLIC_COMPONENT_LABELS = {
    "bat": "电池部件（储能系统）",
    "rw": "反作用轮部件（姿态控制执行器）",
    "rwa": "反作用轮部件（姿态控制执行器）",
}
_PUBLIC_MODEL_NAMES = {
    "bat": "电池部件剩余寿命预测模型（储能系统）",
    "rw": "反作用轮部件剩余寿命预测模型（姿态控制执行器）",
    "rwa": "反作用轮部件剩余寿命预测模型（姿态控制执行器）",
}
_PUBLIC_MODEL_VERSION = "cb7628d6dd487559cd8aec230fe497d882956e1c84556ae2c752c54393d422f3"

FAILURE_MODES = {
    0: "删失 / 未触发",
    1: "RW 累计磨损超阈",
    2: "RW 转速跟踪持续失败",
    3: "RW 润滑突失",
    11: "BAT SOH ≤ 80%",
    12: "BAT 单体微短路",
}

SAMPLE_RE = re.compile(
    r"^(?P<prefix>BAT|RWA)_(?P<orbit>[A-Z]+\d{3})_B(?P<beta>\d{2})_"
    r"H(?P<h>[0-2])_L(?P<l>\d)_(?P<sample_marker>S|N(?=2[12]\d$))(?P<seed>\d{3})$"
)

GMAT_ELEMENT_RE = re.compile(
    r"^\s*GMAT\s+DefaultSC\.(?P<name>SMA|ECC|INC|RAAN|AOP|TA)\s*=\s*"
    r"(?P<value>[-+]?\d+(?:\.\d+)?)\s*;\s*$",
    re.MULTILINE,
)
GMAT_EPOCH_RE = re.compile(
    r"^\s*GMAT\s+DefaultSC\.Epoch\s*=\s*'(?P<value>[^']+)'\s*;\s*$",
    re.MULTILINE,
)

# The competition snapshot is intentionally small and may not carry the
# source GMAT scripts.  Keep the already-audited script values as a read-only
# deployment fallback so a snapshot packaging decision cannot erase the
# orbit projection from the cockpit.  These values mirror sim/gmat/*.script.
_FROZEN_GMAT_ELEMENTS = {
    "LEO500": {"SMA": 6878.137, "ECC": 0.001, "INC": 97.3, "AOP": 90.0, "TA": 0.0},
    "LEO550": {"SMA": 6928.137, "ECC": 0.001, "INC": 97.3, "AOP": 90.0, "TA": 0.0},
    "LEO700": {"SMA": 7078.137, "ECC": 0.001, "INC": 97.3, "AOP": 90.0, "TA": 0.0},
}
_FROZEN_GMAT_RAAN = {"B00": 104.2, "B30": 71.6, "B60": 37.7}
_FROZEN_GMAT_EPOCH = "01 Jan 2026 00:00:00.000"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default


@lru_cache(maxsize=16)
def _gmat_orbit_state(orbit: str, beta_deg: int) -> dict[str, Any] | None:
    """Read the frozen Keplerian initial state from the matching GMAT script."""
    orbit_id = f"{orbit}_B{int(beta_deg):02d}"
    path = GMAT_ROOT / f"{orbit_id}.script"
    source = None
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        pass
    elements = ({match.group("name"): float(match.group("value")) for match in GMAT_ELEMENT_RE.finditer(source)}
                if source is not None else {})
    if not elements:
        base = _FROZEN_GMAT_ELEMENTS.get(str(orbit))
        raan = _FROZEN_GMAT_RAAN.get(f"B{int(beta_deg):02d}")
        if base is None or raan is None:
            return None
        elements = {**base, "RAAN": raan}
    if set(elements) != {"SMA", "ECC", "INC", "RAAN", "AOP", "TA"}:
        return None
    if not all(math.isfinite(value) for value in elements.values()):
        return None
    if not (elements["SMA"] > EARTH_EQUATORIAL_RADIUS_KM and 0 <= elements["ECC"] < 1):
        return None
    if not 0 <= elements["INC"] <= 180:
        return None
    epoch = GMAT_EPOCH_RE.search(source) if source is not None else None
    period_min = 2 * math.pi * math.sqrt(elements["SMA"] ** 3 / EARTH_MU_KM3_S2) / 60
    result = {
        "schema": "orbit-elements-1.0",
        "orbit_id": orbit_id,
        "source": "GMAT 冻结初始根数（随部署内嵌）" if source is None else "GMAT 冻结初始根数",
        "source_file": f"sim/gmat/{orbit_id}.script",
        "reference_frame": "EarthMJ2000Eq",
        "reference_radius_km": EARTH_EQUATORIAL_RADIUS_KM,
        "semi_major_axis_km": elements["SMA"],
        "eccentricity": elements["ECC"],
        "inclination_deg": elements["INC"],
        "raan_deg": elements["RAAN"],
        "arg_periapsis_deg": elements["AOP"],
        "true_anomaly_deg": elements["TA"],
        "period_min": period_min,
        "epoch_utc": epoch.group("value") if epoch else _FROZEN_GMAT_EPOCH,
    }
    return result


def _policy() -> dict:
    value = _read_json(CONFIG_PATH, {})
    zones = value.get("hi_zones") if isinstance(value, dict) else None
    if not isinstance(zones, list) or len(zones) != 3:
        return {
            "schema": "dashboard-policy-invalid",
            "hi_zones": [],
            "maintenance_margin": {},
            "display_points": 420,
            "playback_seconds": 24,
            "policy_note": "配置不可用；告警与维护建议已禁用。",
            "valid": False,
        }
    value["valid"] = True
    return value


def _r4(value: Any) -> float:
    return round(float(value), 4)


def _r4_or_none(value: Any) -> float | None:
    """Round a finite numeric value without inventing a fallback."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if math.isfinite(number) else None


def _active_lut(line: str) -> dict:
    manifest = _read_json(LUT_MANIFEST, {})
    table_name = "bat_aging_lut.csv" if line == "bat" else "rw_friction_lut.csv"
    table = ((manifest or {}).get("tables") or {}).get(table_name, {})
    identity = {}
    if line == "rw":
        for fragment in str(table.get("note", "")).split(";"):
            key, separator, value = fragment.strip().partition("=")
            if separator and key in {
                "romax_product", "romax_version", "bearing_catalog_entry",
                "loss_model", "lubricant",
            }:
                identity[key] = value.strip().strip('"')
    return {
        "name": table_name,
        "plan": table.get("plan"),
        "version": table.get("version"),
        "sha256": table.get("sha256"),
        "identity": identity,
        "source": "sim/lut/lut_manifest.json",
    }


def _rwa_role_separation() -> dict:
    receipt = _read_json(QA_FILES["rwa_plan_a_selection"], {})
    production = receipt.get("production_selection") if isinstance(receipt, dict) else {}
    identity = receipt.get("identity") if isinstance(receipt, dict) else {}
    valid = bool(
        receipt.get("pass") is True
        and receipt.get("status") == "geometry_selected_dataset_not_promoted"
        and isinstance(production, dict)
        and production.get("selected") is False
        and production.get("prediction_metrics_used") is False
        and isinstance(identity, dict)
        and identity.get("friction_plan") == "A"
    )
    if not valid:
        return {"valid": False, "source": str(QA_FILES["rwa_plan_a_selection"])}
    return {
        "valid": True,
        "source": str(QA_FILES["rwa_plan_a_selection"]),
        "schema": receipt.get("schema"),
        "status": receipt.get("status"),
        "formal_role": "冻结 B 多机制生产语料",
        "candidate_role": "Romax A 隔离压力与适用边界语料",
        "decision": production.get("decision"),
        "reason": production.get("reason"),
        "identity": identity,
    }


def _compare_lut(line: str, embedded: dict) -> dict:
    active = _active_lut(line)
    known = all((embedded.get("lut_plan"), embedded.get("lut_version"),
                 active.get("plan"), active.get("version")))
    matches = bool(known and embedded["lut_plan"] == active["plan"]
                   and embedded["lut_version"] == active["version"])
    role_separation = _rwa_role_separation() if line == "rw" else {"valid": False}
    roles_are_distinct = bool(
        known and not matches and role_separation.get("valid")
        and embedded.get("lut_plan") == "B" and active.get("plan") == "A"
    )
    return {
        "state": (
            "current" if matches else
            "role-separated" if roles_are_distinct else
            "mismatch" if known else "unknown"
        ),
        "matches": matches if known else None,
        "embedded": {
            "name": embedded.get("lut_name"),
            "plan": embedded.get("lut_plan"),
            "version": embedded.get("lut_version"),
        },
        "active": active,
        "role_separation": role_separation if line == "rw" else None,
        "note": (
            "样本与当前机读 LUT 基线一致。" if matches else
            "样本属于冻结 B 多机制生产语料；仓库中的 Romax A 是隔离压力集，角色不同，不作版本冲突判定。" if roles_are_distinct else
            "样本保留生成时版本；未重标记为当前基线。" if known else
            "版本信息不足，未作一致性推断。"
        ),
    }


def _decode_u8(dataset: Any) -> str:
    try:
        import numpy as np

        return bytes(np.asarray(dataset).reshape(-1).tolist()).decode("utf-8", "replace")
    except Exception:
        return ""


def _selected_indices(t_values: Any, failure_time: float, limit: int) -> Any:
    import numpy as np

    t = np.asarray(t_values, dtype=float).reshape(-1)
    if not t.size:
        return np.asarray([], dtype=int)
    failure_index = int(np.searchsorted(t, failure_time, side="left"))
    failure_index = min(max(failure_index, 1), t.size - 1)
    count = failure_index + 1
    if limit <= 0 or count <= limit:
        return np.arange(count, dtype=int)
    idx = np.linspace(0, failure_index, limit).round().astype(int)
    return np.unique(np.concatenate(([0], idx, [failure_index]))).astype(int)


@lru_cache(maxsize=128)
def _read_mat_cached(path_text: str, mtime_ns: int, display_points: int) -> dict | None:
    del mtime_ns
    try:
        import h5py
        import numpy as np
    except Exception:
        return None

    path = Path(path_text)
    line = "bat" if path.stem.startswith("BAT_") else "rw"
    try:
        with h5py.File(str(path), "r") as handle:
            t_all = np.asarray(handle["/telemetry/t_days"][()]).reshape(-1)
            attrs = handle["/labels"].attrs
            failure_time = float(np.asarray(attrs["failure_time_days"]).reshape(-1)[0])
            failure_mode = int(np.asarray(attrs.get("failure_mode_code", [0])).reshape(-1)[0])
            idx = _selected_indices(t_all, failure_time, display_points)
            if idx.size < 2:
                return None

            def take(key: str) -> list[float]:
                array = np.asarray(handle[key][()]).reshape(-1)
                return [_r4(v) for v in array[idx]]

            channels = {
                f"{line}.{name}": take(f"/telemetry/{line}/{name}")
                for name in CHANNELS[line]
                if f"/telemetry/{line}/{name}" in handle
            }
            mechanism = {
                f"{line}.{name}": take(f"/truth/{line}/{name}")
                for name in MECHANISM_CHANNELS[line]
                if f"/truth/{line}/{name}" in handle
            }
            orbit = {
                "eclipse_flag": take("/telemetry/orbit/eclipse_flag")
                if "/telemetry/orbit/eclipse_flag" in handle else [],
                "beta_deg": take("/telemetry/orbit/beta_deg")
                if "/telemetry/orbit/beta_deg" in handle else [],
            }
            labels = {
                name: take(f"/labels/{name}")
                for name in ("hi", "rul_days", "fail")
                if f"/labels/{name}" in handle
            }
            meta_json = _decode_u8(handle["/meta/meta_json"][()]) \
                if "/meta/meta_json" in handle else ""
            meta = json.loads(meta_json) if meta_json else {}
            lut = {}
            try:
                lut = json.loads(meta.get("lut_versions", "{}"))
            except (TypeError, ValueError):
                pass
            table_name = "bat_aging_lut.csv" if line == "bat" else "rw_friction_lut.csv"
            table = (lut.get("tables") or {}).get(table_name, {})
            provenance = {
                "schema": meta.get("schema_version", "simout-1.0"),
                "sim_model": meta.get("sim_model_version", "unknown"),
                "created_utc": meta.get("created_utc"),
                "matlab": meta.get("matlab_ver"),
                "lut_name": table_name,
                "lut_plan": table.get("plan"),
                "lut_version": table.get("version"),
                "lut_physics": table.get("physics") or table.get("backend"),
            }
            return {
                "t_days": [_r4(t_all[i]) for i in idx],
                "channels": channels,
                "mechanism": mechanism,
                "orbit": orbit,
                "labels": labels,
                "failure_time_days": _r4(failure_time),
                "failure_mode_code": failure_mode,
                "failure_mode": FAILURE_MODES.get(failure_mode, f"模式 {failure_mode}"),
                "provenance": provenance,
            }
    except Exception:
        return None


def _read_mat(path: Path, display_points: int) -> dict | None:
    try:
        return _read_mat_cached(str(path.resolve()), path.stat().st_mtime_ns, display_points)
    except OSError:
        return None


def _read_parquet(path: Path) -> list[dict]:
    try:
        import pyarrow.parquet as parquet

        return parquet.read_table(path).to_pylist()
    except Exception:
        return []


def _prediction_bundle(line: str) -> tuple[dict[str, dict], dict]:
    spec = PREDICTION_FILES[line]
    interval_path = spec["interval"]
    safety_fields_available = False
    safe_path = spec.get("safe_interval")
    if isinstance(safe_path, Path) and safe_path.is_file():
        safe_rows = _read_parquet(safe_path)
        if safe_rows and {"y_pred_rul", "y_pred_rul_raw", "rul_output_clamped"} <= set(safe_rows[0]):
            interval_rows = safe_rows
            interval_path = safe_path
            safety_fields_available = True
        else:
            interval_rows = _read_parquet(spec["interval"])
    else:
        interval_rows = _read_parquet(spec["interval"])
    t1_rows = _read_parquet(spec["t1"])
    t2_rows = _read_parquet(spec["t2"])
    if not interval_rows:
        return {}, {"available": False, "line": line, "missing": str(spec["interval"])}

    def row_key(row: dict) -> tuple[str, str] | None:
        unit = str(row.get("unit_id", ""))
        window = str(row.get("window_id", ""))
        return (unit, window) if unit and window else None

    def by_window(rows: list[dict]) -> dict:
        return {key: row for row in rows if (key := row_key(row)) is not None}

    t1_by = by_window(t1_rows)
    t2_by = by_window(t2_rows)
    grouped: dict[str, list] = {}
    integrity = {
        "invalid_interval_rows": 0,
        "quantile_crossings": 0,
        "missing_t1": 0,
        "missing_t2": 0,
        "non_holdout_rows": 0,
        "safety_fields_available": safety_fields_available,
        "raw_bounds_violation_rows": 0,
        "raw_clamp_rows": 0,
    }
    for row in interval_rows:
        unit = str(row.get("unit_id", ""))
        window = str(row.get("window_id", ""))
        if not unit or not window:
            integrity["invalid_interval_rows"] += 1
            continue
        if str(row.get("split", "")) != "holdout":
            integrity["non_holdout_rows"] += 1
            continue
        critical = {
            "t": _r4_or_none(row.get("t_end", row.get("t"))),
            "y_true": _r4_or_none(row.get("y_true_rul")),
            "p10": _r4_or_none(row.get("p10")),
            "p50": _r4_or_none(row.get("p50")),
            "p90": _r4_or_none(row.get("p90")),
        }
        if any(value is None for value in critical.values()):
            integrity["invalid_interval_rows"] += 1
            continue
        key = (unit, window)
        t1 = t1_by.get(key)
        t2 = t2_by.get(key)
        if t1 is None:
            integrity["missing_t1"] += 1
        if t2 is None:
            integrity["missing_t2"] += 1
        interval_order_valid = critical["p10"] <= critical["p50"] <= critical["p90"]
        if not interval_order_valid:
            integrity["quantile_crossings"] += 1
        rec = {
            "window_id": window,
            **critical,
            "ensemble_std": _r4_or_none(row.get("ensemble_std")),
            "y_pred_rul": _r4_or_none(row.get("y_pred_rul")),
            "y_pred_rul_raw": _r4_or_none(row.get("y_pred_rul_raw")),
            "rul_output_clamped": (bool(row.get("rul_output_clamped"))
                                    if "rul_output_clamped" in row else None),
            "t1": _r4_or_none(t1.get("y_pred_rul")) if t1 else None,
            "t2": _r4_or_none(t2.get("y_pred_rul")) if t2 else None,
            "interval_order_valid": interval_order_valid,
        }
        if rec["rul_output_clamped"]:
            integrity["raw_bounds_violation_rows"] += 1
            integrity["raw_clamp_rows"] += 1
        grouped.setdefault(unit, []).append(rec)

    bundles = {}
    for unit, rows in grouped.items():
        rows.sort(key=lambda item: item["t"])
        bundles[unit] = {
            "time_unit": spec["time_unit"],
            "rows": rows,
            "run_ids": {
                "interval": spec["interval"].parent.name,
                "t1": str(t1_rows[0].get("run_id", "")) if t1_rows else "",
                "t2": str(t2_rows[0].get("run_id", "")) if t2_rows else "",
            },
        }
    return bundles, {
        "available": True,
        "line": line,
        "n_windows": len(interval_rows),
        "n_units": len(bundles),
        "time_unit": spec["time_unit"],
        "integrity": integrity,
        "sources": {key: str(value) for key, value in spec.items() if isinstance(value, Path)},
        "interval_source": str(interval_path),
        "safety_fields_available": safety_fields_available,
    }


def _competition_csv_rows(path: Path, expected_route: str) -> tuple[list[dict], dict]:
    """Read the small, machine-produced competition prediction table."""
    rows: list[dict] = []
    invalid = 0
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                unit = str(raw.get("unit_id", "")).strip()
                window = str(raw.get("window_id", "")).strip()
                route = str(raw.get("route_id", "")).strip()
                t = _r4_or_none(raw.get("t_end"))
                prediction = _r4_or_none(raw.get("prediction"))
                truth = _r4_or_none(raw.get("truth"))
                if not unit or not window or route != expected_route or any(
                    value is None for value in (t, prediction, truth)
                ):
                    invalid += 1
                    continue
                # Seed-bearing source IDs can contain ``S21``/``S22`` as a
                # substring (for example ``..._S222``).  Those are sample
                # identities, not route IDs, but the browser projection must
                # not expose anything that can be mistaken for a development
                # route code.
                public_unit = re.sub(r"_S(21|22)(?=\d)", r"_N\1", unit, flags=re.IGNORECASE)
                public_window = re.sub(r"_S(21|22)(?=\d)", r"_N\1", window, flags=re.IGNORECASE)
                rows.append({
                    "window_id": public_window,
                    "unit_id": public_unit,
                    "t": t,
                    "y_true": truth,
                    "p50": prediction,
                    "y_pred_rul": prediction,
                    "interval_order_valid": None,
                })
    except (OSError, UnicodeError):
        return [], {"available": False, "invalid_rows": invalid, "path": str(path)}
    return rows, {
        "available": bool(rows),
        "invalid_rows": invalid,
        "path": str(path),
        "n_windows": len(rows),
        "n_units": len({row["unit_id"] for row in rows}),
    }


def _competition_prediction_bundle(line: str, split: str) -> tuple[dict[str, dict], dict]:
    root = _competition_result_root()
    route = "S22" if line == "bat" else "S21"
    unit = "cycles" if line == "bat" else "days"
    filename = f"{split}/{'bat' if line == 'bat' else 'rwa'}_{split}_predictions.csv"
    rows, integrity = _competition_csv_rows(root / filename, route)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["unit_id"], []).append({key: value for key, value in row.items() if key != "unit_id"})
    bundles = {
        sample_id: {"time_unit": unit, "rows": sorted(values, key=lambda item: item["t"]), "run_ids": {"split": split}}
        for sample_id, values in grouped.items()
    }
    integrity.update({"available": bool(bundles), "line": line, "split": split, "time_unit": unit})
    return bundles, integrity


def _competition_evidence() -> dict[str, Any]:
    """Return real snapshot identity and metrics without exposing route IDs."""
    root = _competition_result_root()
    paths = _competition_receipt_paths(root)
    manifest = _read_json(paths["manifest"], {})
    validation = _read_json(paths["validation"], {})
    holdout = _read_json(paths["holdout"], {})
    if not isinstance(manifest, dict) or not isinstance(validation, dict) or not isinstance(holdout, dict):
        return {"available": False, "result_root": str(root)}
    lines: dict[str, dict[str, Any]] = {}
    for public_line, raw_line in (("bat", "bat"), ("rwa", "rwa")):
        val_item = (validation.get("lines") or {}).get(raw_line, {})
        hold_item = (holdout.get("lines") or {}).get(raw_line, {})
        if isinstance(val_item, dict) and isinstance(hold_item, dict):
            lines[public_line] = {
                "validation": val_item.get("metrics") if isinstance(val_item.get("metrics"), dict) else {},
                "holdout": hold_item.get("metrics") if isinstance(hold_item.get("metrics"), dict) else {},
                "rul_unit": hold_item.get("rul_unit") or val_item.get("rul_unit"),
            }
    return {
        "available": bool(lines) and validation.get("status") == "pass" and holdout.get("status") == "pass",
        "framework": ((manifest.get("framework") or {}).get("name") or validation.get("framework", {}).get("name")),
        "framework_version": ((manifest.get("framework") or {}).get("version") or validation.get("framework", {}).get("version")),
        "manifest_sha256": manifest.get("manifest_sha256") or validation.get("manifest_sha256"),
        "config_sha256": manifest.get("config_sha256"),
        "implementation_sha256": manifest.get("implementation_sha256"),
        "validation": validation.get("split") == "validation",
        "holdout": holdout.get("split") == "holdout",
        "lines": lines,
        "result_root": str(root),
    }


def _load_competition_real(policy: dict) -> tuple[list, dict, dict, dict] | None:
    evidence = _competition_evidence()
    if evidence.get("available") is not True:
        return None
    predictions: dict[str, dict] = {}
    pred_origin: dict[str, dict] = {}
    for line in ("bat", "rw"):
        public_line = "bat" if line == "bat" else "rwa"
        bundles, origin = _competition_prediction_bundle(line, "holdout")
        predictions.update(bundles)
        pred_origin[line] = origin
    if not predictions:
        return None
    telemetry: dict[str, dict] = {}
    samples: list[dict] = []
    for sample_id, prediction in sorted(predictions.items()):
        rows = prediction.get("rows", [])
        line = "bat" if sample_id.startswith("BAT_") else "rw"
        truths = [row["y_true"] for row in rows if row.get("y_true") is not None]
        times = [row["t"] for row in rows if row.get("t") is not None]
        # The competition CSV is a prediction artifact, not raw telemetry. Keep
        # that distinction explicit: no channels or orbital state are guessed.
        telemetry[sample_id] = {
            "t_days": times,
            "time_unit": prediction.get("time_unit", "days"),
            "channels": {},
            "labels": {"rul_days": truths},
            "failure_time_days": None,
            "failure_mode_code": 0,
            "provenance": {"sim_model": _PUBLIC_MODEL_NAMES[line]},
        }
        samples.append(_sample_card(sample_id, telemetry[sample_id]))
    return samples, telemetry, predictions, pred_origin


def _sample_card(sample_id: str, telemetry: dict) -> dict:
    match = SAMPLE_RE.match(sample_id)
    values = match.groupdict() if match else {}
    line = "bat" if sample_id.startswith("BAT_") else "rw"
    provenance = telemetry["provenance"]
    orbit_name = values.get("orbit", "unknown")
    beta_deg = int(values.get("beta", 0))
    return {
        "sample_id": sample_id,
        "line": line,
        "line_label": _PUBLIC_COMPONENT_LABELS[line],
        "dataset_id": "SIM_bat" if line == "bat" else "SIM_rwa",
        "orbit": orbit_name,
        "beta_deg": beta_deg,
        "orbit_state": _gmat_orbit_state(orbit_name, beta_deg),
        "health_level": f"H{values.get('h', '?')}",
        "load_level": f"L{values.get('l', '?')}",
        "sample_index": int(values.get("seed", 0)),
        "failure_time_days": telemetry["failure_time_days"],
        "failure_mode_code": telemetry["failure_mode_code"],
        **({"failure_mode": telemetry["failure_mode"]} if telemetry.get("failure_mode") else {}),
        "provenance": provenance,
        "lut_comparison": _compare_lut(line, provenance),
    }


def _load_real(policy: dict) -> tuple[list, dict, dict, dict]:
    predictions: dict[str, dict] = {}
    pred_origin = {}
    for line in ("bat", "rw"):
        line_predictions, origin = _prediction_bundle(line)
        predictions.update(line_predictions)
        pred_origin[line] = origin

    paths = []
    for line in ("bat", "rwa"):
        folder = HOLDOUT / line
        try:
            paths.extend(path for path in folder.glob("*.mat") if path.stem in predictions)
        except OSError:
            pass
    paths.sort(key=lambda path: path.stem)
    display_points = int(policy.get("display_points", 420))
    workers = max(1, min(len(paths), int(os.environ.get("RUL_MAT_WORKERS", "8") or 8)))

    def load(path: Path) -> tuple[str, dict | None]:
        return path.stem, _read_mat(path, display_points)

    if workers > 1 and len(paths) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            loaded = list(pool.map(load, paths))
    else:
        loaded = [load(path) for path in paths]

    telemetry = {sample_id: value for sample_id, value in loaded if value is not None}
    matched = sorted(set(telemetry) & set(predictions))
    samples = [_sample_card(sample_id, telemetry[sample_id]) for sample_id in matched]
    predictions = {sample_id: predictions[sample_id] for sample_id in matched}
    return samples, telemetry, predictions, pred_origin


def _integrity_summary(samples: list[dict], pred_origin: dict) -> dict:
    mismatches = [
        sample["sample_id"] for sample in samples
        if sample.get("lut_comparison", {}).get("state") == "mismatch"
    ]
    unknown = [
        sample["sample_id"] for sample in samples
        if sample.get("lut_comparison", {}).get("state") == "unknown"
    ]
    role_separated = [
        sample["sample_id"] for sample in samples
        if sample.get("lut_comparison", {}).get("state") == "role-separated"
    ]
    prediction_issues = {
        line: origin.get("integrity", {}) for line, origin in pred_origin.items()
    }
    return {
        "lut_version_mismatch_count": len(mismatches),
        "lut_version_mismatch_samples": mismatches,
        "lut_version_unknown_count": len(unknown),
        "lut_role_separated_count": len(role_separated),
        "lut_role_separated_samples": role_separated,
        "prediction": prediction_issues,
        "truth_layers": {
            "telemetry": "仿真输出回放",
            "mechanism": "仿真机理真值，仅用于回顾解释",
            "labels": "仿真标签真值，不等同于在线可观测量",
            "prediction": "独立测试模型估计与校准区间",
            "policy": "可配置运维策略，不是数据集失效判据",
        },
    }


def _csv_summary(path: Path) -> list[dict]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(line for line in handle if not line.startswith("#")))
    except OSError:
        return []


def _evidence() -> dict:
    gate2 = _read_json(QA_FILES["gate2"], {})
    gate3 = _read_json(QA_FILES["gate3"], {})
    t3c = _read_json(QA_FILES["t3c"], {})
    data_version = _read_json(QA_FILES["data_version"], {})
    route_ledger = _read_json(QA_FILES["route_ledger"], {})
    output_safety = _read_json(QA_FILES["output_safety"], {})
    data_freeze = _read_json(QA_FILES["data_freeze"], {})
    return {
        "gate2": gate2,
        "gate3": gate3,
        "t3c": t3c,
        "data_version": {
            "version_id": data_version.get("version_id"),
            "pass": data_version.get("pass"),
            "holdout_manifest": data_version.get("holdout_manifest", {}),
            "isolation": data_version.get("isolation", {}),
            "lut_manifest": data_version.get("lut_manifest", {}),
        },
        "t2a_bat": _csv_summary(RESULTS / "tables" / "t2a_bat_full.csv"),
        "t2a_rw": _csv_summary(RESULTS / "tables" / "t2a_rw_full.csv"),
        "t1_validation": {
            "bat": _csv_summary(T1_EVIDENCE_TABLES["bat"]),
            "rwa": _csv_summary(T1_EVIDENCE_TABLES["rwa"]),
        },
        "uncertainty": _csv_summary(RESULTS / "tables" / "uncertainty_coverage.csv"),
        "deployment": _csv_summary(RESULTS / "tables" / "t3c_size_acc_latency.csv"),
        "route": route_ledger.get("production_route", {}),
        "output_safety": output_safety,
        "data_freeze": {
            "pass": data_freeze.get("pass"),
            "status": data_freeze.get("status"),
            "source_count": data_freeze.get("source_count"),
        },
        "competition": _competition_evidence(),
    }


def load_mock() -> dict:
    if MOCK_JSON.exists():
        with MOCK_JSON.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    spec = importlib.util.spec_from_file_location("_gen_mock_rt", HERE / "mock" / "gen_mock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_mock()


def _mock_payload(policy: dict, reason: str) -> dict:
    mock = load_mock()
    mock_samples: list[dict[str, Any]] = []
    for raw_sample in mock.get("samples", []):
        if not isinstance(raw_sample, dict):
            continue
        sample = dict(raw_sample)
        match = SAMPLE_RE.match(str(sample.get("sample_id", "")))
        if match:
            orbit_state = _gmat_orbit_state(match.group("orbit"), int(match.group("beta")))
            if orbit_state is not None:
                sample["orbit_state"] = orbit_state
        mock_samples.append(sample)
    return {
        "schema": "cockpit-2",
        "source": "mock",
        "source_state": {"available": True, "degraded": True, "reason": reason},
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "discipline": DISCIPLINE,
        "config": policy,
        "channel_meta": CHANNEL_META,
        "samples": mock_samples,
        "telemetry": mock.get("telemetry", {}),
        "predictions": {},
        "evidence": _evidence(),
        "origins": {"mode": "mock", "explicit_opt_in": True},
        "route": _evidence().get("route", {}),
        "output_safety": _evidence().get("output_safety", {}),
    }


def _embedded_examples() -> dict[str, Any]:
    """Build the deterministic, explicitly-labelled replay examples.

    These examples are not competition evidence and never participate in
    metrics or model selection.  They exist so the reviewer-facing replay
    surface remains usable when a competition result snapshot contains only
    prediction CSVs and no raw MAT telemetry.
    """
    mock = load_mock()
    samples: list[dict[str, Any]] = []
    telemetry = mock.get("telemetry", {}) if isinstance(mock.get("telemetry"), dict) else {}
    predictions: dict[str, dict[str, Any]] = {}
    for raw_sample in mock.get("samples", []):
        if not isinstance(raw_sample, dict):
            continue
        sample_id = raw_sample.get("sample_id")
        match = SAMPLE_RE.match(str(sample_id))
        if not match or sample_id not in telemetry:
            continue
        line = "bat" if match.group("prefix") == "BAT" else "rw"
        sample = dict(raw_sample)
        sample.update({
            "orbit": match.group("orbit"),
            "beta_deg": int(match.group("beta")),
            "health_level": f"H{match.group('h')}",
            "load_level": f"L{match.group('l')}",
            "line": line,
            "line_label": _PUBLIC_COMPONENT_LABELS[line],
            "failure_mode": FAILURE_MODES.get(int(raw_sample.get("failure_mode_code", 0)), "未标注"),
            "example": True,
            "example_label": "项目内置示例（只读）",
            "provenance": {"sim_model": "项目内置确定性演示数据"},
            "orbit_state": _gmat_orbit_state(match.group("orbit"), int(match.group("beta"))),
        })
        samples.append(sample)
        run_id = raw_sample.get("run_id")
        run = (mock.get("runs") or {}).get(run_id, {}) if isinstance(mock.get("runs"), dict) else {}
        rows = []
        for raw_row in run.get("preds", []) if isinstance(run, dict) else []:
            if raw_row.get("unit_id") != sample_id:
                continue
            rows.append({
                "t": raw_row.get("t"),
                "y_true": raw_row.get("y_true_rul"),
                "p10": raw_row.get("p10"),
                "p50": raw_row.get("p50", raw_row.get("y_pred_rul")),
                "p90": raw_row.get("p90"),
                "display_estimate": raw_row.get("y_pred_rul"),
                "raw_estimate": raw_row.get("y_pred_rul"),
                "interval_order_valid": True,
                "boundary_adjusted": False,
            })
        predictions[str(sample_id)] = {"time_unit": "days", "rows": rows}
    return {"samples": samples, "telemetry": telemetry, "predictions": predictions}


def build_payload() -> dict:
    policy = _policy()
    competition = _load_competition_real(policy)
    if competition is not None:
        samples, telemetry, predictions, pred_origin = competition
        counts = {
            "bat": sum(sample["line"] == "bat" for sample in samples),
            "rw": sum(sample["line"] == "rw" for sample in samples),
        }
        integrity = _integrity_summary(samples, pred_origin)
        return {
            "schema": "cockpit-2",
            "source": "results",
            "source_state": {
                "available": True,
                "degraded": False,
                "reason": "当前读取竞赛 PyTorch 预测快照；该快照只含结构化预测结果，没有原始遥测回放。",
            },
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "discipline": DISCIPLINE,
            "contract_refs": ["competition manifest", "validation/holdout prediction receipts", "prediction CSV"],
            "config": policy,
            "channel_meta": CHANNEL_META,
            "samples": samples,
            "telemetry": telemetry,
            "predictions": predictions,
            "evidence": _evidence(),
            "integrity": integrity,
            "origins": {
                "mode": "competition-holdout",
                "matched_samples": counts,
                "prediction": pred_origin,
                "competition_result_root": str(_competition_result_root()),
                "mock_used": False,
            },
            "route": _evidence().get("route", {}),
            "output_safety": _evidence().get("output_safety", {}),
            "examples": _embedded_examples(),
        }
    samples, telemetry, predictions, pred_origin = _load_real(policy)
    if samples:
        counts = {
            "bat": sum(sample["line"] == "bat" for sample in samples),
            "rw": sum(sample["line"] == "rw" for sample in samples),
        }
        integrity = _integrity_summary(samples, pred_origin)
        degraded = integrity["lut_version_mismatch_count"] > 0
        return {
            "schema": "cockpit-2",
            "source": "results",
            "source_state": {
                "available": True,
                "degraded": degraded,
                "reason": (
                    "部分回放样本的内嵌 LUT 版本与当前机读基线不同；"
                    "界面保留历史版本，不作重标记。"
                ) if degraded else None,
            },
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "discipline": DISCIPLINE,
            "contract_refs": ["results holdout predictions", "data/holdout replay artifacts"],
            "config": policy,
            "channel_meta": CHANNEL_META,
            "samples": samples,
            "telemetry": telemetry,
            "predictions": predictions,
            "evidence": _evidence(),
            "integrity": integrity,
            "origins": {
                "mode": "matched-holdout",
                "matched_samples": counts,
                "prediction": pred_origin,
                "holdout_root": str(HOLDOUT),
                "mock_used": False,
            },
            "route": _evidence().get("route", {}),
            "output_safety": _evidence().get("output_safety", {}),
        }

    reason = "未找到可配对的 holdout 遥测与预测产物"
    if os.environ.get("RUL_DASHBOARD_ALLOW_MOCK", "").strip() == "1":
        return _mock_payload(policy, reason)
    return {
        "schema": "cockpit-2",
        "source": "unavailable",
        "source_state": {"available": False, "degraded": True, "reason": reason},
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "discipline": DISCIPLINE,
        "config": policy,
        "channel_meta": CHANNEL_META,
        "samples": [],
        "telemetry": {},
        "predictions": {},
        "evidence": _evidence(),
        "origins": {"mode": "unavailable", "mock_used": False},
        "route": _evidence().get("route", {}),
        "output_safety": _evidence().get("output_safety", {}),
    }


# The raw adapter payload deliberately retains diagnostics needed by local
# verification.  Browser payloads use this explicit allow-list projection.
_PUBLIC_SOURCES = frozenset({"results", "mock", "unavailable"})
_PUBLIC_LINES = frozenset({"bat", "rw"})
_PUBLIC_TIME_UNITS = frozenset({"cycles", "days"})
_PUBLIC_TEXT_RE = re.compile(
    r"(?ix)(?:"
    r"(?:^|[\s:])(?:[a-z]:[\\/]|/(?:mnt|home|tmp|data|results|configs|scripts|outputs)(?:[\\/]|$))|"
    # Underscore is a separator for public text checks; reserved words inside
    # identifiers such as ``holdout_dataset_t2c`` must still be rejected.
    r"(?<![a-z0-9])(?:t[123](?:[abc])?|gate\d+|w\d+|holdout|checkpoint|manifest|preflight|receipt|seed)(?![a-z0-9])|"
    r"\b0[78]\d{2}-\d{2}\b|"
    r"(?<![a-z0-9])(?:run[_ ]?id|argv|debug|pytest|entrypoint|source[_ ]?path|log[_ ]?path)(?![a-z0-9])|"
    r"\b[\w.-]+\.(?:py|js|ts|tsx|json|yaml|yml|parquet|slx|mat|script)\b|"
    r"(?:^|[\s:])(?:\.{1,2}[\\/]|(?:sim|gmat|src|configs|results|scripts|data)[\\/])|"
    r"竞赛模型合同|数据合同|模型合同|输入契约|模型契约|合同|契约|封存|公开域|目标域|迁移|生产清单|随机种子|脚本名称|命令参数|开发入口|开发工具|开发期|开发|"
    r"--[A-Za-z]"
    r")"
)
_PUBLIC_SAMPLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PUBLIC_DEVELOPMENT_ID_RE = re.compile(
    r"(?i)(?:^|[_-])(?:t[123](?:[abc])?|gate\d+|w\d+)(?:[_-]|$)"
)
_PUBLIC_SCHEDULE_ID_RE = re.compile(r"\b0[78]\d{2}-\d{2}\b")
# Route IDs are private machine identities. Match them even when a backend
# diagnostic joins the component and ID with _, -, or :; \b alone misses
# underscores because Python treats _ as a word character.
_PUBLIC_ROUTE_ID_RE = re.compile(r"(?<![A-Za-z0-9])S(?:21|22)(?![A-Za-z0-9])", re.IGNORECASE)
_PUBLIC_BAT_ROUTE_RE = re.compile(r"(?<![A-Za-z0-9])BAT[\s_:-]+S22(?![A-Za-z0-9])", re.IGNORECASE)
_PUBLIC_RWA_ROUTE_RE = re.compile(r"(?<![A-Za-z0-9])(?:RWA|RW)[\s_:-]+S21(?![A-Za-z0-9])", re.IGNORECASE)


def _public_number(value: Any) -> float | None:
    """Return only a finite number; malformed values remain unavailable."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _public_nonnegative_number(value: Any) -> float | None:
    number = _public_number(value)
    return number if number is not None and number >= 0.0 else None


def _public_ratio(value: Any) -> float | None:
    number = _public_number(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _public_count(value: Any) -> int | None:
    number = _public_number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _public_flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _public_text(value: Any, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit or _PUBLIC_TEXT_RE.search(text):
        return None
    return text


def _public_simulation_model_identity(value: Any) -> str | None:
    """Project model identity to public component wording without route codes."""
    if not isinstance(value, str):
        return None
    if _PUBLIC_BAT_ROUTE_RE.search(value):
        return _PUBLIC_MODEL_NAMES["bat"]
    if _PUBLIC_RWA_ROUTE_RE.search(value):
        return _PUBLIC_MODEL_NAMES["rwa"]
    text = _PUBLIC_SCHEDULE_ID_RE.sub("", value)
    text = re.sub(r"[（(]\s*[）)]", "", text)
    if _PUBLIC_ROUTE_ID_RE.search(text):
        return None
    return _public_text(text.strip(" _-/"), limit=160)


def _public_series(value: Any) -> list[float | None]:
    if not isinstance(value, (list, tuple)):
        return []
    # Keep position alignment intact when an invalid source value is present.
    return [_public_number(item) for item in value]


def _public_channel_meta(raw: Any) -> dict[str, dict[str, str]]:
    source = raw if isinstance(raw, dict) else {}
    public: dict[str, dict[str, str]] = {}
    for key in CHANNEL_META:
        item = source.get(key)
        if not isinstance(item, dict):
            continue
        label = _public_text(item.get("label"), limit=80)
        unit = _public_text(item.get("unit"), limit=40)
        if label is not None and unit is not None:
            public[key] = {"label": label, "unit": unit}
    return public


def _public_margin(raw: Any) -> dict[str, dict[str, float | str]]:
    source = raw if isinstance(raw, dict) else {}
    public: dict[str, dict[str, float | str]] = {}
    for line in _PUBLIC_LINES:
        value = source.get(line)
        if not isinstance(value, dict):
            continue
        amount = _public_number(value.get("value"))
        unit = _public_text(value.get("unit"), limit=40)
        if amount is not None and unit is not None:
            public[line] = {"value": amount, "unit": unit}
    return public


def _public_config(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "maintenance_margin": _public_margin(source.get("maintenance_margin")),
        "playback_seconds": _public_number(source.get("playback_seconds")),
    }


def _public_sample_id(value: Any) -> str | None:
    if not isinstance(value, str) or not _PUBLIC_SAMPLE_ID_RE.fullmatch(value):
        return None
    if _PUBLIC_DEVELOPMENT_ID_RE.search(value):
        return None
    return value


def _public_lut_comparison(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    state = source.get("state")
    if state not in {"current", "role-separated", "mismatch", "unknown"}:
        state = "unknown"
    notes = {
        "current": "样本内嵌版本与当前机读基线一致。",
        "role-separated": "样本保留生成时版本；当前基线用于不同数据角色，不作版本冲突判定。",
        "mismatch": "样本内嵌版本与当前机读基线不同；历史版本信息未被改写。",
        "unknown": "版本信息不足，未作一致性推断。",
    }

    def version(raw_version: Any) -> dict[str, str]:
        item = raw_version if isinstance(raw_version, dict) else {}
        result: dict[str, str] = {}
        for key in ("plan", "version"):
            value = _public_text(item.get(key), limit=100)
            if value is not None:
                result[key] = value
        return result

    active = source.get("active") if isinstance(source.get("active"), dict) else {}
    identity = active.get("identity") if isinstance(active.get("identity"), dict) else {}
    public_identity: dict[str, str] = {}
    for key in ("romax_product", "romax_version", "bearing_catalog_entry"):
        value = _public_text(identity.get(key), limit=160)
        if value is not None:
            public_identity[key] = value
    public_active = version(active)
    if public_identity:
        public_active["identity"] = public_identity
    return {
        "state": state,
        "matches": _public_flag(source.get("matches")),
        "embedded": version(source.get("embedded")),
        "active": public_active,
        "note": notes[state],
    }


def _public_samples(raw: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    values = raw if isinstance(raw, list) else []
    samples: list[dict[str, Any]] = []
    lines: dict[str, str] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        sample_id = _public_sample_id(item.get("sample_id"))
        line = item.get("line")
        if sample_id is None or line not in _PUBLIC_LINES or sample_id in lines:
            continue
        sample: dict[str, Any] = {"sample_id": sample_id, "line": line}
        for key in ("dataset_id", "orbit", "health_level", "load_level", "failure_mode"):
            value = _public_text(item.get(key), limit=160)
            if value is not None:
                sample[key] = value
        # Component labels are part of the public vocabulary.  Do not trust a
        # historical shorthand supplied by an artifact when the machine line
        # identity gives us an unambiguous label.
        sample["line_label"] = _PUBLIC_COMPONENT_LABELS[line]
        if item.get("example") is True:
            sample["example"] = True
            sample["example_label"] = "项目内置示例（只读）"
        for key in ("beta_deg", "failure_time_days"):
            value = _public_number(item.get(key))
            if value is not None:
                sample[key] = value
        # Do not infer a spacecraft state from a display orbit label in the
        # public projection.  Only a state explicitly attached to the source
        # sample is a fact; missing state remains missing and the cockpit then
        # fails closed instead of drawing a guessed track.
        orbit_state = item.get("orbit_state") if isinstance(item.get("orbit_state"), dict) else None
        if isinstance(orbit_state, dict):
            allowed = (
                "schema", "orbit_id", "source", "reference_frame", "reference_radius_km",
                "semi_major_axis_km", "eccentricity", "inclination_deg", "raan_deg", "arg_periapsis_deg",
                "true_anomaly_deg", "period_min", "epoch_utc",
            )
            public_state = {}
            for key in allowed:
                value = orbit_state.get(key)
                if key == "epoch_utc" or key in {"schema", "orbit_id", "source", "reference_frame"}:
                    text = _public_text(value, limit=160)
                    if text is not None:
                        public_state[key] = text
                elif _public_number(value) is not None:
                    public_state[key] = _public_number(value)
            if public_state.get("semi_major_axis_km") and public_state.get("eccentricity") is not None:
                sample["orbit_state"] = public_state
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        sim_model = _public_simulation_model_identity(provenance.get("sim_model"))
        if sim_model is not None:
            sample["provenance"] = {"sim_model": sim_model}
        sample["lut_comparison"] = _public_lut_comparison(item.get("lut_comparison"))
        samples.append(sample)
        lines[sample_id] = line
    return samples, lines


def _public_telemetry(raw: Any, sample_lines: dict[str, str]) -> dict[str, dict[str, Any]]:
    source = raw if isinstance(raw, dict) else {}
    public: dict[str, dict[str, Any]] = {}
    for sample_id, line in sample_lines.items():
        item = source.get(sample_id)
        if not isinstance(item, dict):
            continue
        channels = item.get("channels") if isinstance(item.get("channels"), dict) else {}
        public_channels = {
            f"{line}.{name}": _public_series(channels.get(f"{line}.{name}"))
            for name in CHANNELS[line]
            if f"{line}.{name}" in channels
        }
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        public_labels = {
            key: _public_series(labels.get(key))
            for key in ("hi", "rul_days", "fail") if key in labels
        }
        public[sample_id] = {
            "t_days": _public_series(item.get("t_days")),
            "time_unit": item.get("time_unit") if item.get("time_unit") in _PUBLIC_TIME_UNITS else "days",
            "channels": public_channels,
            "labels": public_labels,
        }
    return public


def _public_prediction_row(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    numeric_fields = {
        "t": "t",
        "y_true": "y_true",
        "p10": "p10",
        "p50": "p50",
        "p90": "p90",
        "ensemble_std": "ensemble_std",
        "display_estimate": "y_pred_rul",
        "raw_estimate": "y_pred_rul_raw",
        "supervised_estimate": "t1",
        "adapted_estimate": "t2",
    }
    public = {name: _public_number(source.get(source_name)) for name, source_name in numeric_fields.items()}
    public["boundary_adjusted"] = _public_flag(source.get("rul_output_clamped"))
    public["interval_order_valid"] = _public_flag(source.get("interval_order_valid"))
    return public


def _public_predictions(raw: Any, sample_lines: dict[str, str]) -> dict[str, dict[str, Any]]:
    source = raw if isinstance(raw, dict) else {}
    public: dict[str, dict[str, Any]] = {}
    for sample_id in sample_lines:
        item = source.get(sample_id)
        if not isinstance(item, dict):
            continue
        unit = item.get("time_unit")
        rows = item.get("rows")
        if unit not in _PUBLIC_TIME_UNITS or not isinstance(rows, list):
            continue
        public[sample_id] = {
            "time_unit": unit,
            "rows": [_public_prediction_row(row) for row in rows if isinstance(row, dict)],
        }
    return public


def _public_examples(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    samples, sample_lines = _public_samples(source.get("samples"))
    return {
        "source": "embedded-example",
        "source_state": {"available": bool(samples), "degraded": False},
        "samples": samples,
        "telemetry": _public_telemetry(source.get("telemetry"), sample_lines),
        "predictions": _public_predictions(source.get("predictions"), sample_lines),
    }


def _public_prediction_quality(raw: Any) -> dict[str, dict[str, Any]]:
    source = raw if isinstance(raw, dict) else {}
    public: dict[str, dict[str, Any]] = {}
    line_names = {"bat": "battery", "rw": "reaction_wheel", "rwa": "reaction_wheel"}
    for raw_line, public_line in line_names.items():
        item = source.get(raw_line)
        if not isinstance(item, dict) or public_line in public:
            continue
        fields = {
            "invalid_rows": "invalid_interval_rows",
            "interval_order_issues": "quantile_crossings",
            "missing_supervised_reference": "missing_t1",
            "missing_adapted_reference": "missing_t2",
            "boundary_fields_available": "safety_fields_available",
            "raw_boundary_exceedance_rows": "raw_bounds_violation_rows",
            "boundary_adjusted_rows": "raw_clamp_rows",
        }
        public[public_line] = {
            name: (_public_flag(item.get(raw_name)) if raw_name == "safety_fields_available"
                   else _public_count(item.get(raw_name)))
            for name, raw_name in fields.items()
        }
    return public


def _public_integrity(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    comparison_fields = {
        "mismatch_count": "lut_version_mismatch_count",
        "unknown_count": "lut_version_unknown_count",
        "role_separated_count": "lut_role_separated_count",
    }
    prediction = source.get("prediction") if isinstance(source.get("prediction"), dict) else {}
    return {
        "version_comparison": {
            name: _public_count(source.get(raw_name))
            for name, raw_name in comparison_fields.items()
        },
        "prediction_quality": _public_prediction_quality(prediction),
        "data_roles": {
            "telemetry": "仿真输出回放",
            "reference_labels": "仿真参考标签，仅用于回放解释",
            "prediction": "模型估计与校准区间",
        },
    }


def _public_output_safety(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    state = source.get("status")
    if state not in {"complete", "incomplete", "pending", "unavailable"}:
        state = "unavailable"
    return {
        "available": bool(source),
        "state": state,
        "verified": _public_flag(source.get("pass")),
    }


def _public_evaluation_metrics(raw: Any) -> dict[str, dict[str, Any]]:
    source = raw if isinstance(raw, dict) else {}
    competition = source.get("competition") if isinstance(source.get("competition"), dict) else {}
    if competition.get("available") is True:
        public: dict[str, dict[str, Any]] = {}
        lines = competition.get("lines") if isinstance(competition.get("lines"), dict) else {}
        for raw_line, public_line, label, unit in (
            ("bat", "battery", _PUBLIC_COMPONENT_LABELS["bat"], "循环"),
            ("rwa", "reaction_wheel", _PUBLIC_COMPONENT_LABELS["rw"], "天"),
        ):
            item = lines.get(raw_line)
            if not isinstance(item, dict):
                continue
            validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
            holdout = item.get("holdout") if isinstance(item.get("holdout"), dict) else {}
            public[public_line] = {
                "label": label,
                "unit": unit,
                "validation_rmse": _public_nonnegative_number(validation.get("rmse")),
                "validation_mae": _public_nonnegative_number(validation.get("mae")),
                "validation_count": _public_count(validation.get("n_windows")),
                "validation_units": _public_count(validation.get("n_units")),
                "evaluation_rmse": _public_nonnegative_number(holdout.get("rmse")),
                "evaluation_mae": _public_nonnegative_number(holdout.get("mae")),
                "evaluation_count": _public_count(holdout.get("n_windows")),
                "evaluation_units": _public_count(holdout.get("n_units")),
                "evaluation_scope": "航天数据独立测试集",
                "framework": _public_text(competition.get("framework"), limit=40),
                "model_version_hash": _public_text(competition.get("manifest_sha256"), limit=100),
            }
        if public:
            return public
    mapping = (
        ("bat", "battery", _PUBLIC_COMPONENT_LABELS["bat"], "循环"),
        ("rw", "reaction_wheel", _PUBLIC_COMPONENT_LABELS["rw"], "天"),
        ("rwa", "reaction_wheel", _PUBLIC_COMPONENT_LABELS["rwa"], "天"),
    )
    public: dict[str, dict[str, Any]] = {}
    for raw_line, public_line, label, unit in mapping:
        item = source.get(raw_line)
        if not isinstance(item, dict) or public_line in public:
            continue
        public[public_line] = {
            "label": label,
            "unit": unit,
            "supervised_rmse": _public_nonnegative_number(item.get("mean_t1_rmse")),
            "adaptation_rmse": _public_nonnegative_number(item.get("mean_t2_rmse")),
            "rmse_change": _public_number(item.get("transfer_gain_rmse")),
            "rmse_change_definition": (
                "监督基线 RMSE 减去跨域适配 RMSE；这是带部件寿命单位的绝对差值，"
                "不是相对百分比。"
            ),
            "evaluation_rmse": _public_nonnegative_number(item.get("holdout_rmse")),
            "evaluation_mae": _public_nonnegative_number(item.get("holdout_mae")),
            "coverage_90": _public_ratio(item.get("coverage_90")),
            "mpiw_90": _public_nonnegative_number(item.get("mpiw_90")),
            "n_calibration": _public_count(item.get("n_calibration")),
            "n_evaluation": _public_count(item.get("n_holdout")),
            "n_members": _public_count(item.get("n_members")),
            "n_transfer_members": _public_count(item.get("n_transfer_seeds")),
            "qhat": _public_nonnegative_number(item.get("qhat")),
        }
    return public


def _machine_result_number(value: Any) -> float | None:
    """Parse a finite numeric cell from a trusted machine-generated result table."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _machine_result_seed(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        seed = value
    elif isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return None
        seed = int(number)
    elif isinstance(value, str) and re.fullmatch(r"\d{1,12}", value.strip()):
        seed = int(value.strip())
    else:
        return None
    return seed if seed >= 0 else None


def _machine_result_adopted(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() in {"true", "1"}


def _public_validation_metric(raw: Any, metric: str, label: str) -> dict[str, Any] | None:
    rows = raw if isinstance(raw, list) else []
    per_seed: dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not _machine_result_adopted(row.get("adopted")):
            continue
        if str(row.get("split", "")).strip().lower() != "val":
            continue
        seed = _machine_result_seed(row.get("seed"))
        value = _machine_result_number(row.get(metric))
        if seed is None or value is None or not 0.0 <= value <= 1.0:
            continue
        previous = per_seed.get(seed)
        if previous is not None and previous != value:
            # More than one adopted result for one seed is ambiguous; fail closed.
            return None
        per_seed[seed] = value
    if not per_seed:
        return None
    values = list(per_seed.values())
    try:
        mean = math.fsum(values) / len(values)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(mean):
        return None
    mean = round(mean, 12)
    return {
        "label": label,
        "mean": mean,
        "minimum": min(values),
        "maximum": max(values),
        "seed_count": len(per_seed),
    }


def _public_validation_components(raw: Any, metric: str) -> dict[str, dict[str, Any]]:
    source = raw if isinstance(raw, dict) else {}
    definitions = (
        ("battery", _PUBLIC_COMPONENT_LABELS["bat"], source.get("bat")),
        (
            "reaction_wheel",
            _PUBLIC_COMPONENT_LABELS["rw"],
            source.get("rwa") if isinstance(source.get("rwa"), list) else source.get("rw"),
        ),
    )
    public: dict[str, dict[str, Any]] = {}
    for component, label, rows in definitions:
        summary = _public_validation_metric(rows, metric, label)
        if summary is not None:
            public[component] = summary
    return public


def _public_robustness_scope(raw: Any) -> tuple[str | None, int | None]:
    source = raw if isinstance(raw, dict) else {}
    line_names = _PUBLIC_COMPONENT_LABELS
    baseline_names = {"t1": "监督基线", "t2": "跨域适配基线"}
    split_names = {"val": "验证集", "holdout": "独立测试集"}

    def translated_list(value: Any, names: dict[str, str]) -> list[str]:
        if not isinstance(value, list):
            return []
        translated: list[str] = []
        for item in value:
            label = names.get(str(item).strip().lower())
            if label is not None and label not in translated:
                translated.append(label)
        return translated

    parts: list[str] = []
    lines = translated_list(source.get("lines"), line_names)
    if lines:
        parts.append(f"部件：{'、'.join(lines)}")
    baselines = translated_list(source.get("baselines"), baseline_names)
    if baselines:
        parts.append(f"比较对象：{'、'.join(baselines)}")
    split = split_names.get(str(source.get("split", "")).strip().lower())
    if split is not None:
        parts.append(f"数据划分：{split}")

    seeds = source.get("seeds")
    if isinstance(seeds, list):
        valid_seeds = {_machine_result_seed(seed) for seed in seeds}
        valid_seeds.discard(None)
        seed_count = len(valid_seeds) if valid_seeds else None
    else:
        seed_count = _public_count(source.get("n_seeds"))
    return ("；".join(parts) if parts else None), seed_count


def _public_evaluation_dimensions(
    metrics: dict[str, dict[str, Any]],
    t1_validation: Any,
    robustness: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    error_fields = (
        "supervised_rmse", "adaptation_rmse", "rmse_change", "holdout_rmse",
        "holdout_mae", "coverage_90", "mpiw_90", "qhat",
    )
    prediction_error_available = any(
        item.get(field) is not None
        for item in metrics.values()
        for field in error_fields
    )
    trend_components = _public_validation_components(
        t1_validation, "alpha_lambda_acc@0.2"
    )
    earliness_components = _public_validation_components(t1_validation, "horizon@0.2")
    stability_available = robustness.get("positive_gain") is not None
    return {
        "prediction_error": {
            "available": prediction_error_available,
            "scope": (
                "预测误差与 90% 区间校准（本地独立测试结果）"
                if prediction_error_available else None
            ),
            "summary": (
                "各部件按自身寿命单位报告 RMSE、MAE 与区间统计；RMSE 改善量是绝对"
                "量纲差值，不是百分比。"
                if prediction_error_available else "当前未读取到本地独立测试误差统计。"
            ),
        },
        "trend_consistency": {
            "available": bool(trend_components),
            "scope": "跨寿命阶段一致性（基础模型验证集）" if trend_components else None,
            "summary": (
                "汇总正式采用模型在不同寿命阶段落入真值正负 20% 容差带的比例。"
                if trend_components else "当前未读取到可汇总的正式基础模型验证行。"
            ),
            "components": trend_components,
        },
        "stability": {
            "available": stability_available,
            "scope": robustness.get("scope") if stability_available else None,
        "member_count": robustness.get("seed_count") if stability_available else None,
            "summary": (
                robustness.get("summary")
                if stability_available else "当前未读取到可判定的稳定性结论。"
            ),
        },
        "earliness": {
            "available": bool(earliness_components),
            "scope": "相对提前视界（基础模型验证集）" if earliness_components else None,
            "summary": (
                "汇总正式采用模型从多早开始持续落入真值正负 20% 容差带的相对寿命"
                "比例；不是绝对循环数或天数。"
                if earliness_components else "当前未读取到可汇总的正式基础模型验证行。"
            ),
            "components": earliness_components,
        },
    }


def _public_decision(value: Any) -> str | bool | None:
    if isinstance(value, bool):
        return value
    return value if value in {"appendix", "production", "pass", "fail"} else None


def _public_evidence(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    evaluation = source.get("gate2") if isinstance(source.get("gate2"), dict) else {}
    robustness_source = source.get("gate3") if isinstance(source.get("gate3"), dict) else {}
    deployment = source.get("t3c") if isinstance(source.get("t3c"), dict) else {}
    data_version = source.get("data_version") if isinstance(source.get("data_version"), dict) else {}
    data_freeze = source.get("data_freeze") if isinstance(source.get("data_freeze"), dict) else {}
    t1_validation = source.get("t1_validation")
    deployment_measurements = (
        deployment.get("measurements")
        if isinstance(deployment.get("measurements"), dict) else {}
    )
    robust_positive_gain = _public_flag(robustness_source.get("robust_positive_gain"))
    if robust_positive_gain is True:
        robustness_summary = "独立验证中观察到跨样本、跨独立模型成员的稳定正向改善。"
    elif robust_positive_gain is False:
        robustness_summary = "独立验证中未观察到跨样本、跨独立模型成员的稳定正向改善；该模型保留为补充分析，不纳入当前生产推理。"
    else:
        robustness_summary = None
    robustness_scope, robustness_seed_count = _public_robustness_scope(
        robustness_source.get("scope")
    )
    evaluation_metrics = evaluation.get("metrics") if isinstance(evaluation.get("metrics"), dict) else {}
    metrics = _public_evaluation_metrics({**evaluation_metrics, "competition": source.get("competition")})
    public_robustness = {
        "decision": _public_decision(robustness_source.get("decision")),
        "positive_gain": robust_positive_gain,
        "scope": robustness_scope,
        "seed_count": robustness_seed_count,
        "summary": robustness_summary,
    }
    competition_source = source.get("competition") if isinstance(source.get("competition"), dict) else {}
    public_competition: dict[str, Any] = {
        "available": competition_source.get("available") is True,
        "framework": _public_text(competition_source.get("framework"), limit=40),
        "framework_version": _public_text(competition_source.get("framework_version"), limit=40),
        "model_version_hash": _public_text(competition_source.get("manifest_sha256"), limit=100),
        "config_sha256": _public_text(competition_source.get("config_sha256"), limit=100),
        "implementation_sha256": _public_text(competition_source.get("implementation_sha256"), limit=100),
        "validation_record": competition_source.get("validation") is True,
        "evaluation_record": competition_source.get("holdout") is True,
    }
    result = {
        "evaluation": {
            "metrics": metrics,
            "dimensions": _public_evaluation_dimensions(
                metrics, t1_validation, public_robustness
            ),
            "organizer_boundary": {
                "public_data_available": False,
                "local_result_equivalent": False,
                "summary": (
                    "本页仅报告项目内当前可读取的结构化验证产物；未纳入验证的"
                    "数据、场景与组件不在结论范围内，也不能由页面外推。"
                ),
            },
        },
        "robustness": public_robustness,
        "deployment": {
            "available": bool(deployment),
            "verified": _public_flag(deployment.get("pass")),
            "model_size_mb": _public_number(
                deployment_measurements.get("model_size_mb", deployment.get("model_size_mb"))
            ),
            "latency_p50_ms": _public_number(
                deployment_measurements.get("latency_p50_ms", deployment.get("latency_ms"))
            ),
            "latency_p95_ms": _public_number(deployment_measurements.get("latency_p95_ms")),
            "peak_memory_mb": _public_number(deployment_measurements.get("peak_memory_mb")),
            "conversion_drift_pct": _public_number(
                deployment_measurements.get("onnx_conversion_drift_pct")
            ),
        },
        "data_state": {
            "available": bool(data_version),
            "verified": _public_flag(data_version.get("pass")),
            "source_count": _public_count(data_freeze.get("source_count")),
            "frozen": _public_flag(data_freeze.get("pass")),
        },
    }
    if public_competition["available"]:
        result["competition_snapshot"] = public_competition
    return result


def _public_production_model() -> dict[str, Any]:
    components = {}
    for line in ("bat", "rw"):
        components[line] = {
            "model_name": _PUBLIC_MODEL_NAMES[line],
            "component_name": _PUBLIC_COMPONENT_LABELS[line],
            "framework": "PyTorch",
            "n_members": 3,
            "selection_method": "三个独立模型结果取中位数",
            "point_prediction_method": "三个独立模型结果取中位数",
            "range_method": "三个独立模型形成经验预测范围",
            "monotonicity_adjustment": "按退化方向校正预测曲线",
        }
    return {
        "status": "validated",
        "framework": "PyTorch",
        "modelVersion_sha256": _PUBLIC_MODEL_VERSION,
        "components": components,
    }


def _public_source_state(source: str, raw: Any) -> dict[str, Any]:
    state = raw if isinstance(raw, dict) else {}
    available = _public_flag(state.get("available"))
    degraded = _public_flag(state.get("degraded"))
    if source == "mock":
        reason = "当前展示项目内置示例，仅供只读回放，不代表本次上传数据或在线测量。"
    elif source == "unavailable":
        reason = "当前没有可读取的独立测试结果。"
    elif degraded:
        reason = "部分版本信息无法与当前机读基线完全对应，历史记录未被改写。"
    else:
        reason = None
    return {
        "available": available if available is not None else source != "unavailable",
        "degraded": degraded if degraded is not None else source != "results",
        "reason": reason,
    }


def _public_origins(source: str, raw: Any) -> dict[str, Any]:
    values = raw if isinstance(raw, dict) else {}
    matched = values.get("matched_samples") if isinstance(values.get("matched_samples"), dict) else {}
    replay_kind = {
        "results": "独立测试结果只读回放",
        "mock": "项目内置示例只读回放",
        "unavailable": "当前无可读取回放",
    }[source]
    return {
        "replay_kind": replay_kind,
        "matched_samples": {
            "battery": _public_count(matched.get("bat")),
            "reaction_wheel": _public_count(matched.get("rw")),
        },
    }


def public_payload(raw_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the bounded, browser-safe cockpit view of a raw adapter payload."""
    raw = build_payload() if raw_payload is None else raw_payload
    source_data = raw if isinstance(raw, dict) else {}
    source = source_data.get("source")
    if source not in _PUBLIC_SOURCES:
        source = "unavailable"
    samples, sample_lines = _public_samples(source_data.get("samples"))
    generated_utc = _public_text(source_data.get("generated_utc"), limit=40)
    return {
        "schema": "cockpit-2",
        "source": source,
        "source_state": _public_source_state(source, source_data.get("source_state")),
        "generated_utc": generated_utc,
        "discipline": "原始数据事实优先；页面仅展示可核验的只读数据。",
        "config": _public_config(source_data.get("config")),
        "channel_meta": _public_channel_meta(source_data.get("channel_meta")),
        "samples": samples,
        "telemetry": _public_telemetry(source_data.get("telemetry"), sample_lines),
        "predictions": _public_predictions(source_data.get("predictions"), sample_lines),
        "integrity": _public_integrity(source_data.get("integrity")),
        "origins": _public_origins(source, source_data.get("origins")),
        "output_safety": _public_output_safety(source_data.get("output_safety")),
        "production_model": _public_production_model(),
        "evidence": _public_evidence(source_data.get("evidence")),
        "examples": _public_examples(source_data.get("examples")),
    }
