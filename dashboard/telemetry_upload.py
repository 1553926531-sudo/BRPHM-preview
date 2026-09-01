"""Fail-closed ingestion and inference for committee telemetry tables.

Long tables keep the original one-observation-per-row contract.  Wide tables
are deterministically reduced to the same internal long records: declared
machine channels remain eligible for the frozen model, while other finite
numeric telemetry columns keep their source labels and are ignored. Optional
context and target columns are retained as bounded provenance summaries but
never enter a frozen model. Numeric time units come from an explicit header or
a user selection, never from filename or value-shape guessing. Uploaded files
are never written to disk.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import secrets
import tarfile
import threading
import time
import unicodedata
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import numpy as np
try:
    import py7zr
except ImportError:  # optional archive reader; core table formats remain available
    py7zr = None
try:
    import rarfile
except ImportError:  # optional archive reader; core table formats remain available
    rarfile = None


SCHEMA = "brphm-telemetry-prediction-1.3"
EXAMPLES_SCHEMA = "brphm-telemetry-examples-1.0"
MAX_FILES = 32
MAX_COLUMNS = 32
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_RECORDS_PER_FILE = 250_000
MAX_WINDOWS_PER_FILE = 5_000
RESULT_TTL_SECONDS = 60 * 60
MAX_STORED_BATCHES = 20
# The replay trace is a bounded, lossless subset of the accepted source rows
# for display only.  It is deliberately separate from the prediction export
# and never participates in model preparation.
MAX_REPLAY_TRACE_POINTS = 8_000

# Deterministic examples keep browser and CLI guidance on identical legal
# shapes without adding large fixture files to the release bundle.
_EXAMPLE_OPTIONAL_FIELDS = ("component_id", "operating_condition", "degradation_state", "rul")
_EXAMPLE_FORMATS = (
    {"id": "csv", "extensions": [".csv"], "available": True, "label": "CSV"},
    {"id": "tsv", "extensions": [".tsv"], "available": True, "label": "TSV"},
    {"id": "txt", "extensions": [".txt"], "available": True, "label": "Delimited TXT"},
    {"id": "tab", "extensions": [".tab"], "available": True, "label": "TAB"},
    {"id": "dat", "extensions": [".dat"], "available": True, "label": "DAT"},
    {"id": "json", "extensions": [".json"], "available": True, "label": "JSON records"},
    {"id": "jsonl", "extensions": [".jsonl"], "available": True, "label": "JSONL records"},
    {"id": "xlsx", "extensions": [".xlsx"], "available": True, "label": "Excel"},
    {"id": "parquet", "extensions": [".parquet"], "available": True, "label": "Parquet"},
    {"id": "feather", "extensions": [".feather"], "available": True, "label": "Feather"},
    {"id": "arrow", "extensions": [".arrow"], "available": True, "label": "Arrow IPC"},
    {"id": "npz", "extensions": [".npz"], "available": True, "label": "NumPy NPZ"},
    {"id": "h5", "extensions": [".h5"], "available": True, "label": "HDF5"},
    {"id": "mat", "extensions": [".mat"], "available": True, "label": "MAT"},
    {"id": "zip", "extensions": [".zip"], "available": True, "label": "ZIP archive"},
    {"id": "tar.gz", "extensions": [".tar.gz"], "available": True, "label": "TAR.GZ archive"},
    {"id": "7z", "extensions": [".7z"], "available": False, "label": "7Z archive", "note": "上传解析支持 7Z；此服务不生成二进制 7Z 示例。"},
    {"id": "rar", "extensions": [".rar"], "available": False, "label": "RAR archive", "note": "上传解析支持 RAR；此服务不生成 RAR 示例。"},
    {"id": "xls", "extensions": [".xls", ".xlsm", ".xlsb", ".ods"], "available": False, "label": "Other spreadsheet", "note": "表格语义与 XLSX 相同；请另存为对应格式后上传。"},
)


def _example_columns(example_id: str, *, include_optional: bool) -> list[str]:
    if example_id.startswith("battery"):
        columns = ["cycle", "bat.capacity_ah", "bat.temp_mean_c", "bat.charge_time_s"]
    else:
        columns = ["time_s", "rw.speed_rpm", "rw.motor_current_a", "rw.bearing_temp_c"]
    return columns + (list(_EXAMPLE_OPTIONAL_FIELDS) if include_optional else [])


def _example_wide_rows(example_id: str) -> list[dict[str, Any]]:
    if example_id in {"battery-empty", "reaction-wheel-empty"}:
        return []
    if example_id in {"battery-single", "battery-complete"}:
        count = 60 if example_id == "battery-single" else 90
        include_optional = example_id == "battery-complete"
        rows: list[dict[str, Any]] = []
        for index in range(count):
            row: dict[str, Any] = {"cycle": index, "bat.capacity_ah": round(2.50 - 0.0035 * index, 6), "bat.temp_mean_c": round(25.0 + 0.035 * index, 6), "bat.charge_time_s": round(4200.0 - 3.0 * index, 6)}
            if include_optional:
                row.update({"component_id": "BAT-DEMO-001", "operating_condition": "nominal", "degradation_state": "demonstration-only", "rul": max(0, count - index)})
            rows.append(row)
        return rows
    if example_id in {"reaction-wheel-single", "reaction-wheel-complete"}:
        bins = 30 if example_id == "reaction-wheel-single" else 40
        include_optional = example_id == "reaction-wheel-complete"
        rows = []
        for bucket in range(bins):
            for offset in (0.0, 300.0):
                row = {"time_s": bucket * 574.0 + offset, "rw.speed_rpm": round(1200.0 - 1.8 * bucket + 0.02 * offset, 6), "rw.motor_current_a": round(0.42 + 0.002 * bucket + 0.0001 * offset, 6), "rw.bearing_temp_c": round(35.0 + 0.08 * bucket + 0.002 * offset, 6)}
                if include_optional:
                    row.update({"component_id": "RWA-DEMO-001", "operating_condition": "nominal", "degradation_state": "demonstration-only", "rul": max(0, bins - bucket)})
                rows.append(row)
        return rows
    raise KeyError(example_id)


def _example_long_rows(rows: Sequence[Mapping[str, Any]], example_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    time_key = "cycle" if example_id.startswith("battery") else "time_s"
    channels = ("bat.capacity_ah", "bat.temp_mean_c", "bat.charge_time_s") if example_id.startswith("battery") else ("rw.speed_rpm", "rw.motor_current_a", "rw.bearing_temp_c")
    columns = [time_key, "telemetry", "value"] + list(_EXAMPLE_OPTIONAL_FIELDS)
    long_rows: list[dict[str, Any]] = []
    for source in rows:
        for channel in channels:
            item: dict[str, Any] = {time_key: source[time_key], "telemetry": channel, "value": source[channel]}
            for field in _EXAMPLE_OPTIONAL_FIELDS:
                if field in source: item[field] = source[field]
            long_rows.append(item)
    return columns, long_rows


def telemetry_example_catalog() -> dict[str, Any]:
    definitions = (("battery-empty", "电池部件空白模板", "battery", "empty_template", 0, 0), ("reaction-wheel-empty", "反作用轮部件空白模板", "reaction_wheel", "empty_template", 0, 0), ("battery-single", "电池部件单点预测输入", "battery", "single_prediction", 60, 1), ("reaction-wheel-single", "反作用轮部件单点预测输入", "reaction_wheel", "single_prediction", 60, 1), ("battery-complete", "电池部件多点预测输入", "battery", "multi_prediction", 90, 7), ("reaction-wheel-complete", "反作用轮部件多点预测输入", "reaction_wheel", "multi_prediction", 80, 11))
    examples = []
    for example_id, title, component, kind, source_rows, prediction_points in definitions:
        columns = _example_columns(example_id, include_optional=kind == "multi_prediction")
        required = ["bat.capacity_ah", "bat.temp_mean_c", "bat.charge_time_s"] if component == "battery" else ["rw.speed_rpm", "rw.motor_current_a", "rw.bearing_temp_c"]
        minimum = "60 个从 0 开始连续的循环序号" if component == "battery" else "30 个连续 574 秒时间桶；示例每桶含两条原始观测"
        variants = []
        for fmt in _EXAMPLE_FORMATS:
            variants.append({"format": fmt["id"], "label": fmt["label"], "download": f"/api/telemetry/examples/{example_id}.{fmt['id']}", "layouts": ["wide", "long"]} if fmt["available"] else {"format": fmt["id"], "label": fmt["label"], "available": False, "note": fmt["note"]})
        examples.append({"id": example_id, "title": title, "component": component, "kind": kind, "default_layout": "wide", "columns": columns, "source_row_count": source_rows, "prediction_points": prediction_points, "required_channels": required, "display_only_fields": list(_EXAMPLE_OPTIONAL_FIELDS), "minimum_history": minimum, "download": f"/api/telemetry/examples/{example_id}.csv", "variants": variants})
    return {"schema": EXAMPLES_SCHEMA, "description": "示例仅用于说明合法表格形状；预测时只有 required_channels 进入模型，display_only_fields 仅作文件溯源或演示。", "layouts": {"wide": "每行一个时间点，已声明通道各占一列。", "long": "每行一条观测，字段为时间、telemetry、value。"}, "formats": [dict(item) for item in _EXAMPLE_FORMATS], "examples": examples}


def telemetry_example_content(example_id: str, fmt: str = "csv", layout: str = "wide") -> tuple[bytes, str, str]:
    if example_id not in {item["id"] for item in telemetry_example_catalog()["examples"]}: raise KeyError(example_id)
    if fmt not in {item["id"] for item in _EXAMPLE_FORMATS if item["available"]}: raise ValueError(f"format {fmt!r} is not generated by this service")
    if layout not in {"wide", "long"}: raise ValueError("layout must be wide or long")
    rows = _example_wide_rows(example_id); columns = _example_columns(example_id, include_optional=example_id.endswith("complete"))
    if layout == "long": columns, rows = _example_long_rows(rows, example_id)
    if fmt in {"csv", "tsv", "txt", "tab", "dat"}:
        delimiter = "\t" if fmt in {"tsv", "tab"} else ","; stream = io.StringIO(newline=""); writer = csv.DictWriter(stream, fieldnames=columns, delimiter=delimiter, extrasaction="ignore", lineterminator="\r\n"); writer.writeheader(); writer.writerows(rows)
        media = "text/tab-separated-values" if delimiter == "\t" else "text/csv"; return ("\ufeff" + stream.getvalue()).encode("utf-8"), f"brphm-{example_id}.{fmt}", f"{media}; charset=utf-8"
    if fmt in {"json", "jsonl"}:
        if fmt == "json":
            content = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
        else:
            content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows) or "\n"
        return content.encode("utf-8"), f"brphm-{example_id}.{fmt}", "application/json; charset=utf-8"
    stream = io.BytesIO()
    if fmt in {"xlsx", "parquet", "feather", "arrow"}:
        import pandas as pd
        frame = pd.DataFrame(rows, columns=columns)
        if fmt == "xlsx": frame.to_excel(stream, index=False); media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "parquet": frame.to_parquet(stream, index=False); media = "application/vnd.apache.parquet"
        else: frame.to_feather(stream); media = "application/vnd.apache.arrow.file"
        return stream.getvalue(), f"brphm-{example_id}.{fmt}", media
    if fmt == "npz":
        np.savez(stream, **{column: np.asarray([row.get(column, "") for row in rows]) for column in columns}); return stream.getvalue(), f"brphm-{example_id}.npz", "application/octet-stream"
    if fmt == "h5":
        import h5py
        with h5py.File(stream, "w") as handle:
            for column in columns:
                values = [row.get(column, "") for row in rows]
                if values and isinstance(values[0], str): handle.create_dataset(column, data=np.asarray(values, dtype="S256"))
                else: handle.create_dataset(column, data=np.asarray(values, dtype=np.float64))
        return stream.getvalue(), f"brphm-{example_id}.h5", "application/x-hdf5"
    if fmt == "mat":
        from scipy.io import savemat
        savemat(stream, {column: np.asarray([row.get(column, "") for row in rows]) for column in columns}); return stream.getvalue(), f"brphm-{example_id}.mat", "application/x-matlab-data"
    if fmt in {"zip", "tar.gz"}:
        csv_bytes, csv_name, _ = telemetry_example_content(example_id, "csv", layout)
        if fmt == "zip":
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive: archive.writestr(csv_name, csv_bytes)
            media = "application/zip"
        else:
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                info = tarfile.TarInfo(csv_name); info.size = len(csv_bytes); archive.addfile(info, io.BytesIO(csv_bytes))
            media = "application/gzip"
        return stream.getvalue(), f"brphm-{example_id}.{fmt}", media
    raise ValueError(f"format {fmt!r} is not implemented")

# A container is only accepted when it can be losslessly reduced to the
# committee's three required semantics plus bounded scalar context. Python
# serializers are intentionally absent: parsing an uploaded pickle or joblib
# would execute attacker-controlled object reconstruction code.
TEXT_EXTENSIONS = frozenset({".csv", ".tsv", ".txt", ".tab", ".dat"})
JSON_EXTENSIONS = frozenset({".json", ".jsonl", ".ndjson"})
SPREADSHEET_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"})
COLUMNAR_EXTENSIONS = frozenset({".parquet", ".pq", ".feather", ".arrow", ".ipc"})
SCIENTIFIC_EXTENSIONS = frozenset({".mat", ".h5", ".hdf5", ".npy", ".npz"})
MULTI_ARCHIVE_EXTENSIONS = frozenset({
    ".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
})
ARCHIVE_EXTENSIONS = MULTI_ARCHIVE_EXTENSIONS | frozenset({".gz"})
UNSAFE_SERIALIZED_EXTENSIONS = frozenset({".pkl", ".pickle", ".joblib"})
SUPPORTED_DATA_EXTENSIONS = (
    TEXT_EXTENSIONS | JSON_EXTENSIONS | SPREADSHEET_EXTENSIONS
    | COLUMNAR_EXTENSIONS | SCIENTIFIC_EXTENSIONS
)
# This bounds decompression as well as an ordinary upload.  A compressed
# 8 MiB member may not expand into an unbounded allocation during inspection.
MAX_EXPANDED_BYTES = MAX_FILE_BYTES

BAT_CHANNELS = (
    "bat.capacity_ah",
    "bat.ir_proxy_ohm",
    "bat.temp_mean_c",
    "bat.charge_time_s",
)
BAT_REQUIRED = frozenset((
    "bat.capacity_ah",
    "bat.temp_mean_c",
    "bat.charge_time_s",
))
RWA_BASE_CHANNELS = (
    "rw.speed_rpm",
    "rw.motor_current_a",
    "rw.bearing_temp_c",
)
RWA_CHANNELS = (
    "rw.speed_rpm.mean", "rw.speed_rpm.std", "rw.speed_rpm.min", "rw.speed_rpm.max",
    "rw.motor_current_a.mean", "rw.motor_current_a.std", "rw.motor_current_a.min",
    "rw.motor_current_a.max", "rw.bearing_temp_c.mean", "rw.bearing_temp_c.std",
    "rw.bearing_temp_c.min", "rw.bearing_temp_c.max", "rw.fric_tc.mean",
)
EXPECTED_ENSEMBLE_SEEDS = (17, 42, 73)
EXPECTED_POINT_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "bat": {
        "selection_aggregation": "median3",
        "production_point_aggregation": "median3",
        "production_point_seed": None,
    },
    "rwa": {
        "selection_aggregation": "median3",
        "production_point_aggregation": "median3",
        "production_point_seed": None,
    },
}

_PUBLIC_MODEL_NAMES = {
    "bat": "电池部件剩余寿命预测模型（储能系统）",
    "rwa": "反作用轮部件剩余寿命预测模型（姿态控制执行器）",
}
_PUBLIC_COMPONENT_NAMES = {
    "bat": "电池部件（储能系统）",
    "rwa": "反作用轮部件（姿态控制执行器）",
}

# This is the machine-readable allow-list for uploaded model features.  It is
# intentionally narrower than the set of provenance fields accepted below.
# Targets, future information and arbitrary derived telemetry cannot enter a
# model merely because a similarly named column is present in an upload.
MODEL_INPUT_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "battery_cycle": {
        "route": "bat",
        "observed_upload_channels": tuple(sorted(BAT_REQUIRED)),
        "derived_upload_channels": (),
        "frozen_absent_channels": ("bat.ir_proxy_ohm",),
    },
    "reaction_wheel_raw": {
        "route": "rwa",
        "observed_upload_channels": RWA_BASE_CHANNELS,
        "derived_upload_channels": (),
        "derived_in_service": RWA_CHANNELS,
    },
    "reaction_wheel_preaggregated": {
        "route": "rwa",
        "observed_upload_channels": (),
        "derived_upload_channels": RWA_CHANNELS,
    },
}
MODEL_DECLARED_UPLOAD_CHANNELS = frozenset(
    channel
    for contract in MODEL_INPUT_CONTRACTS.values()
    for field in ("observed_upload_channels", "derived_upload_channels", "frozen_absent_channels")
    for channel in contract.get(field, ())
)

# Tree experts are sensitive to both feature order and feature count.  Keep
# the post-normalisation shapes explicit in the public contract so a caller
# can distinguish a readable table from a table that is actually model-ready.
# The service may accept a wider source table, but it must deterministically
# reduce it to these shapes before invoking PyTorch or the tree expert.
MODEL_SHAPE_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "battery": {
        "route": "bat",
        "source_semantics": "three_declared_battery_channels",
        "required_history_steps": 60,
        "window_shape": [60, 4],
        "pytorch_direct_features": 55,
        "pytorch_invariant_features": 55,
        "tree_feature_vector": 52,
        "tree_feature_order": "13_statistics x 4_canonical_channels",
        "missing_policy": "reject_missing_required_channel; ir_proxy_is_frozen_zero_channel",
    },
    "reaction_wheel_raw": {
        "route": "rwa",
        "source_semantics": "three_raw_reaction_wheel_channels",
        "required_history_steps": 30,
        "aggregation": "574_second_bins",
        "window_shape": [30, 13],
        "pytorch_direct_features": 172,
        "pytorch_invariant_features": 55,
        "tree_feature_vector": 390,
        "tree_feature_order": "30_bins x 13_canonical_features",
        "missing_policy": "reject_incomplete_bin; derive_only_declared_statistics_and_friction_feature",
    },
    "reaction_wheel_preaggregated": {
        "route": "rwa",
        "source_semantics": "thirteen_declared_reaction_wheel_features",
        "required_history_steps": 30,
        "aggregation": "already_aggregated_time_bin",
        "window_shape": [30, 13],
        "pytorch_direct_features": 172,
        "pytorch_invariant_features": 55,
        "tree_feature_vector": 390,
        "tree_feature_order": "30_bins x 13_canonical_features",
        "missing_policy": "reject_missing_feature_or_noncontiguous_bin",
    },
}


class TelemetryError(ValueError):
    """A client-safe rejection with a stable machine-readable error code."""

    def __init__(self, code: str, message: str, *, status: int = 422,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ModelUnavailable(TelemetryError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("model_unavailable", message, status=503, details=details)


@dataclass(frozen=True)
class Record:
    time_key: str
    time_order: float
    time_display: str
    time_kind: str
    channel: str
    value: float
    row_number: int


@dataclass(frozen=True)
class ParsedUpload:
    filename: str
    sha256: str
    byte_count: int
    encoding: str
    delimiter: str
    headers: tuple[str, ...]
    time_header: str
    time_hint: str | None
    records: tuple[Record, ...]
    channels: tuple[str, ...]
    channel_labels: Mapping[str, str]
    source_format: str
    embedded_filename: str | None
    metadata: Mapping[str, Mapping[str, Any]]
    container_filename: str | None = None
    container_sha256: str | None = None
    table_layout: str = "long"


@dataclass(frozen=True)
class UploadPart:
    """One bounded logical data file discovered in a browser upload."""

    filename: str
    parse_filename: str
    content: bytes
    embedded_filename: str | None = None
    container_filename: str | None = None
    container_sha256: str | None = None
    wrapper: str | None = None


@dataclass(frozen=True)
class TableSource:
    """A parsed, but not yet semantically trusted, tabular container."""

    headers: tuple[Any, ...]
    rows: Iterable[Sequence[Any]]
    source_format: str
    encoding: str
    delimiter: str = ""


@dataclass(frozen=True)
class PreparedInput:
    filename: str
    sha256: str
    route: str
    input_mode: str
    matrix: np.ndarray
    time_ends: tuple[dict[str, Any], ...]
    source_records: int
    source_time_points: int
    ignored_channels: tuple[str, ...]
    imputed_cells: int
    time_unit: str
    time_unit_basis: str


@dataclass
class StoredBatch:
    created_monotonic: float
    content: bytes


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"[\s_.\-/:()（）\[\]{}]+", "", text)


def _header_key(value: str) -> str:
    """Normalise width and whitespace while preserving semantic punctuation."""
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", "", text)


def _safe_upload_filename(filename: str) -> str:
    """Return a basename consistently for browser, Windows, and POSIX clients."""
    candidate = str(filename or "telemetry.csv").replace("\\", "/")
    return PurePosixPath(candidate).name or "telemetry.csv"


def _archive_suffix(filename: str) -> str | None:
    lower = filename.casefold()
    for suffix in sorted(ARCHIVE_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(suffix):
            return suffix
    return None


def _alias_map(
    groups: dict[str, Iterable[str]],
    *,
    normalizer: Callable[[str], str] = _key,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for canonical, values in groups.items():
        for value in (canonical, *values):
            normalized = normalizer(value)
            previous = result.get(normalized)
            if previous is not None and previous != canonical:
                raise RuntimeError(f"ambiguous internal alias {value!r}")
            result[normalized] = canonical
    return result


TIME_HEADER_UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "cycle": (
        "cycle", "cycle_index", "cycle(index)", "cycle[index]", "循环", "循环数",
        "循环序号", "循环序号(cycle)", "时间_循环", "时间(循环)", "时间[循环]",
    ),
    "millisecond": (
        "time_ms", "time(ms)", "time[ms]", "milliseconds", "时间_毫秒",
        "时间(毫秒)", "时间[毫秒]",
    ),
    "second": (
        "time_s", "time(s)", "time[s]", "seconds", "时间_秒", "时间(秒)", "时间[秒]",
    ),
    "minute": (
        "time_min", "time(min)", "time[min]", "minutes", "时间_分钟",
        "时间(分钟)", "时间[分钟]",
    ),
    "hour": (
        "time_h", "time(h)", "time[h]", "hours", "时间_小时", "时间(小时)", "时间[小时]",
    ),
    "day": (
        "time_day", "time(day)", "time[day]", "days", "时间_天", "时间(天)", "时间[天]",
    ),
    "bin": (
        "time_bin", "time(bin)", "time[bin]", "bin", "时间_桶", "时间(桶)",
        "时间[桶]", "时间桶", "已聚合时间桶",
    ),
}
GENERIC_TIME_HEADER_ALIASES = (
    "time", "timestamp", "datetime", "date_time", "t", "sample_index", "sample_clock",
    "step", "step_index", "index", "时间", "时刻", "采样时间", "时间戳", "时间(time)",
)
TIME_FIELD_ALIASES = GENERIC_TIME_HEADER_ALIASES + tuple(
    alias for aliases in TIME_HEADER_UNIT_ALIASES.values() for alias in aliases
)

HEADER_ALIASES = _alias_map({
    "time": TIME_FIELD_ALIASES,
    "telemetry": ("telemetry_name", "telemetry_variable", "channel", "channel_name",
                  "metric", "variable", "signal", "遥测量", "遥测量名称", "遥测参数",
                  "参数", "测点", "变量", "遥测量(telemetry)"),
    "value": ("reading", "measurement", "telemetry_value", "数值", "值", "测量值",
              "遥测值", "值(value)"),
}, normalizer=_header_key)

CHANNEL_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "bat.capacity_ah": (
        "capacity_ah", "battery_capacity_ah", "电池容量ah", "容量ah", "容量(Ah)",
    ),
    "bat.ir_proxy_ohm": (
        "ir_proxy_ohm", "battery_ir_proxy_ohm", "内阻代理ohm", "内阻代理(ohm)",
    ),
    "bat.temp_mean_c": (
        "temp_mean_c", "battery_temp_mean_c", "battery_temperature_c", "电池温度c",
        "电池温度(℃)", "电池平均温度c",
    ),
    "bat.charge_time_s": (
        "charge_time_s", "battery_charge_time_s", "充电时长s", "充电时间s", "充电时间(秒)",
    ),
    "rw.speed_rpm": ("reaction_wheel_speed_rpm", "wheel_speed_rpm", "转速rpm", "转速(rpm)"),
    "rw.motor_current_a": ("reaction_wheel_current_a", "wheel_current_a", "电机电流a", "电机电流(a)"),
    "rw.bearing_temp_c": ("reaction_wheel_bearing_temp_c", "轴承温度c", "轴承温度(℃)"),
    **{channel: () for channel in RWA_CHANNELS},
}
CHANNEL_ALIASES = _alias_map(CHANNEL_ALIAS_GROUPS)

# Unit declarations are optional, but once supplied they must match the exact
# scale used to train the frozen model. Same-scale spellings are accepted;
# conversions such as mAh -> Ah or ms -> s are intentionally never guessed.
MODEL_CHANNEL_UNITS: Mapping[str, str] = {
    "bat.capacity_ah": "Ah",
    "bat.ir_proxy_ohm": "ohm",
    "bat.temp_mean_c": "degC",
    "bat.charge_time_s": "s",
    "rw.speed_rpm": "rpm",
    "rw.motor_current_a": "A",
    "rw.bearing_temp_c": "degC",
    **{
        channel: (
            "rpm" if channel.startswith("rw.speed_rpm.")
            else "A" if channel.startswith("rw.motor_current_a.") or channel == "rw.fric_tc.mean"
            else "degC"
        )
        for channel in RWA_CHANNELS
    },
}
UNIT_VALUE_ALIAS_GROUPS: Mapping[str, tuple[str, ...]] = {
    "Ah": ("ah", "ampere-hour", "ampere hour", "ampere-hours", "安时", "安培小时"),
    "ohm": ("ohm", "ohms", "Ω", "Ω", "欧姆"),
    "degC": ("degc", "degree celsius", "degrees celsius", "°c", "℃", "摄氏度"),
    "s": ("s", "sec", "secs", "second", "seconds", "秒"),
    "rpm": ("rpm", "r/min", "rev/min", "revolutions/minute", "转/分", "转每分钟"),
    "A": ("a", "amp", "amps", "ampere", "amperes", "安培"),
}
UNIT_VALUE_ALIASES = _alias_map(
    {canonical: aliases for canonical, aliases in UNIT_VALUE_ALIAS_GROUPS.items()},
    normalizer=_key,
)

OPTIONAL_METADATA_FIELDS = (
    {
        "name": "unit", "label": "单位", "unit": None,
        "description": "观测值的工程单位；长表可逐观测声明，宽表只适合声明同单位通道。",
    },
    {
        "name": "component", "label": "组件类型", "unit": None,
        "description": "原数据给出的组件或子系统类型，不依据遥测数值猜测。",
    },
    {
        "name": "component_id", "label": "组件标识", "unit": None,
        "description": "原数据给出的组件、设备或样本唯一标识。",
    },
    {
        "name": "orbit_altitude_km", "label": "轨道高度", "unit": "km",
        "description": "平均地心轨道半径减去 6378.1363 km 地球赤道参考半径。",
    },
    {
        "name": "orbit_inclination_deg", "label": "轨道倾角", "unit": "deg",
        "description": "轨道平面与地球赤道平面的夹角，范围 0 至 180 度。",
    },
    {
        "name": "orbital_period_min", "label": "轨道周期", "unit": "min",
        "description": "航天器完成一整圈轨道公转所需的分钟数。",
    },
    {
        "name": "orbit_perigee_altitude_km", "label": "近地点高度", "unit": "km",
        "description": "近地点距地球赤道参考面的高度；与远地点共同定义椭圆轨道。",
    },
    {
        "name": "orbit_apogee_altitude_km", "label": "远地点高度", "unit": "km",
        "description": "远地点距地球赤道参考面的高度；必须不小于近地点高度。",
    },
    {
        "name": "orbit_eccentricity", "label": "轨道偏心率", "unit": None,
        "description": "无量纲偏心率 e；0 为圆轨道，0<e<1 为闭合椭圆。",
    },
    {
        "name": "orbit_semi_major_axis_km", "label": "轨道半长轴", "unit": "km",
        "description": "地心轨道半长轴 a；必须与偏心率或近远地点数据自洽。",
    },
    {
        "name": "orbit_speed_km_s", "label": "当前位置切向速度", "unit": "km/s",
        "description": "当前位置的切向速度；用于按比机械能判定闭合、临界或逃逸状态。",
    },
    {
        "name": "orbit_raan_deg", "label": "升交点赤经", "unit": "deg",
        "description": "轨道平面升交点相对参考方向的角度；没有该事实时不由页面猜测。",
    },
    {
        "name": "orbit_arg_periapsis_deg", "label": "近地点幅角", "unit": "deg",
        "description": "轨道平面内升交点到近地点的角度。",
    },
    {
        "name": "orbit_true_anomaly_deg", "label": "真近点角", "unit": "deg",
        "description": "从近地点量到当前位置的轨道平面角度。",
    },
    {
        "name": "operating_condition", "label": "运行工况", "unit": None,
        "description": "原数据提供的工况标识或标量；不从遥测数值反推。",
    },
    {
        "name": "degradation_state", "label": "退化状态", "unit": None,
        "description": "原数据提供的退化状态或标量；缺失时保持为空。",
    },
    {
        "name": "rul", "label": "剩余寿命标签", "unit": None,
        "description": "原数据提供的剩余寿命真值；仅用于溯源或评价，禁止作为当前预测特征。",
    },
    {
        "name": "life_label", "label": "寿命标签", "unit": None,
        "description": "原数据提供的寿命阶段或寿命真值标签；禁止作为当前预测特征。",
    },
    {
        "name": "failure_label", "label": "失效标签", "unit": None,
        "description": "原数据提供的失效标签；不会由预测结果反向补造。",
    },
)

OPTIONAL_METADATA_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "unit": ("units", "engineering_unit", "measurement_unit", "value_unit", "单位", "量纲"),
    "component": (
        "component_type", "component_name", "subsystem", "part", "组件", "部件",
        "组件类型", "部件类型", "子系统",
    ),
    "component_id": (
        "componentid", "asset_id", "device_id", "unit_id", "sample_id", "组件编号",
        "组件标识", "部件编号", "设备编号", "样本编号",
    ),
    "orbit_altitude_km": ("orbit_altitude", "altitude_km", "轨道高度", "轨道高度_km"),
    "orbit_inclination_deg": ("orbit_inclination", "inclination_deg", "轨道倾角", "轨道倾角_deg"),
    "orbital_period_min": ("orbit_period_min", "orbital_period", "轨道周期", "轨道周期_min"),
    "orbit_perigee_altitude_km": ("perigee_altitude_km", "近地点高度", "近地点高度_km"),
    "orbit_apogee_altitude_km": ("apogee_altitude_km", "远地点高度", "远地点高度_km"),
    "orbit_eccentricity": ("eccentricity", "轨道偏心率", "偏心率"),
    "orbit_semi_major_axis_km": ("semi_major_axis_km", "轨道半长轴", "半长轴_km"),
    "orbit_speed_km_s": ("orbital_speed_km_s", "轨道速度", "轨道速度_km_s"),
    "orbit_raan_deg": ("raan_deg", "raan", "升交点赤经", "升交点赤经_deg"),
    "orbit_arg_periapsis_deg": ("arg_periapsis_deg", "近地点幅角", "近地点幅角_deg"),
    "orbit_true_anomaly_deg": ("true_anomaly_deg", "真近点角", "真近点角_deg"),
    "operating_condition": (
        "condition", "operating_mode", "operating_regime", "regime", "工况", "运行工况",
        "工作状态", "工况标识",
    ),
    "degradation_state": (
        "degradation", "health_state", "label.hi", "hi_label", "health_index_label",
        "退化状态", "健康状态", "退化阶段",
    ),
    "rul": (
        "remaining_useful_life", "remaining_life", "residual_life", "rul_label", "label.rul",
        "剩余使用寿命", "剩余寿命", "剩余寿命标签",
    ),
    "life_label": ("lifetime_label", "life_stage", "寿命标签", "寿命阶段"),
    "failure_label": (
        "failure", "failure_state", "fault_label", "label.fail", "label.failure",
        "失效标签", "故障标签",
    ),
}
OPTIONAL_METADATA_ALIASES = _alias_map(
    OPTIONAL_METADATA_ALIAS_GROUPS,
    normalizer=_header_key,
)
TARGET_METADATA_FIELDS = frozenset({"rul", "life_label", "failure_label"})


def _metadata_role(canonical: str, source_header: str) -> tuple[str, str]:
    """Classify provenance without granting it model-feature status."""
    normalized = _header_key(source_header)
    future_markers = ("future", "target", "label", "truth", "ytrue", "未来", "真值", "标签")
    if canonical in TARGET_METADATA_FIELDS or any(marker in normalized for marker in future_markers):
        return "target_or_future_information", "target_or_future_information_is_never_a_model_feature"
    if canonical == "degradation_state":
        return "evaluation_context", "evaluation_state_not_declared_by_frozen_model"
    return "provenance_context", "not_declared_by_frozen_model_contract"


def _validate_model_channel_units(metadata_state: Mapping[str, Mapping[str, Any]]) -> None:
    """Reject declared units whose dimension or scale differs from training."""
    unit_state = metadata_state.get("unit")
    if not unit_state:
        return
    values_by_channel = unit_state.get("values_by_channel", {})
    for channel, declared_values in values_by_channel.items():
        expected = MODEL_CHANNEL_UNITS.get(channel)
        if expected is None:
            continue
        mismatches = [
            value for value in declared_values
            if UNIT_VALUE_ALIASES.get(_key(str(value))) != expected
        ]
        if mismatches:
            raise TelemetryError(
                "model_channel_unit_mismatch",
                "已声明模型通道的单位或倍率与冻结模型输入契约不一致；服务不会静默换算",
                details={
                    "channel": channel,
                    "expected": expected,
                    "received": mismatches,
                    "conversion_performed": False,
                },
            )

TIME_HEADER_HINTS = {
    _header_key(alias): unit
    for unit, aliases in TIME_HEADER_UNIT_ALIASES.items()
    for alias in aliases
}


def _time_hint(header: str) -> str | None:
    return TIME_HEADER_HINTS.get(_header_key(header))


def _parse_time(value: str, row_number: int) -> tuple[str, float, str]:
    text = value.strip()
    if not text:
        raise TelemetryError("empty_time", "时间字段不能为空", details={"row": row_number})
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        if not math.isfinite(numeric):
            raise TelemetryError("nonfinite_time", "时间必须是有限数值或 ISO 8601 时间", details={"row": row_number})
        # Canonicalise signed zero so ``-0`` cannot bypass a duplicate check
        # against ``0`` for the same telemetry channel.
        canonical = 0.0 if numeric == 0 else numeric
        return "numeric", canonical, f"numeric:{format(canonical, '.17g')}"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TelemetryError(
            "invalid_time", "时间必须是有限数值或 ISO 8601 时间", details={"row": row_number, "value": text[:80]}
        ) from exc
    if parsed.tzinfo is None:
        epoch = datetime(1970, 1, 1)
        seconds = (parsed - epoch).total_seconds()
        kind = "iso_naive"
        canonical = f"iso_naive:{parsed.isoformat()}"
    else:
        seconds = parsed.astimezone(timezone.utc).timestamp()
        kind = "iso_aware"
        # The same instant can be written with distinct offsets.  Its UTC epoch
        # identity, rather than source spelling, is the only safe duplicate key.
        canonical = f"iso_aware:{format(seconds, '.17g')}"
    if not math.isfinite(seconds):
        raise TelemetryError("invalid_time", "ISO 时间超出可处理范围", details={"row": row_number})
    return kind, seconds, canonical


def _decode(content: bytes) -> tuple[str, str]:
    if not content:
        raise TelemetryError("empty_file", "上传文件为空")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise TelemetryError("unsupported_encoding", "文件必须是 UTF-8 或 GB18030 编码的文本")


def _cell_text(value: Any, *, location: str) -> str:
    """Return a scalar cell without silently flattening nested structures."""
    if isinstance(value, np.generic):
        value = value.item()
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030"):
            try:
                return value.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        raise TelemetryError(
            "binary_cell_encoding",
            "二进制表中的文本单元格不是 UTF-8 或 GB18030 编码",
            details={"location": location},
        )
    if isinstance(value, (Mapping, list, tuple, set, np.ndarray)):
        raise TelemetryError(
            "nested_table_cell",
            "遥测表格的每个单元格必须是单个标量，不能嵌套数组或对象",
            details={"location": location},
        )
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _table_from_json_records(
    records: Sequence[Any], *, source_format: str, encoding: str,
) -> TableSource:
    if not records:
        raise TelemetryError("no_observations", "上传文件没有有效观测行")
    first = records[0]
    if isinstance(first, Mapping):
        headers = tuple(first.keys())
        header_set = set(headers)
        if len(header_set) != len(headers):
            raise TelemetryError("ambiguous_header", "同一字段语义出现多个表头")
        normalized_rows: list[tuple[Any, ...]] = []
        for row_number, record in enumerate(records, start=2):
            if not isinstance(record, Mapping) or set(record.keys()) != header_set:
                raise TelemetryError(
                    "json_record_shape",
                    "JSON 记录必须具有同一组表头字段",
                    details={"row": row_number},
                )
            normalized_rows.append(tuple(record[header] for header in headers))
        return TableSource(headers, tuple(normalized_rows), source_format, encoding)
    if isinstance(first, Sequence) and not isinstance(first, (str, bytes, bytearray)):
        headers = tuple(first)
        rows: list[tuple[Any, ...]] = []
        for row_number, record in enumerate(records[1:], start=2):
            if not isinstance(record, Sequence) or isinstance(record, (str, bytes, bytearray)):
                raise TelemetryError(
                    "json_record_shape",
                    "JSON 表必须由表头数组和等宽的记录数组组成",
                    details={"row": row_number},
                )
            rows.append(tuple(record))
        return TableSource(headers, tuple(rows), source_format, encoding)
    raise TelemetryError(
        "json_layout_required",
        "JSON 必须是对象记录数组，或以表头数组开头的二维表格数组",
    )


def _json_table(filename: str, content: bytes) -> TableSource:
    text, encoding = _decode(content)
    suffix = Path(filename).suffix.lower()

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TelemetryError(
                    "duplicate_json_key",
                    "JSON 记录包含重复字段名；服务不会让后出现的值静默覆盖先出现的值",
                    details={"key": key},
                )
            result[key] = value
        return result

    if suffix == ".json":
        try:
            payload = json.loads(text, object_pairs_hook=reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise TelemetryError("invalid_json", "JSON 文件不是有效的结构化遥测表格") from exc
        if not isinstance(payload, list):
            raise TelemetryError("json_layout_required", "JSON 顶层必须是遥测表格记录数组")
        return _table_from_json_records(payload, source_format="json", encoding=encoding)

    records: list[Any] = []
    for row_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, object_pairs_hook=reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise TelemetryError(
                "invalid_json_line", "JSONL 的每个非空行都必须是一条 JSON 记录",
                details={"line": row_number},
            ) from exc
        if not isinstance(record, Mapping):
            raise TelemetryError(
                "json_layout_required", "JSONL 的每个非空行都必须是含表头字段的对象记录",
                details={"line": row_number},
            )
        records.append(record)
    return _table_from_json_records(records, source_format="jsonl", encoding=encoding)


def _delimiter(text: str, filename: str) -> str:
    if Path(filename).suffix.lower() in {".tsv", ".tab"}:
        return "\t"
    try:
        return csv.Sniffer().sniff(text[:8192], delimiters=",\t;").delimiter
    except csv.Error as exc:
        raise TelemetryError("delimiter_unknown", "无法识别 CSV/TSV 分隔符") from exc


def _text_table(filename: str, content: bytes) -> TableSource:
    text, encoding = _decode(content)
    delimiter = _delimiter(text, filename)
    rows = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        raw_headers = tuple(next(rows))
    except StopIteration as exc:
        raise TelemetryError("empty_file", "上传文件没有表头") from exc
    return TableSource(raw_headers, rows, "delimited_text", encoding, delimiter)


def _dataframe_table(frame: Any, *, source_format: str) -> TableSource:
    if len(frame.index) > MAX_RECORDS_PER_FILE:
        raise TelemetryError(
            "too_many_records", f"单文件最多 {MAX_RECORDS_PER_FILE} 条观测", status=413,
        )
    return TableSource(
        tuple(frame.columns.tolist()),
        (tuple(row) for row in frame.itertuples(index=False, name=None)),
        source_format,
        "binary container",
    )


def _spreadsheet_table(filename: str, content: bytes) -> TableSource:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - requirements pin this dependency
        raise TelemetryError(
            "format_dependency_unavailable", "当前服务未安装 Excel 读取依赖，不能验证该文件", status=415,
        ) from exc
    suffix = Path(filename).suffix.lower()
    engines = {".xlsx": "openpyxl", ".xlsm": "openpyxl", ".xls": "xlrd", ".xlsb": "pyxlsb", ".ods": "odf"}
    try:
        workbook = pd.ExcelFile(io.BytesIO(content), engine=engines[suffix])
        try:
            sheet_names = list(workbook.sheet_names)
            if len(sheet_names) != 1:
                raise TelemetryError(
                    "ambiguous_spreadsheet_sheet",
                    "Excel 或 ODS 文件必须恰好包含一个工作表，避免猜测应导入的表",
                    details={"sheets": sheet_names},
                )
            frame = pd.read_excel(workbook, sheet_name=0, dtype=object, na_filter=False)
        finally:
            workbook.close()
    except TelemetryError:
        raise
    except ImportError as exc:
        raise TelemetryError(
            "format_dependency_unavailable", "当前服务未安装该 Excel 或 ODS 格式的读取依赖", status=415,
        ) from exc
    except Exception as exc:
        raise TelemetryError("invalid_spreadsheet", "无法读取此 Excel 或 ODS 遥测表格") from exc
    return _dataframe_table(frame, source_format=f"spreadsheet:{suffix[1:]}")


def _table_from_arrow(table: Any, *, source_format: str) -> TableSource:
    if int(table.num_rows) > MAX_RECORDS_PER_FILE:
        raise TelemetryError(
            "too_many_records", f"单文件最多 {MAX_RECORDS_PER_FILE} 条观测", status=413,
        )
    headers = tuple(table.column_names)
    records = table.to_pylist()
    return TableSource(
        headers,
        tuple(tuple(record.get(header) for header in headers) for record in records),
        source_format,
        "binary container",
    )


def _columnar_table(filename: str, content: bytes) -> TableSource:
    try:
        import pyarrow as pa
        import pyarrow.feather as feather
        import pyarrow.ipc as ipc
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - requirements pin this dependency
        raise TelemetryError(
            "format_dependency_unavailable", "当前服务未安装列式数据读取依赖，不能验证该文件", status=415,
        ) from exc
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in {".parquet", ".pq"}:
            table = parquet.read_table(pa.BufferReader(content))
        elif suffix == ".feather":
            table = feather.read_table(pa.BufferReader(content))
        else:
            try:
                table = ipc.open_file(pa.BufferReader(content)).read_all()
            except Exception:
                table = ipc.open_stream(pa.BufferReader(content)).read_all()
    except Exception as exc:
        raise TelemetryError("invalid_columnar_file", "无法读取此 Parquet、Feather 或 Arrow 遥测表格") from exc
    return _table_from_arrow(table, source_format=f"columnar:{suffix[1:]}")


def _one_dimensional_column(value: Any, *, name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype.hasobject:
        raise TelemetryError(
            "unsafe_object_array", "二进制表包含对象数组，不能在不反序列化对象的前提下读取",
            details={"field": name},
        )
    if array.ndim == 1:
        return array
    raise TelemetryError(
        "binary_layout_required", "二进制遥测表格的每个字段必须是一维、等长的标量序列",
        details={"field": name, "shape": list(array.shape)},
    )


def _structured_array_table(array: Any, *, source_format: str) -> TableSource:
    values = np.asarray(array)
    if values.dtype.names is None or values.ndim != 1:
        raise TelemetryError(
            "binary_layout_required", "二进制遥测表格必须是一维具名结构数组，或多条具名等长序列",
        )
    if values.dtype.hasobject:
        raise TelemetryError("unsafe_object_array", "二进制遥测表格不能包含对象数组")
    if len(values) > MAX_RECORDS_PER_FILE:
        raise TelemetryError("too_many_records", f"单文件最多 {MAX_RECORDS_PER_FILE} 条观测", status=413)
    headers = tuple(values.dtype.names)
    return TableSource(
        headers,
        tuple(tuple(row[header] for header in headers) for row in values),
        source_format,
        "binary container",
    )


def _named_columns_table(columns: Mapping[str, Any], *, source_format: str) -> TableSource:
    if len(columns) < 2 or len(columns) > MAX_COLUMNS:
        raise TelemetryError(
            "telemetry_table_columns_required",
            f"测试数据必须满足长表三语义或宽表时间加遥测通道契约；总列数不能超过 {MAX_COLUMNS}",
            details={"headers": list(columns)},
        )
    headers = tuple(columns)
    arrays = [_one_dimensional_column(columns[header], name=header) for header in headers]
    lengths = {len(array) for array in arrays}
    if len(lengths) != 1:
        raise TelemetryError("binary_column_length", "二进制遥测表格的所有字段长度必须一致")
    length = lengths.pop()
    if length > MAX_RECORDS_PER_FILE:
        raise TelemetryError("too_many_records", f"单文件最多 {MAX_RECORDS_PER_FILE} 条观测", status=413)
    return TableSource(headers, tuple(zip(*arrays)), source_format, "binary container")


def _numpy_table(filename: str, content: bytes) -> TableSource:
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".npy":
            values = np.load(io.BytesIO(content), allow_pickle=False)
            return _structured_array_table(values, source_format="numpy:npy")
        with np.load(io.BytesIO(content), allow_pickle=False) as archive:
            names = tuple(archive.files)
            if len(names) == 1:
                return _structured_array_table(archive[names[0]].copy(), source_format="numpy:npz")
            columns = {name: archive[name].copy() for name in names}
            return _named_columns_table(columns, source_format="numpy:npz")
    except TelemetryError:
        raise
    except (OSError, ValueError) as exc:
        raise TelemetryError("invalid_numpy_file", "无法读取此 NPY 或 NPZ 遥测表格") from exc


def _mat_table(content: bytes) -> TableSource:
    try:
        from scipy.io import loadmat
    except ImportError as exc:  # pragma: no cover - requirements pin this dependency
        raise TelemetryError(
            "format_dependency_unavailable", "当前服务未安装 MAT 文件读取依赖，不能验证该文件", status=415,
        ) from exc
    try:
        # Preserve singleton dimensions so the binary layout validator can
        # reject ambiguous row/column matrices instead of silently flattening
        # them before validation.
        document = loadmat(io.BytesIO(content), struct_as_record=False, squeeze_me=False, chars_as_strings=True)
    except NotImplementedError:
        return _hdf5_table(content, source_format="mat-v7.3")
    except Exception as exc:
        raise TelemetryError("invalid_mat_file", "无法读取此 MAT 遥测表格") from exc
    variables = {name: value for name, value in document.items() if not name.startswith("__")}
    if len(variables) == 1:
        value = next(iter(variables.values()))
        if isinstance(value, np.ndarray) and value.dtype.names is not None:
            return _structured_array_table(value, source_format="mat")
        field_names = getattr(value, "_fieldnames", None)
        if isinstance(field_names, list):
            variables = {name: getattr(value, name) for name in field_names}
    return _named_columns_table(variables, source_format="mat")


def _hdf5_table(content: bytes, *, source_format: str = "hdf5") -> TableSource:
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover - requirements pin this dependency
        raise TelemetryError(
            "format_dependency_unavailable", "当前服务未安装 HDF5 文件读取依赖，不能验证该文件", status=415,
        ) from exc
    try:
        with h5py.File(io.BytesIO(content), "r") as handle:
            if hasattr(handle, "visititems_links"):
                unsafe_link = False

                def inspect_link(_: str, link: Any) -> None:
                    nonlocal unsafe_link
                    if isinstance(link, (h5py.SoftLink, h5py.ExternalLink)):
                        unsafe_link = True

                handle.visititems_links(inspect_link)
                if unsafe_link:
                    raise TelemetryError(
                        "unsafe_hdf5_link", "HDF5 文件不能包含软链接或外部链接",
                    )
            datasets: dict[str, Any] = {}

            def collect(name: str, value: Any) -> None:
                if isinstance(value, h5py.Dataset):
                    datasets[name] = value

            handle.visititems(collect)
            if len(datasets) == 1:
                dataset = next(iter(datasets.values()))
                if dataset.size > MAX_RECORDS_PER_FILE:
                    raise TelemetryError(
                        "too_many_records", f"单文件最多 {MAX_RECORDS_PER_FILE} 条观测", status=413,
                    )
                return _structured_array_table(dataset[()], source_format=source_format)
            columns = {
                Path(name).name: dataset[()]
                for name, dataset in datasets.items()
            }
            if len(columns) != len(datasets):
                raise TelemetryError(
                    "ambiguous_hdf5_field", "HDF5 文件中的字段名称重复，无法唯一识别遥测字段",
                )
            return _named_columns_table(columns, source_format=source_format)
    except TelemetryError:
        raise
    except Exception as exc:
        raise TelemetryError("invalid_hdf5_file", "无法读取此 HDF5 遥测表格") from exc


def _read_limited(stream: Any) -> bytes:
    content = stream.read(MAX_EXPANDED_BYTES + 1)
    if len(content) > MAX_EXPANDED_BYTES:
        raise TelemetryError(
            "expanded_file_too_large",
            f"解压后的单个文件不能超过 {MAX_EXPANDED_BYTES // 1024 // 1024} MiB",
            status=413,
        )
    return content


def _unwrap_archive(filename: str, content: bytes) -> tuple[str, bytes, str | None]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".gz":
        inner = Path(filename).stem
        if Path(inner).suffix.lower() not in SUPPORTED_DATA_EXTENSIONS:
            raise TelemetryError(
                "archive_member_unsupported", "GZIP 文件名必须保留可识别的数据扩展名，例如 telemetry.csv.gz", status=415,
            )
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as stream:
                return Path(inner).name, _read_limited(stream), "gzip"
        except TelemetryError:
            raise
        except OSError as exc:
            raise TelemetryError("invalid_gzip", "无法读取此 GZIP 文件") from exc
    if suffix != ".zip":
        return filename, content, None
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1:
                raise TelemetryError(
                    "zip_member_count", "ZIP 文件必须只包含一个数据文件，不能混入多个候选表或目录",
                )
            member = members[0]
            member_path = PurePosixPath(member.filename)
            if member.flag_bits & 0x1 or member_path.is_absolute() or ".." in member_path.parts:
                raise TelemetryError("unsafe_zip_member", "ZIP 文件包含不安全或加密的成员，不能读取")
            inner = member_path.name
            if not inner or Path(inner).suffix.lower() not in SUPPORTED_DATA_EXTENSIONS:
                raise TelemetryError(
                    "archive_member_unsupported", "ZIP 中唯一成员必须是受支持的数据文件", status=415,
                )
            if member.file_size > MAX_EXPANDED_BYTES:
                raise TelemetryError(
                    "expanded_file_too_large",
                    f"解压后的单个文件不能超过 {MAX_EXPANDED_BYTES // 1024 // 1024} MiB",
                    status=413,
                )
            with archive.open(member, "r") as stream:
                return inner, _read_limited(stream), "zip"
    except TelemetryError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise TelemetryError("invalid_zip", "无法读取此 ZIP 文件") from exc


def _decode_table(filename: str, content: bytes) -> TableSource:
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return _text_table(filename, content)
    if suffix in JSON_EXTENSIONS:
        return _json_table(filename, content)
    if suffix in SPREADSHEET_EXTENSIONS:
        return _spreadsheet_table(filename, content)
    if suffix in COLUMNAR_EXTENSIONS:
        return _columnar_table(filename, content)
    if suffix in {".npy", ".npz"}:
        return _numpy_table(filename, content)
    if suffix == ".mat":
        return _mat_table(content)
    if suffix in {".h5", ".hdf5"}:
        return _hdf5_table(content)
    if suffix in UNSAFE_SERIALIZED_EXTENSIONS:
        raise TelemetryError(
            "unsafe_serialized_format",
            "不接收 Pickle、Joblib 等可执行对象序列化格式；请导出为可审计结构化表格",
            status=415,
        )
    raise TelemetryError("unsupported_file_type", "该文件格式不在安全遥测表格兼容范围内", status=415)


def parse_upload(filename: str, content: bytes) -> ParsedUpload:
    safe_name = _safe_upload_filename(filename)
    archive_suffix = _archive_suffix(safe_name)
    if archive_suffix in MULTI_ARCHIVE_EXTENSIONS:
        parts = _expand_upload_parts(safe_name, content)
        if len(parts) != 1:
            wrapper = parts[0].wrapper or "archive"
            raise TelemetryError(
                f"{wrapper}_member_count",
                f"{wrapper.upper()} 文件包含多个数据文件；单文件入口不能替用户选择，请使用批量解析入口",
                details={"members": [part.embedded_filename for part in parts]},
            )
        part = parts[0]
        parsed = parse_upload(part.parse_filename, part.content)
        outer_sha256 = hashlib.sha256(content).hexdigest()
        return replace(
            parsed,
            filename=safe_name,
            sha256=outer_sha256,
            byte_count=len(content),
            source_format=f"{part.wrapper or 'archive'}/{parsed.source_format}",
            embedded_filename=part.embedded_filename,
            container_filename=safe_name,
            container_sha256=outer_sha256,
        )
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_DATA_EXTENSIONS | ARCHIVE_EXTENSIONS | UNSAFE_SERIALIZED_EXTENSIONS:
        raise TelemetryError("unsupported_file_type", "该文件格式不在安全遥测表格兼容范围内", status=415)
    if len(content) > MAX_FILE_BYTES:
        raise TelemetryError("file_too_large", f"单文件不能超过 {MAX_FILE_BYTES // 1024 // 1024} MiB", status=413)
    table_filename, table_content, wrapper = _unwrap_archive(safe_name, content)
    table = _decode_table(table_filename, table_content)
    headers = [_cell_text(value, location="header") for value in table.headers]
    if len(headers) < 2 or len(headers) > MAX_COLUMNS or any(not value for value in headers):
        raise TelemetryError(
            "telemetry_table_columns_required",
            f"长表必须包含时间、遥测量、值，宽表必须包含时间和至少一个有限数值遥测列；总列数不能超过 {MAX_COLUMNS}",
            details={"headers": headers},
        )
    semantic_positions: dict[str, int] = {}
    header_keys: set[str] = set()
    for index, header in enumerate(headers):
        normalized_header = _header_key(header)
        if normalized_header in header_keys:
            raise TelemetryError("ambiguous_header", "规范化后的表头重复，无法唯一识别列", details={"header": header})
        header_keys.add(normalized_header)
        semantic = HEADER_ALIASES.get(normalized_header)
        if semantic is None:
            continue
        if semantic in semantic_positions:
            raise TelemetryError("ambiguous_header", "同一字段语义出现多个表头", details={"semantic": semantic})
        semantic_positions[semantic] = index

    all_long_semantics = {"time", "telemetry", "value"}
    metadata_positions: list[tuple[int, str, str]] = []
    wide_channel_positions: list[tuple[int, str, str]] = []
    metadata_semantics: set[str] = set()
    wide_channels: set[str] = set()
    layout: str

    if set(semantic_positions) == all_long_semantics:
        layout = "long"
        for index, header in enumerate(headers):
            if index in semantic_positions.values():
                continue
            canonical_metadata = OPTIONAL_METADATA_ALIASES.get(_header_key(header), header)
            wide_channel = CHANNEL_ALIASES.get(_key(header))
            if wide_channel is not None:
                raise TelemetryError(
                    "mixed_table_layout",
                    "同一张表不能同时使用长表三字段和宽表遥测通道列",
                    details={"header": header, "channel": wide_channel},
                )
            if canonical_metadata in metadata_semantics:
                raise TelemetryError(
                    "ambiguous_metadata_header",
                    "同一可选语义出现多个表头，无法确定采用哪一列",
                    details={"semantic": canonical_metadata, "header": header},
                )
            metadata_semantics.add(canonical_metadata)
            metadata_positions.append((index, canonical_metadata, header))
    elif set(semantic_positions) == {"time"}:
        layout = "wide"
        for index, header in enumerate(headers):
            if index == semantic_positions["time"]:
                continue
            canonical_metadata = OPTIONAL_METADATA_ALIASES.get(_header_key(header))
            if canonical_metadata is not None:
                if canonical_metadata in metadata_semantics:
                    raise TelemetryError(
                        "ambiguous_metadata_header",
                        "同一可选语义出现多个表头，无法确定采用哪一列",
                        details={"semantic": canonical_metadata, "header": header},
                    )
                metadata_semantics.add(canonical_metadata)
                metadata_positions.append((index, canonical_metadata, header))
                continue
            channel = CHANNEL_ALIASES.get(_key(header))
            if channel is None:
                # Preserve undeclared numeric telemetry by its exact source
                # label. It remains outside MODEL_INPUT_CONTRACTS and is
                # therefore reported as ignored during preparation.
                channel = f"unknown:{header}"
            if channel not in MODEL_DECLARED_UPLOAD_CHANNELS:
                if channel.startswith("unknown:"):
                    wide_channel_positions.append((index, channel, header))
                    continue
                channel = f"unknown:{header}"
                wide_channel_positions.append((index, channel, header))
                continue
            if channel in wide_channels:
                raise TelemetryError(
                    "ambiguous_wide_channel",
                    "多个宽表列映射到同一遥测通道，无法确定采用哪一列",
                    details={"channel": channel, "header": header},
                )
            wide_channels.add(channel)
            wide_channel_positions.append((index, channel, header))
        if not wide_channel_positions:
            raise TelemetryError(
                "wide_table_telemetry_required",
                "宽表缺少可解析的有限数值遥测列",
            )
    else:
        unknown = [
            header for header in headers
            if HEADER_ALIASES.get(_header_key(header)) is None
        ]
        if len(headers) == 3 and unknown:
            raise TelemetryError(
                "unknown_header", "三个必需字段的表头无法映射到时间、遥测量、值语义",
                details={"header": unknown[0], "accepted": sorted(HEADER_ALIASES)},
            )
        raise TelemetryError(
            "long_table_columns_required",
            "长表必须同时提供时间、遥测量和值；宽表只能提供一个时间列，不得残留不完整的长表语义",
            details={"recognized_semantics": sorted(semantic_positions)},
        )

    records: list[Record] = []
    seen: set[tuple[str, str]] = set()
    time_kinds: set[str] = set()
    channel_labels: dict[str, str] = {}
    metadata_state: dict[str, dict[str, Any]] = {}
    component_ids: set[str] = set()
    component_id_position = next(
        (position for position, canonical, _ in metadata_positions if canonical == "component_id"),
        None,
    )
    for _, canonical, source_header in metadata_positions:
        role, exclusion_reason = _metadata_role(canonical, source_header)
        metadata_state[canonical] = {
            "label": source_header,
            "semantic": canonical,
            "role": role,
            "summary_scope": "file",
            "time_aligned": False,
            "distinct_values_limit": 8,
            "distinct_values_truncated": False,
            "nonempty_count": 0,
            "distinct_values": [],
            "model_used": False,
            "blocked_from_model": True,
            "model_exclusion_reason": exclusion_reason,
        }
        if canonical == "unit":
            metadata_state[canonical]["values_by_channel"] = {}
    for row_number, source_row in enumerate(table.rows, start=2):
        if not isinstance(source_row, Sequence) or isinstance(source_row, (str, bytes, bytearray)):
            raise TelemetryError(
                "row_shape", "每条遥测观测必须是由标量组成的一行", details={"row": row_number},
            )
        row = [_cell_text(value, location=f"row {row_number}") for value in source_row]
        if len(row) != len(headers):
            raise TelemetryError(
                "row_column_count", "每条观测的列数必须与表头一致",
                details={"row": row_number, "expected": len(headers), "received": len(row)},
            )
        # A physically blank line is represented by an empty sequence and is
        # safe to ignore. A delimited row that has the wrong width must be
        # rejected first, even when all of its cells are empty: otherwise a
        # malformed table can silently change the observed time axis.
        if not row or all(not value for value in row):
            continue
        raw_time = row[semantic_positions["time"]].strip()
        time_kind, time_order, time_key = _parse_time(raw_time, row_number)
        if component_id_position is not None:
            component_id = row[component_id_position].strip()
            if component_id:
                component_ids.add(component_id)
                if len(component_ids) > 1:
                    raise TelemetryError(
                        "multiple_component_ids",
                        "单个逻辑文件只能包含一个非空组件标识；请按组件拆分后再预测，不能跨实体拼接模型窗口",
                        details={"row": row_number, "component_ids": sorted(component_ids)},
                    )
        if layout == "long":
            observations = [(
                row[semantic_positions["telemetry"]].strip(),
                row[semantic_positions["value"]].strip(),
                None,
            )]
        else:
            observations = [
                (source_header, row[position].strip(), channel)
                for position, channel, source_header in wide_channel_positions
                if row[position].strip()
            ]
            # A declared time with no observations is a malformed time step,
            # not an absent row. Reject it so history cannot be shortened by
            # silently dropping the evaluator's timestamp.
            if not observations:
                raise TelemetryError(
                    "empty_wide_observation",
                    "宽表时间点没有任何非空遥测值，不能静默丢弃该时间点",
                    details={"row": row_number, "time": raw_time},
                )
        time_kinds.add(time_kind)

        row_channels: list[str] = []
        for raw_channel, raw_value, known_channel in observations:
            if len(records) >= MAX_RECORDS_PER_FILE:
                raise TelemetryError("too_many_records", f"单文件最多 {MAX_RECORDS_PER_FILE} 条观测", status=413)
            if not raw_channel:
                raise TelemetryError("empty_telemetry", "遥测量名称不能为空", details={"row": row_number})
            if len(raw_channel) > 160:
                raise TelemetryError("telemetry_name_too_long", "遥测量名称不能超过 160 个字符", details={"row": row_number})
            try:
                value = float(raw_value)
            except ValueError as exc:
                if layout == "wide" and known_channel and known_channel.startswith("unknown:"):
                    raise TelemetryError(
                        "ambiguous_wide_column",
                        "未声明宽表列只有在所有非空单元均为有限数值时，才能按原表头保留为忽略遥测；文本列必须声明为受支持的上下文语义",
                        details={"row": row_number, "header": raw_channel, "value": raw_value},
                    ) from exc
                raise TelemetryError("invalid_value", "遥测值必须是有限数值", details={"row": row_number}) from exc
            if not math.isfinite(value):
                raise TelemetryError("nonfinite_value", "遥测值不能为 NaN 或 Inf", details={"row": row_number})
            channel = known_channel or CHANNEL_ALIASES.get(_key(raw_channel), f"unknown:{raw_channel}")
            # Preserve the uploaded label for unsupported channels reported back
            # to the user, while recognized aliases use their public canonical name.
            channel_labels.setdefault(channel, raw_channel if channel.startswith("unknown:") else channel)
            identity = (time_key, channel)
            if identity in seen:
                raise TelemetryError(
                    "duplicate_observation", "同一时间和遥测量只能出现一次",
                    details={"row": row_number, "time": raw_time, "telemetry": raw_channel},
                )
            seen.add(identity)
            records.append(Record(time_key, time_order, raw_time, time_kind, channel, value, row_number))
            row_channels.append(channel)

        for position, canonical, _source_header in metadata_positions:
            metadata_value = row[position].strip()
            if not metadata_value:
                continue
            if canonical == "unit" and layout == "wide" and len(wide_channel_positions) > 1:
                raise TelemetryError(
                    "ambiguous_wide_unit",
                    "多遥测通道宽表中的单个单位列无法确定对应关系；请在数据字典中声明，或转换为带单位列的长表",
                    details={"channels": [channel for _, channel, _ in wide_channel_positions]},
                )
            state = metadata_state[canonical]
            state["nonempty_count"] += 1
            if metadata_value not in state["distinct_values"]:
                if len(state["distinct_values"]) < state["distinct_values_limit"]:
                    state["distinct_values"].append(metadata_value)
                else:
                    state["distinct_values_truncated"] = True
            if canonical == "unit":
                by_channel = state["values_by_channel"]
                for channel in row_channels:
                    values = by_channel.setdefault(channel, [])
                    if metadata_value not in values and len(values) < 8:
                        values.append(metadata_value)
    if not records:
        raise TelemetryError("no_observations", "上传文件没有有效观测行")
    numeric_or_iso = {"numeric" if item == "numeric" else "iso" for item in time_kinds}
    if len(numeric_or_iso) != 1 or len(time_kinds & {"iso_naive", "iso_aware"}) > 1:
        raise TelemetryError("mixed_time_types", "同一文件不能混用数值、带时区 ISO 和无时区 ISO 时间")
    for canonical, state in metadata_state.items():
        state["is_static"] = state["nonempty_count"] > 0 and len(state["distinct_values"]) == 1
        if canonical == "unit":
            inconsistent = {
                channel: values
                for channel, values in state["values_by_channel"].items()
                if len(values) > 1
            }
            if inconsistent:
                raise TelemetryError(
                    "inconsistent_channel_unit",
                    "同一遥测通道声明了多个单位；服务不会猜测换算关系",
                    details={"channels": inconsistent},
                )
    _validate_model_channel_units(metadata_state)
    return ParsedUpload(
        filename=safe_name,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_count=len(content),
        encoding=table.encoding,
        delimiter=table.delimiter,
        headers=tuple(headers),  # type: ignore[arg-type]
        time_header=headers[semantic_positions["time"]],
        time_hint=_time_hint(headers[semantic_positions["time"]]),
        records=tuple(records),
        channels=tuple(sorted({item.channel for item in records})),
        channel_labels=dict(channel_labels),
        source_format=f"{wrapper}/{table.source_format}" if wrapper else table.source_format,
        embedded_filename=table_filename if wrapper else None,
        metadata={canonical: dict(state) for canonical, state in metadata_state.items()},
        table_layout=layout,
    )


def _validate_archive_entries(
    kind: str,
    entries: Sequence[tuple[Any, str, int, bool, bool]],
) -> tuple[tuple[Any, str], ...]:
    """Validate every member before any archive payload is decompressed."""
    if not entries:
        raise TelemetryError(f"{kind}_member_count", f"{kind.upper()} 文件没有可读取的数据文件")
    if len(entries) > MAX_FILES:
        raise TelemetryError("too_many_files", f"{kind.upper()} 内最多包含 {MAX_FILES} 个数据文件", status=413)

    validated: list[tuple[Any, str]] = []
    normalized_paths: set[str] = set()
    expanded_total = 0
    for token, raw_path, size, encrypted, is_link in entries:
        unsafe_code = f"unsafe_{kind}_member"
        if encrypted:
            raise TelemetryError(unsafe_code, f"{kind.upper()} 文件包含加密成员，不能读取")
        if (not raw_path or "\\" in raw_path or "\x00" in raw_path
                or any(ord(char) < 32 for char in raw_path)):
            raise TelemetryError(unsafe_code, f"{kind.upper()} 文件包含不安全的成员路径，不能读取")
        member_path = PurePosixPath(raw_path)
        if (member_path.is_absolute() or ".." in member_path.parts
                or re.match(r"^[A-Za-z]:", raw_path)):
            raise TelemetryError(unsafe_code, f"{kind.upper()} 文件包含路径穿越或绝对路径，不能读取")
        if is_link:
            raise TelemetryError(unsafe_code, f"{kind.upper()} 文件包含链接成员，不能读取")
        inner = member_path.name
        suffix = _archive_suffix(inner)
        data_suffix = Path(inner).suffix.lower()
        if not inner or suffix is not None or data_suffix not in SUPPORTED_DATA_EXTENSIONS:
            raise TelemetryError(
                "archive_member_unsupported",
                f"{kind.upper()} 成员必须是受支持的数据文件，且不能嵌套压缩包",
                status=415,
                details={"member": raw_path},
            )
        normalized = unicodedata.normalize("NFKC", member_path.as_posix()).casefold()
        if normalized in normalized_paths:
            raise TelemetryError(
                f"duplicate_{kind}_member",
                f"{kind.upper()} 内成员路径规范化后重复，不能唯一识别",
            )
        normalized_paths.add(normalized)
        if size < 0 or size > MAX_EXPANDED_BYTES:
            raise TelemetryError(
                "expanded_file_too_large",
                f"解压后的单个文件不能超过 {MAX_EXPANDED_BYTES // 1024 // 1024} MiB",
                status=413,
                details={"member": raw_path},
            )
        expanded_total += size
        if expanded_total > MAX_TOTAL_BYTES:
            raise TelemetryError(
                "expanded_batch_too_large",
                f"解压后的文件总量不能超过 {MAX_TOTAL_BYTES // 1024 // 1024} MiB",
                status=413,
            )
        validated.append((token, member_path.as_posix()))
    return tuple(validated)


def _archive_parts(
    safe_name: str,
    content: bytes,
    kind: str,
    validated: Sequence[tuple[Any, str]],
    reader: Callable[[Any, str], bytes],
) -> tuple[UploadPart, ...]:
    outer_sha256 = hashlib.sha256(content).hexdigest()
    parts: list[UploadPart] = []
    actual_total = 0
    for token, member_path in validated:
        member_content = reader(token, member_path)
        if not isinstance(member_content, bytes):
            member_content = bytes(member_content)
        if len(member_content) > MAX_EXPANDED_BYTES:
            raise TelemetryError(
                "expanded_file_too_large",
                f"解压后的单个文件不能超过 {MAX_EXPANDED_BYTES // 1024 // 1024} MiB",
                status=413,
                details={"member": member_path},
            )
        actual_total += len(member_content)
        if actual_total > MAX_TOTAL_BYTES:
            raise TelemetryError(
                "expanded_batch_too_large",
                f"解压后的文件总量不能超过 {MAX_TOTAL_BYTES // 1024 // 1024} MiB",
                status=413,
            )
        parts.append(UploadPart(
            filename=f"{safe_name}::{member_path}",
            parse_filename=PurePosixPath(member_path).name,
            content=member_content,
            embedded_filename=member_path,
            container_filename=safe_name,
            container_sha256=outer_sha256,
            wrapper=kind,
        ))
    return tuple(parts)


def _expand_upload_parts(filename: str, content: bytes) -> tuple[UploadPart, ...]:
    """Validate an archive completely, then expose bounded in-memory members."""
    safe_name = _safe_upload_filename(filename)
    suffix = _archive_suffix(safe_name)
    if suffix not in MULTI_ARCHIVE_EXTENSIONS:
        return (UploadPart(safe_name, safe_name, content),)
    if len(content) > MAX_FILE_BYTES:
        raise TelemetryError("file_too_large", f"单文件不能超过 {MAX_FILE_BYTES // 1024 // 1024} MiB", status=413)

    if suffix == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                entries = []
                for member in members:
                    mode = member.external_attr >> 16
                    is_link = bool(mode and (mode & 0o170000) == 0o120000)
                    entries.append((member, member.filename, member.file_size, bool(member.flag_bits & 0x1), is_link))
                validated = _validate_archive_entries("zip", entries)

                def read_zip(member: zipfile.ZipInfo, _member_path: str) -> bytes:
                    with archive.open(member, "r") as stream:
                        return _read_limited(stream)

                return _archive_parts(safe_name, content, "zip", validated, read_zip)
        except TelemetryError:
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise TelemetryError("invalid_zip", "无法读取此 ZIP 文件") from exc

    if suffix == ".7z":
        try:
            if py7zr is None:
                raise TelemetryError("format_unavailable", "当前环境未安装 7Z 读取组件", status=422)
            with py7zr.SevenZipFile(io.BytesIO(content), mode="r") as archive:
                listed = [item for item in archive.list() if not item.is_directory]
                file_state = {item.filename: item for item in archive.files}
                encrypted = archive.needs_password()
                entries = [
                    (item.filename, item.filename, int(item.uncompressed or 0), encrypted,
                     bool(file_state.get(item.filename) and file_state[item.filename].is_symlink))
                    for item in listed
                ]
                validated = _validate_archive_entries("7z", entries)
                unpacked = archive.readall() or {}

                def read_7z(_token: str, member_path: str) -> bytes:
                    stream = unpacked.get(member_path)
                    if stream is None:
                        raise TelemetryError("invalid_7z", "7Z 成员读取不完整")
                    return _read_limited(stream)

                return _archive_parts(safe_name, content, "7z", validated, read_7z)
        except TelemetryError:
            raise
        except Exception as exc:
            raise TelemetryError("invalid_7z", "无法读取此 7Z 文件") from exc

    if suffix == ".rar":
        try:
            if rarfile is None:
                raise TelemetryError("format_unavailable", "当前环境未安装 RAR 读取组件", status=422)
            with rarfile.RarFile(io.BytesIO(content)) as archive:
                members = [item for item in archive.infolist() if not item.isdir()]
                encrypted = archive.needs_password()
                entries = [
                    (item, item.filename, int(item.file_size), encrypted,
                     bool(getattr(item, "is_symlink", lambda: False)()))
                    for item in members
                ]
                validated = _validate_archive_entries("rar", entries)

                def read_rar(member: Any, _member_path: str) -> bytes:
                    return bytes(archive.read(member))

                return _archive_parts(safe_name, content, "rar", validated, read_rar)
        except TelemetryError:
            raise
        except Exception as exc:
            raise TelemetryError(
                "invalid_rar",
                "无法读取此 RAR 文件；运行环境必须安装可用的 unrar、unar 或 7z 解压后端",
            ) from exc

    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            members = [item for item in archive.getmembers() if not item.isdir()]
            entries = [
                (item, item.name, int(item.size), False, not item.isfile())
                for item in members
            ]
            validated = _validate_archive_entries("tar", entries)

            def read_tar(member: tarfile.TarInfo, _member_path: str) -> bytes:
                stream = archive.extractfile(member)
                if stream is None:
                    raise TelemetryError("invalid_tar", "TAR 成员读取不完整")
                with stream:
                    return _read_limited(stream)

            return _archive_parts(safe_name, content, "tar", validated, read_tar)
    except TelemetryError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise TelemetryError("invalid_tar", "无法读取此 TAR 文件") from exc


def _parse_upload_part(part: UploadPart) -> ParsedUpload:
    parsed = parse_upload(part.parse_filename, part.content)
    if part.container_filename is None:
        return parsed
    return replace(
        parsed,
        filename=part.filename,
        source_format=f"{part.wrapper or 'archive'}/{parsed.source_format}",
        embedded_filename=part.embedded_filename,
        container_filename=part.container_filename,
        container_sha256=part.container_sha256,
    )


def parse_uploads(filename: str, content: bytes) -> tuple[ParsedUpload, ...]:
    """Expand one browser upload and parse every discovered logical data file."""
    return tuple(_parse_upload_part(part) for part in _expand_upload_parts(filename, content))


TIME_UNIT_SECONDS = {
    "millisecond": 0.001,
    "second": 1.0,
    "minute": 60.0,
    "hour": 3600.0,
    "day": 86400.0,
}


def _check_time_unit(
    parsed: ParsedUpload,
    requested: str,
    *,
    allowed: set[str],
) -> tuple[str, str]:
    valid = {"auto", "cycle", "bin", *TIME_UNIT_SECONDS}
    if requested not in valid:
        raise TelemetryError(
            "invalid_time_unit",
            "time_unit 只能是 auto、cycle、millisecond、second、minute、hour、day 或 bin",
            status=400,
        )
    inferred = parsed.time_hint
    if requested != "auto" and inferred is not None and requested != inferred:
        raise TelemetryError(
            "time_unit_conflict", "请求声明的 time_unit 与表头单位冲突",
            details={"request": requested, "header": parsed.time_header, "header_unit": inferred},
        )
    selected = inferred if requested == "auto" else requested
    basis = f"header:{parsed.time_header}" if inferred is not None else "user_selected"
    if selected is None:
        raise TelemetryError(
            "time_unit_required", "数值时间列未声明单位；请在界面明确选择 time_unit",
            details={"header": parsed.time_header, "allowed": sorted(allowed)},
        )
    if selected not in allowed:
        raise TelemetryError(
            "unsupported_time_unit", "该模型输入不接受当前时间单位",
            details={"selected": selected, "allowed": sorted(allowed)},
        )
    return selected, basis


def _relevant_steps(parsed: ParsedUpload, channels: Iterable[str]) -> list[tuple[float, str, str, dict[str, float]]]:
    required = set(channels)
    grouped: dict[str, tuple[float, str, str, dict[str, float]]] = {}
    for record in parsed.records:
        if record.channel not in required:
            continue
        existing = grouped.get(record.time_key)
        if existing is None:
            grouped[record.time_key] = (record.time_order, record.time_display, record.time_kind, {record.channel: record.value})
        else:
            existing[3][record.channel] = record.value
    ordered = sorted(grouped.values(), key=lambda item: item[0])
    if not ordered:
        raise TelemetryError("required_telemetry_missing", "未找到模型所需的遥测量")
    missing = [
        {"time": display, "missing": sorted(required - set(values))}
        for _, display, _, values in ordered if required - set(values)
    ]
    if missing:
        raise TelemetryError(
            "incomplete_time_steps", "同一时间点的模型必需遥测量不完整，不能插值或补造",
            details={"examples": missing[:5], "count": len(missing)},
        )
    return ordered


def _require_contiguous_indices(
    steps: list[tuple[float, str, str, dict[str, float]]],
    *,
    route: str,
    label: str,
) -> None:
    """Reject time axes that cannot represent consecutive frozen-model steps."""
    integers: list[int] = []
    for order, display, kind, _ in steps:
        rounded = round(order)
        if kind != "numeric" or not math.isclose(order, rounded, rel_tol=0.0, abs_tol=1e-9):
            raise TelemetryError(
                f"{route}_index_not_integer",
                f"{label}必须是连续的整数索引，不能从墙钟时间或小数间隔推断模型步长",
                details={"time": display},
            )
        if rounded < 0:
            raise TelemetryError(
                f"{route}_index_negative",
                f"{label}不能为负数，冻结模型时间轴从零开始",
                details={"time": display, "index": int(rounded)},
            )
        integers.append(int(rounded))
    if integers and integers[0] != 0:
        raise TelemetryError(
            f"{route}_index_origin",
            f"{label}必须从 0 开始；不能通过平移时间轴掩盖缺失的历史起点",
            details={"first": integers[0]},
        )
    for previous, current in zip(integers, integers[1:]):
        if current != previous + 1:
            raise TelemetryError(
                f"{route}_time_gap",
                f"{label}存在断档；不能把跨未知时段的记录拼成冻结模型窗口",
                details={"previous": previous, "current": current, "missing_from": previous + 1},
            )


def _windows(matrix: np.ndarray, steps: list[tuple[float, str, str, dict[str, float]]], *, length: int, stride: int) -> tuple[np.ndarray, tuple[dict[str, Any], ...]]:
    if len(matrix) < length:
        raise TelemetryError(
            "insufficient_history", "历史观测长度不足，无法组成冻结模型窗口",
            details={"required": length, "received": int(len(matrix))},
        )
    starts = list(range(0, len(matrix) - length + 1, stride))
    if len(starts) > MAX_WINDOWS_PER_FILE:
        raise TelemetryError(
            "too_many_windows", "生成窗口过多；请按设备或时间段拆分后上传",
            status=413, details={"windows": len(starts), "maximum": MAX_WINDOWS_PER_FILE},
        )
    windows = np.stack([matrix[start:start + length] for start in starts]).astype(np.float32, copy=False)
    ends = tuple({
        "index": number,
        "time": steps[start + length - 1][1],
        "time_order": float(steps[start + length - 1][0]),
    } for number, start in enumerate(starts))
    return windows, ends


def _normalise(matrix: np.ndarray, channels: tuple[str, ...], stats: dict[str, Any]) -> tuple[np.ndarray, int]:
    mean = np.asarray([float(stats[channel]["mean"]) for channel in channels], dtype=np.float64)
    std = np.asarray([float(stats[channel]["std"]) for channel in channels], dtype=np.float64)
    if not np.isfinite(mean).all() or not np.isfinite(std).all() or (std < 0).any():
        raise ModelUnavailable("冻结归一化统计量损坏")
    norm = (matrix - mean) / np.where(std > 1e-12, std, 1.0)
    invalid = ~np.isfinite(norm)
    imputed = int(invalid.sum())
    if imputed:
        norm[invalid] = 0.0  # Frozen impute_mean policy: normalised train mean, not an invented raw value.
    return norm.astype(np.float32), imputed


def _ignored_channel_labels(parsed: ParsedUpload, used_channels: Iterable[str]) -> tuple[str, ...]:
    used = set(used_channels)
    return tuple(sorted(
        parsed.channel_labels.get(channel, channel)
        for channel in parsed.channels
        if channel not in used
    ))


def _replay_trace(parsed: ParsedUpload, route: str) -> dict[str, Any] | None:
    """Project accepted source rows into a bounded, display-only trace.

    The inference response intentionally contains predictions, not a second
    copy of the upload.  The replay page nevertheless needs the measured
    signal to remain visible after navigation.  This projection keeps the
    original numeric values and timestamps, inserts ``None`` only where a
    channel was not present at an existing source time, and never interpolates
    or derives a value.  A deterministic stride is used only when the source
    exceeds the UI transport bound; the original record count is retained in
    the metadata so the reduction is explicit.
    """
    if route == "bat":
        preferred = tuple(channel for channel in BAT_CHANNELS if channel in BAT_REQUIRED)
    elif route == "rwa":
        # Raw RWA uploads expose the three base channels; pre-aggregated
        # feature tables expose the derived feature channels.  Keep whichever
        # accepted family is present, preserving the canonical order.
        preferred = tuple(RWA_BASE_CHANNELS) + tuple(RWA_CHANNELS)
    else:
        preferred = tuple(parsed.channels)
    allowed = set(preferred)
    grouped: dict[str, dict[str, Any]] = {}
    for record in parsed.records:
        if record.channel not in allowed:
            continue
        item = grouped.setdefault(record.time_key, {
            "order": float(record.time_order),
            "display": str(record.time_display),
            "kind": str(record.time_kind),
            "values": {},
        })
        # Duplicate channel/time rows are rejected during parsing.  Keeping
        # assignment explicit here protects the projection if that contract
        # changes in a future parser.
        item["values"][record.channel] = float(record.value)
    ordered = sorted(grouped.values(), key=lambda item: item["order"])
    if not ordered:
        return None
    source_points = len(ordered)
    if source_points > MAX_REPLAY_TRACE_POINTS:
        # Select source rows, rather than averaging or interpolating them.
        indices = np.linspace(0, source_points - 1, MAX_REPLAY_TRACE_POINTS, dtype=np.int64).tolist()
        selected = [ordered[int(index)] for index in indices]
    else:
        selected = ordered
    channels = [channel for channel in preferred if any(channel in item["values"] for item in selected)]
    if not channels:
        return None
    return {
        "time_order": [float(item["order"]) for item in selected],
        "time_display": [item["display"] for item in selected],
        "time_kind": selected[0]["kind"],
        "time_header": parsed.time_header,
        "channels": {
            channel: [item["values"].get(channel) for item in selected]
            for channel in channels
        },
        "channel_labels": {channel: parsed.channel_labels.get(channel, channel) for channel in channels},
        "source_points": source_points,
        "display_points": len(selected),
        "downsampled": source_points > len(selected),
    }


def _prepare_bat(parsed: ParsedUpload, *, time_unit: str, stats: dict[str, Any]) -> PreparedInput:
    if "bat.ir_proxy_ohm" in parsed.channels:
        raise TelemetryError(
            "unsupported_bat_ir_proxy_present",
            "当前冻结电池模型训练时内阻代理通道全程缺席；不能把未训练的实测值伪装成已知特征",
        )
    steps = _relevant_steps(parsed, BAT_REQUIRED)
    selected_unit, unit_basis = _check_time_unit(
        parsed,
        time_unit,
        allowed={"cycle"},
    )
    _require_contiguous_indices(steps, route="bat_cycle", label="电池循环索引")
    matrix = np.asarray([
        [values["bat.capacity_ah"], 0.0, values["bat.temp_mean_c"], values["bat.charge_time_s"]]
        for _, _, _, values in steps
    ], dtype=np.float64)
    normalized, imputed = _normalise(matrix, BAT_CHANNELS, stats)
    windows, ends = _windows(normalized, steps, length=60, stride=5)
    ignored = _ignored_channel_labels(parsed, BAT_REQUIRED)
    return PreparedInput(parsed.filename, parsed.sha256, "bat", "cycle_features", windows, ends,
                         len(parsed.records), len(steps), ignored, imputed, selected_unit, unit_basis)


def _rwa_direct_steps(parsed: ParsedUpload) -> list[tuple[float, str, str, dict[str, float]]]:
    return _relevant_steps(parsed, RWA_CHANNELS)


def _prepare_rwa_direct(parsed: ParsedUpload, *, time_unit: str, stats: dict[str, Any]) -> PreparedInput:
    steps = _rwa_direct_steps(parsed)
    selected_unit, unit_basis = _check_time_unit(
        parsed,
        time_unit,
        allowed={"bin"},
    )
    _require_contiguous_indices(steps, route="rwa_feature_bin", label="反作用轮特征桶索引")
    matrix = np.asarray([[values[channel] for channel in RWA_CHANNELS] for _, _, _, values in steps], dtype=np.float64)
    normalized, imputed = _normalise(matrix, RWA_CHANNELS, stats)
    windows, ends = _windows(normalized, steps, length=30, stride=1)
    ignored = _ignored_channel_labels(parsed, RWA_CHANNELS)
    return PreparedInput(parsed.filename, parsed.sha256, "rwa", "preaggregated_features", windows, ends,
                         len(parsed.records), len(steps), ignored, imputed, selected_unit, unit_basis)


def _prepare_rwa_raw(parsed: ParsedUpload, *, time_unit: str, stats: dict[str, Any], baseline: tuple[float, float]) -> PreparedInput:
    selected_unit, unit_basis = _check_time_unit(
        parsed,
        time_unit,
        allowed=set(TIME_UNIT_SECONDS),
    )
    if any(record.time_kind != "numeric" for record in parsed.records if record.channel in RWA_BASE_CHANNELS):
        raise TelemetryError(
            "rwa_bin_phase_undefined",
            "反作用轮原始遥测需要从任务起点累计的数值时间，以保持 574 秒分箱相位；ISO 墙钟时间不能安全推断该相位",
        )
    by_bin: dict[int, dict[str, list[float]]] = {}
    bin_end: dict[int, tuple[float, str]] = {}
    for record in parsed.records:
        if record.channel not in RWA_BASE_CHANNELS:
            continue
        seconds = record.time_order * TIME_UNIT_SECONDS[selected_unit]
        number = math.floor(seconds / 574.0)
        if number < 0:
            raise TelemetryError(
                "rwa_raw_time_negative",
                "反作用轮原始遥测映射出的 574 秒时间桶不能为负数，冻结时间轴从零开始",
                details={"time": record.time_display, "bin": number},
            )
        by_bin.setdefault(number, {}).setdefault(record.channel, []).append(record.value)
        old = bin_end.get(number)
        if old is None or record.time_order > old[0]:
            bin_end[number] = (record.time_order, record.time_display)
    if not by_bin:
        raise TelemetryError("required_telemetry_missing", "未找到反作用轮模型所需的三条原始遥测")
    bins = sorted(by_bin)
    if bins and bins[0] != 0:
        raise TelemetryError(
            "rwa_raw_time_origin",
            "反作用轮原始遥测映射出的 574 秒时间桶必须从 0 开始；不能通过平移时间轴掩盖缺失的历史起点",
            details={"first_bin": bins[0]},
        )
    expected = list(range(bins[0], bins[0] + len(bins)))
    if bins != expected:
        raise TelemetryError(
            "rwa_time_gap", "574 秒聚合桶存在断档，继续生成 30 桶窗口会跨越未知时段",
            details={"first_missing_bin": next((item for item in expected if item not in by_bin), None)},
        )
    steps: list[tuple[float, str, str, dict[str, float]]] = []
    raw_matrix: list[list[float]] = []
    for number in bins:
        values = by_bin[number]
        missing = sorted(set(RWA_BASE_CHANNELS) - set(values))
        if missing:
            raise TelemetryError(
                "incomplete_rwa_bin", "反作用轮聚合桶缺少必需原始遥测量，不能插值或补造",
                details={"bin": number, "missing": missing},
            )
        features: dict[str, float] = {}
        for channel in RWA_BASE_CHANNELS:
            data = np.asarray(values[channel], dtype=np.float64)
            features[f"{channel}.mean"] = float(data.mean())
            features[f"{channel}.std"] = float(data.std(ddof=1)) if len(data) > 1 else float("nan")
            features[f"{channel}.min"] = float(data.min())
            features[f"{channel}.max"] = float(data.max())
        features["rw.fric_tc.mean"] = (
            features["rw.motor_current_a.mean"]
            - (float(baseline[0]) + float(baseline[1]) * features["rw.bearing_temp_c.mean"])
        )
        order, display = bin_end[number]
        steps.append((order, display, "numeric", features))
        raw_matrix.append([features[channel] for channel in RWA_CHANNELS])
    normalized, imputed = _normalise(np.asarray(raw_matrix, dtype=np.float64), RWA_CHANNELS, stats)
    windows, ends = _windows(normalized, steps, length=30, stride=1)
    ignored = _ignored_channel_labels(parsed, RWA_BASE_CHANNELS)
    return PreparedInput(parsed.filename, parsed.sha256, "rwa", "raw_574s_aggregation", windows, ends,
                         len(parsed.records), len(steps), ignored, imputed, selected_unit, unit_basis)


def _validate_prepared_shape(prepared: PreparedInput) -> None:
    """Fail before inference if a future parser change breaks the model ABI."""
    expected = (60, 4) if prepared.route == "bat" else (30, 13)
    matrix = np.asarray(prepared.matrix)
    received = list(matrix.shape[1:]) if matrix.ndim >= 2 else list(matrix.shape)
    if matrix.ndim != 3 or tuple(received) != expected:
        raise TelemetryError(
            "model_shape_mismatch",
            "统一预处理后的窗口形状与当前模型契约不一致，文件未进入推理",
            details={"route": prepared.route, "expected_window_shape": list(expected),
                     "received_matrix_shape": list(matrix.shape)},
        )


def _route_for(parsed: ParsedUpload, requested_line: str) -> str:
    if requested_line not in {"auto", "bat", "rwa"}:
        raise TelemetryError("invalid_line", "line 只能是 auto、bat 或 rwa", status=400)
    channel_set = set(parsed.channels)
    bat = BAT_REQUIRED <= channel_set
    rwa_direct = set(RWA_CHANNELS) <= channel_set
    rwa_raw = set(RWA_BASE_CHANNELS) <= channel_set
    candidates = (["bat"] if bat else []) + (["rwa"] if rwa_direct or rwa_raw else [])
    if not candidates:
        raise TelemetryError(
            "unsupported_telemetry_set", "上传遥测无法映射到任何已封存的生产模型输入契约",
            details={"channels": sorted(channel_set), "battery_required": sorted(BAT_REQUIRED),
                     "rwa_raw_required": list(RWA_BASE_CHANNELS), "rwa_feature_required": list(RWA_CHANNELS)},
        )
    if len(candidates) > 1:
        if requested_line in candidates:
            # A spacecraft file may contain several component families.  An
            # explicit selection is truthful because preparation later lists
            # every channel that did not participate in this model invocation.
            return requested_line
        raise TelemetryError(
            "ambiguous_product_line",
            "同一文件同时包含电池和反作用轮遥测；请明确选择本次预测的部件类型，或拆分后自动识别",
        )
    route = candidates[0]
    if requested_line != "auto" and requested_line != route:
        raise TelemetryError("line_mismatch", "选择的产品线与上传遥测量不匹配", details={"selected": requested_line, "detected": route})
    return route


def _model_upload_channels(prepared: PreparedInput) -> tuple[str, ...]:
    """Return the exact uploaded channels allowed into this prepared matrix."""
    if prepared.input_mode == "cycle_features":
        return tuple(sorted(BAT_REQUIRED))
    if prepared.input_mode == "raw_574s_aggregation":
        return tuple(RWA_BASE_CHANNELS)
    if prepared.input_mode == "preaggregated_features":
        return tuple(RWA_CHANNELS)
    raise RuntimeError(f"unknown prepared input mode: {prepared.input_mode}")


def _rwa_model_ages(prepared: PreparedInput) -> list[float]:
    """Map validated upload time coordinates to the target model's age in days."""
    if prepared.input_mode == "preaggregated_features":
        bin_indices = [int(round(float(end["time_order"]))) for end in prepared.time_ends]
        if any(index < 0 for index in bin_indices):
            raise ModelUnavailable("反作用轮特征桶索引不能为负")
        # Target simulation is sampled every 10 seconds and uses the final
        # observed sample in each half-open 574-second aggregation bucket.
        ages_seconds = [
            math.floor(math.nextafter((index + 1) * 574.0, -math.inf) / 10.0) * 10.0
            for index in bin_indices
        ]
    elif prepared.input_mode == "raw_574s_aggregation":
        seconds_per_unit = TIME_UNIT_SECONDS.get(prepared.time_unit)
        if seconds_per_unit is None:
            raise ModelUnavailable("反作用轮累计时间单位无法映射到模型年龄")
        ages_seconds = [
            float(end["time_order"]) * seconds_per_unit
            for end in prepared.time_ends
        ]
    else:
        raise ModelUnavailable("反作用轮输入模式无法映射到模型年龄")
    if any(not math.isfinite(value) or value < 0.0 for value in ages_seconds):
        raise ModelUnavailable("反作用轮任务累计时间不能为负")
    return [value / 86400.0 for value in ages_seconds]


def _empirical_member_interval(
    member_predictions: Sequence[np.ndarray],
    *,
    rows: Sequence[Mapping[str, Any]],
    postprocess: str,
    rmax: float,
    point_prediction: np.ndarray,
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
    """Apply route safety to each member before deriving empirical bounds."""
    try:
        from src.competition_s22_s21 import CompetitionContractError, monotone_project
    except ImportError as exc:
        raise ModelUnavailable("PyTorch 生产推理模块不可用") from exc
    if len(member_predictions) != len(EXPECTED_ENSEMBLE_SEEDS):
        raise ModelUnavailable("生产模型区间必须来自三个预定成员")
    scale = float(rmax)
    if not math.isfinite(scale) or scale <= 0:
        raise ModelUnavailable("冻结模型寿命尺度无效")
    safe_members: list[np.ndarray] = []
    try:
        for member in member_predictions:
            normalized = np.asarray(member, dtype=np.float64)
            if normalized.ndim != 1 or normalized.shape[0] != len(rows) or not np.isfinite(normalized).all():
                raise ModelUnavailable("生产模型成员预测形状或数值无效")
            projected = monotone_project(normalized, rows, postprocess)
            safe_members.append(np.clip(projected, 0.0, 1.0).astype(np.float64) * scale)
    except CompetitionContractError as exc:
        raise ModelUnavailable("生产模型成员后处理合同无效") from exc
    point = np.asarray(point_prediction, dtype=np.float64)
    if point.shape != safe_members[0].shape or not np.isfinite(point).all():
        raise ModelUnavailable("生产模型点预测形状或数值无效")
    stack = np.stack(safe_members, axis=0)
    lower = np.minimum(np.quantile(stack, 0.10, axis=0), point)
    upper = np.maximum(np.quantile(stack, 0.90, axis=0), point)
    return safe_members, lower, upper


class ProductionPredictor:
    """Load only the frozen component-specific PyTorch production routes."""

    _LEGACY_MANIFEST = "results/competition/s22_s21_20260828/production/manifest.json"
    # A competition transfer manifest is not a sealed production asset.  The
    # deployment must opt in with RUL_DASHBOARD_PRODUCTION_MANIFEST after the
    # production sealing gate; otherwise upload inference stays unavailable.
    _PATHFIX_MANIFEST = "results/competition/__production_manifest_required__/manifest.json"
    _MANIFEST = os.environ.get("RUL_DASHBOARD_PRODUCTION_MANIFEST", "").strip()
    if not _MANIFEST:
        _MANIFEST = _PATHFIX_MANIFEST

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._lock = threading.RLock()
        self._loaded: dict[str, dict[str, Any]] = {}
        self._manifest_sha256: str | None = None

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _three_seed_index(entries: Any, *, route: str) -> dict[int, dict[str, Any]]:
        """Validate the frozen three-member route before loading checkpoints."""
        if not isinstance(entries, list) or len(entries) != len(EXPECTED_ENSEMBLE_SEEDS):
            raise ModelUnavailable("生产模型成员账本必须恰有三个预定成员")
        indexed: dict[int, dict[str, Any]] = {}
        for entry in entries:
            if (not isinstance(entry, dict) or type(entry.get("seed")) is not int
                    or not isinstance(entry.get("path"), str) or not entry["path"]):
                raise ModelUnavailable("生产模型成员账本格式无效")
            seed = entry["seed"]
            if seed not in EXPECTED_ENSEMBLE_SEEDS or seed in indexed:
                raise ModelUnavailable("生产模型成员必须覆盖三个不重复的预定种子")
            indexed[seed] = entry
        if tuple(sorted(indexed)) != EXPECTED_ENSEMBLE_SEEDS:
            raise ModelUnavailable("生产模型成员必须覆盖三个预定种子")
        return indexed

    def _manifest_asset_path(
        self, manifest: Mapping[str, Any], value: Any, label: str
    ) -> Path:
        from src.competition_s22_s21 import _manifest_artifact_path

        aliases = manifest.get("artifact_aliases", {})
        if not isinstance(aliases, Mapping):
            raise TypeError("production manifest artifact aliases are invalid")
        return _manifest_artifact_path(
            self.root, value, label, artifact_aliases=aliases
        )

    def _validated_manifest(
        self,
    ) -> tuple[dict[str, Any], Path, str, dict[str, dict[int, dict[str, Any]]]]:
        """Read the canonical manifest and bind every route member to its bytes."""
        try:
            from src.competition_s22_s21 import (
                EXPECTED_ROUTES,
                _manifest_artifact_path,
                _read_manifest,
            )
            manifest_path = self.root / self._MANIFEST
            manifest_sha256 = self._sha256(manifest_path)
            manifest, resolved = _read_manifest(self.root, manifest_path)
            member_indexes: dict[str, dict[int, dict[str, Any]]] = {}
            for route, route_id in EXPECTED_ROUTES.items():
                spec = manifest["routes"][route]
                if spec.get("route_id") != route_id:
                    raise TypeError("route identity drift")
                point_contract = EXPECTED_POINT_CONTRACTS[route]
                if any(spec.get(key) != value for key, value in point_contract.items()):
                    raise TypeError("route point-prediction contract drift")
                if spec.get("interval_member_seeds") != list(EXPECTED_ENSEMBLE_SEEDS):
                    raise TypeError("route interval-member contract drift")
                indexed = self._three_seed_index(spec.get("members"), route=route)
                for entry in indexed.values():
                    if (
                        not isinstance(entry.get("sha256"), str)
                        or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
                    ):
                        raise TypeError("route member hash is missing")
                    checkpoint = _manifest_artifact_path(
                        self.root, entry["path"], "route checkpoint",
                    )
                    if not checkpoint.is_file() or self._sha256(checkpoint) != entry["sha256"]:
                        raise TypeError("route checkpoint hash drift")
                member_indexes[route] = indexed
            if self._sha256(resolved) != manifest_sha256:
                raise TypeError("production manifest changed during validation")
            return manifest, resolved, manifest_sha256, member_indexes
        except (ImportError, OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise ModelUnavailable("生产模型清单或冻结资产不可用") from exc

    def production_contract(self) -> dict[str, Any]:
        """Expose only route identity proven by the current canonical manifest."""
        with self._lock:
            manifest, _path, manifest_sha256, member_indexes = self._validated_manifest()
            return {
                "status": "validated",
                "framework": "PyTorch",
                "manifest_sha256": manifest_sha256,
                "routes": {
                    route: {
                        "route_id": manifest["routes"][route]["route_id"],
                        "model_label": manifest["routes"][route]["model_label"],
                        "n_members": len(member_indexes[route]),
                        "member_sha256s": [
                            member_indexes[route][seed]["sha256"]
                            for seed in EXPECTED_ENSEMBLE_SEEDS
                        ],
                        "selection_aggregation": manifest["routes"][route]["selection_aggregation"],
                        "production_point_aggregation": manifest["routes"][route]["production_point_aggregation"],
                        "production_point_seed": manifest["routes"][route]["production_point_seed"],
                        "interval_member_seeds": list(manifest["routes"][route]["interval_member_seeds"]),
                        "postprocess": manifest["routes"][route]["postprocess"],
                    }
                    for route in ("bat", "rwa")
                },
            }

    def _load(self, route: str) -> dict[str, Any]:
        with self._lock:
            try:
                from src.competition_s22_s21 import load_route_ensemble
            except ImportError as exc:
                raise ModelUnavailable("生产推理依赖未安装") from exc
            try:
                manifest, manifest_path, manifest_sha256, member_indexes = self._validated_manifest()
                if self._manifest_sha256 == manifest_sha256:
                    cached = self._loaded.get(route)
                    if cached is not None:
                        return cached
                else:
                    self._loaded.clear()
                spec = manifest["routes"][route]
                config_path = self._manifest_asset_path(
                    manifest, manifest["config"], "config"
                )
                norm_path = self._manifest_asset_path(
                    manifest, spec["target_norm"], "target normalization"
                )
                preprocess_path = self._manifest_asset_path(
                    manifest, spec["target_preprocess"], "target preprocessing"
                )
                norm_doc = json.loads(norm_path.read_text(encoding="utf-8"))
                if (
                    self._sha256(config_path) != manifest["config_sha256"]
                    or self._sha256(norm_path) != spec["target_norm_sha256"]
                    or self._sha256(preprocess_path) != spec["target_preprocess_sha256"]
                ):
                    raise TypeError("frozen input hash drift")
                route_model = load_route_ensemble(self.root, manifest_path, route)
                if self._sha256(manifest_path) != manifest_sha256:
                    raise TypeError("production manifest changed during route load")
            except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                raise ModelUnavailable("生产模型清单或冻结输入不可用") from exc
            rmax = float(norm_doc.get("rmax", float("nan")))
            channels = tuple(norm_doc.get("channels", {}))
            expected_channels = BAT_CHANNELS if route == "bat" else RWA_CHANNELS
            if (
                not math.isfinite(rmax) or rmax <= 0
                or set(channels) != set(expected_channels)
                or tuple(route_model.feature_contract.get("channels", ())) != expected_channels
                or not math.isclose(route_model.rmax, rmax, rel_tol=0.0, abs_tol=1e-12)
            ):
                raise ModelUnavailable("冻结模型通道或寿命尺度漂移")
            indexed_members = member_indexes[route]
            loaded = {
                "route_model": route_model,
                "stats": norm_doc["channels"],
                "norm_doc": norm_doc,
                "rmax": rmax,
                "channels": expected_channels,
                "model_label": route_model.label,
                "framework": "PyTorch",
                "manifest_sha256": manifest_sha256,
                "member_sha256s": [
                    indexed_members[seed]["sha256"]
                    for seed in EXPECTED_ENSEMBLE_SEEDS
                ],
                "selection_aggregation": spec["selection_aggregation"],
                "production_point_aggregation": spec["production_point_aggregation"],
                "production_point_seed": spec["production_point_seed"],
                "interval_member_seeds": list(spec["interval_member_seeds"]),
            }
            self._loaded[route] = loaded
            self._manifest_sha256 = manifest_sha256
            return loaded

    def route_stats(self, route: str) -> tuple[dict[str, Any], tuple[float, float] | None]:
        loaded = self._load(route)
        if route != "rwa":
            return loaded["stats"], None
        # Read from the route-specific normalization receipt whose hash was verified in _load.
        norm_doc = loaded["norm_doc"]
        baseline = norm_doc.get("derived", {}).get("rw", {}).get("fric_tc", {}).get("baseline_coef")
        if not isinstance(baseline, list) or len(baseline) != 2 or not np.isfinite(np.asarray(baseline, dtype=float)).all():
            raise ModelUnavailable("冻结反作用轮温补摩擦基线不可用")
        return loaded["stats"], (float(baseline[0]), float(baseline[1]))

    def predict(self, prepared: PreparedInput) -> dict[str, Any]:
        loaded = self._load(prepared.route)
        try:
            from src.competition_s22_s21 import predict_loaded_route
        except ImportError as exc:
            raise ModelUnavailable("PyTorch 生产推理模块不可用") from exc
        contract = loaded["route_model"].feature_contract
        if prepared.route == "bat":
            ages = [float(end["time_order"]) for end in prepared.time_ends]
        else:
            ages = _rwa_model_ages(prepared)
        model_rows = [
            {"unit_id": prepared.filename, "t_end": age}
            for age in ages
        ]
        try:
            result = predict_loaded_route(
                loaded["route_model"], prepared.matrix, model_rows, device_name="cpu"
            )
        except (RuntimeError, ValueError) as exc:
            raise ModelUnavailable("PyTorch 生产推理未能按冻结合同执行") from exc
        rmax = float(loaded["rmax"])
        raw_aggregate = np.asarray(result["raw_normalized"], dtype=np.float64) * rmax
        safe = np.asarray(result["prediction"], dtype=np.float64)
        safe_members, lower, upper = _empirical_member_interval(
            result["member_predictions_normalized"],
            rows=model_rows,
            postprocess=loaded["route_model"].postprocess,
            rmax=rmax,
            point_prediction=safe,
        )
        variance = np.var(np.stack(safe_members, axis=0), axis=0, ddof=0)
        rows = []
        for index, end in enumerate(prepared.time_ends):
            rows.append({
                "window_index": int(end["index"]), "time": end["time"], "time_order": end["time_order"],
                "y_pred_rul_raw": float(raw_aggregate[index]), "y_pred_rul": float(safe[index]),
                "rul_output_clamped": bool(raw_aggregate[index] != safe[index]),
                "p10": float(lower[index]), "p50": float(safe[index]), "p90": float(upper[index]),
                "ensemble_std": float(math.sqrt(max(float(variance[index]), 0.0))),
            })
        epsilon = max(1e-7, 1e-6 * rmax)
        return {
            "route": prepared.route, "model": loaded["model_label"], "rmax": rmax,
            "rul_unit": "cycle" if prepared.route == "bat" else "day",
            "route_id": loaded["route_model"].route_id,
            "framework": loaded["framework"],
            "manifest_sha256": loaded["manifest_sha256"],
            "member_sha256s": list(loaded["member_sha256s"]),
            "selection_aggregation": loaded["selection_aggregation"],
            "production_point_aggregation": loaded["production_point_aggregation"],
            "production_point_seed": loaded["production_point_seed"],
            "interval_member_seeds": list(loaded["interval_member_seeds"]),
            "qhat": None,
            "uncertainty_mode": "three_seed_route_safe_empirical_quantiles_point_enclosed",
            "n_members": len(safe_members), "predictions": rows,
            "output_safety": {
                "mode": loaded["route_model"].postprocess,
                "raw_lower_violation_count": int(np.sum(raw_aggregate < -epsilon)),
                "raw_upper_violation_count": int(np.sum(raw_aggregate > rmax + epsilon)),
                "raw_clamp_count": int(np.sum(raw_aggregate != safe)),
                "lower_bound": 0.0, "upper_bound": rmax,
            },
        }


class TelemetryPredictionService:
    """Stateless input handling plus bounded in-memory CSV result export."""

    def __init__(self, root: str | Path, *, predictor: Any | None = None) -> None:
        self.root = Path(root).resolve()
        self.predictor = predictor if predictor is not None else ProductionPredictor(self.root)
        self._batches: OrderedDict[str, StoredBatch] = OrderedDict()
        self._lock = threading.RLock()
        self._inference_lock = threading.Lock()

    @staticmethod
    def organizer_evaluation_input_status() -> dict[str, Any]:
        """Return the currently verified organizer boundary without inferring fields."""
        return {
            "test_dataset_public": False,
            "allowed_telemetry_requires_permission": True,
            "evaluation_channel_list_publication": "undecided",
            "generic_timeseries_input_requirement": "expected_later",
            "interim_guidance": "可先按电池和反作用轮的通用遥测量开展验证。",
            "brphm_contract_is_organizer_fixed_schema": False,
            "description": "测评数据由组委会独立安排，具体可用于评测的遥测量仍需按相关授权和后续公布的通用时序输入要求执行。当前通道用于说明 BRPHM 已核验模型的输入范围；如有后续要求，项目将以组委会口径为准完成适配。",
        }

    @staticmethod
    def component_scope() -> dict[str, Any]:
        """Describe component-level model scope without inferring spacecraft design."""
        return {
            "battery_only_prediction": {
                "supported": True,
                "reaction_wheel_telemetry_required": False,
                "model_output_scope": "battery_component_rul_only",
                "spacecraft_configuration_inferred_from_missing_telemetry": False,
            },
            "platform_configuration": {
                "model_used": False,
                "role": "provenance_or_display_context_only",
            },
            "attitude_control_method": {
                "model_used": False,
                "role": "provenance_or_display_context_only",
            },
        }

    def schema(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "production_model": self._production_model_contract(),
            "contract_scope": {
                "channel_catalog": "brphm_frozen_model",
                "model_input_contracts": "brphm_frozen_model",
                "organizer_authority": "generic_input_and_dataset_documentation_only",
                "brphm_contract_is_organizer_fixed_schema": False,
            },
            "organizer_evaluation_input_status": self.organizer_evaluation_input_status(),
            "component_scope": self.component_scope(),
            "format": {"accepted_layouts": ["long", "wide"],
                       "required_columns": ["time", "telemetry", "value"],
                       "required_columns_scope": "long_input_and_internal_representation",
                       "wide_required_columns": ["time", "one_or_more_finite_numeric_telemetry_channels"],
                       "internal_layout": "long",
                       "optional_columns": "current_model_provenance_only",
                       "optional_model_activation": "machine_contract_declared_only",
                       "column_order": "adaptive",
                       "extensions": sorted(SUPPORTED_DATA_EXTENSIONS | ARCHIVE_EXTENSIONS),
                       "encoding": ["utf-8", "gb18030"]},
            "limits": {"max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES,
                       "max_total_bytes": MAX_TOTAL_BYTES, "max_records_per_file": MAX_RECORDS_PER_FILE},
            "routes": {
                "bat": {"label": "电池", "required_channels": sorted(BAT_REQUIRED), "window": 60,
                        "time_unit": "cycle", "rul_unit": "cycle",
                        "frozen_absent_channel": "bat.ir_proxy_ohm"},
                "rwa": {"label": "反作用轮", "raw_channels": list(RWA_BASE_CHANNELS),
                        "feature_channels": list(RWA_CHANNELS), "raw_bin_seconds": 574.0,
                        "window": 30, "raw_time_unit": sorted(TIME_UNIT_SECONDS), "feature_time_unit": "bin",
                        "rul_unit": "day"},
            },
            "model_input_contracts": {
                name: {field: list(value) if isinstance(value, tuple) else value
                       for field, value in contract.items()}
                for name, contract in MODEL_INPUT_CONTRACTS.items()
            },
            "model_shape_contracts": {
                name: dict(contract) for name, contract in MODEL_SHAPE_CONTRACTS.items()
            },
            "tree_model_safety": {
                "reason": "树专家按固定特征顺序和维度训练；改变列语义、窗口长度或时间分箱会改变模型输入。",
                "final_shape_checked_before_inference": True,
                "preparation_order": [
                    "parse_container", "detect_long_or_wide", "map_header_semantics",
                    "validate_units_and_time", "sort_and_check_contiguity",
                    "map_declared_channels", "construct_fixed_windows",
                    "validate_final_shape", "run_model",
                ],
                "unknown_columns": "finite_numeric_retained_but_ignored",
                "missing_required_channels": "reject",
                "fabricated_channel_values": "forbidden",
            },
            "batch_time_unit": "per_outer_upload",
            "archive": {
                "multi_member_formats": sorted(MULTI_ARCHIVE_EXTENSIONS),
                "members": "independent",
                "maximum_members": MAX_FILES,
                "nested_archives": False,
                "extraction": "bounded_in_memory",
            },
            "fail_closed": ["missing_field", "ambiguous_field", "ambiguous_wide_column",
                            "mixed_table_layout", "nonfinite_value", "duplicate_observation",
                            "multiple_component_ids", "model_channel_unit_mismatch",
                            "unspecified_time_unit", "missing_required_channel", "non_contiguous_time_axis",
            "target_or_future_feature", "undeclared_derived_feature", "artifact_hash_drift",
            "empty_wide_observation", "bat_cycle_index_negative", "bat_cycle_index_origin",
            "rwa_feature_bin_index_negative", "rwa_feature_bin_index_origin",
            "rwa_raw_time_negative", "rwa_raw_time_origin", "incomplete_rwa_bin"],
            "authority": {
                "source": "competition_faq_20260809.docx",
                "sha256": "A635444A4C8BFB08110E2BEB18CD9ADE9A7ED4A0BCB4DA862AE9A64BE79649CE",
                "document_modified_at": "2026-08-09T00:59:00Z",
                "scope": ["generic_input_semantics", "dataset_documentation_requirements"],
                "excludes": ["routes", "required_channels", "model_input_contracts", "evaluation_channel_list"],
            },
        }

    def public_upload_schema(self) -> dict[str, Any]:
        """Return only the user-facing import contract used by the cockpit."""
        schema = {
            "schema": SCHEMA,
            "production_model": self._public_production_model_contract(),
            "authority": {
                "label": "赛事问题 FAQ",
                "source": "competition_faq_20260809.docx",
                "sha256": "A635444A4C8BFB08110E2BEB18CD9ADE9A7ED4A0BCB4DA862AE9A64BE79649CE",
                "document_modified_at": "2026-08-09T00:59:00Z",
                "relevant_questions": [2, 3, 4, 5, 6, 7, 8, 9],
            },
            "organizer_evaluation_input_status": TelemetryPredictionService.organizer_evaluation_input_status(),
            "component_scope": TelemetryPredictionService.component_scope(),
            "accepted_extensions": sorted(SUPPORTED_DATA_EXTENSIONS | ARCHIVE_EXTENSIONS),
            "fail_closed": ["missing_field", "ambiguous_field", "ambiguous_wide_column",
                            "mixed_table_layout", "nonfinite_value", "duplicate_observation",
                            "multiple_component_ids", "model_channel_unit_mismatch",
                            "unspecified_time_unit", "missing_required_channel", "non_contiguous_time_axis",
                            "target_or_future_feature", "undeclared_derived_feature", "artifact_hash_drift",
                            "empty_wide_observation", "bat_cycle_index_negative", "bat_cycle_index_origin",
                            "rwa_feature_bin_index_negative", "rwa_feature_bin_index_origin",
                            "rwa_raw_time_negative", "rwa_raw_time_origin", "incomplete_rwa_bin"],
            "format_groups": [
                {
                    "label": "分隔文本",
                    "extensions": sorted(TEXT_EXTENSIONS),
                    "description": "含表头的 CSV、TSV 或其他分隔文本；支持三语义长表和可唯一识别的遥测宽表。",
                },
                {
                    "label": "结构化 JSON",
                    "extensions": sorted(JSON_EXTENSIONS),
                    "description": "对象记录数组、表头数组表格或逐行对象记录；字段名必须能唯一映射语义。",
                },
                {
                    "label": "电子表格",
                    "extensions": sorted(SPREADSHEET_EXTENSIONS),
                    "description": "仅一个工作表；首行必须满足长表或宽表表头契约。",
                },
                {
                    "label": "列式数据",
                    "extensions": sorted(COLUMNAR_EXTENSIONS),
                    "description": "Parquet、Feather 或 Arrow 中的一张遥测长表或宽表。",
                },
                {
                    "label": "科学数据",
                    "extensions": sorted(SCIENTIFIC_EXTENSIONS),
                    "description": "具名结构数组，或满足长表/宽表契约的同长一维数组集合。",
                },
                {
                    "label": "安全压缩包",
                    "extensions": sorted(ARCHIVE_EXTENSIONS),
                    "description": "ZIP、7Z、RAR 与 TAR 系列可包含多个受支持数据文件并逐成员预测；GZIP 只包装一个文件且必须保留内层扩展名。",
                },
            ],
            "required_semantics": [
                {"key": "time", "label": "时间", "description": "每条观测的时间或连续索引。"},
                {"key": "telemetry", "label": "遥测量", "description": "该观测所属的遥测通道名称。"},
                {"key": "value", "label": "值", "description": "该时间、该遥测量对应的有限数值。"},
            ],
            "required_semantics_scope": "long_input_and_internal_representation",
            "prediction_input_contract": {
                "description": "当前推理接口的最小输入契约；它不等同于官方对自建航天仿真数据集完整性的要求。",
                "long_required_semantics": ["time", "telemetry", "value"],
                "wide_required_semantics": ["time", "one_or_more_finite_numeric_telemetry_channels"],
                "future_labels_required": False,
            },
            "model_shape_contracts": {
                name: dict(contract) for name, contract in MODEL_SHAPE_CONTRACTS.items()
            },
            "competition_dataset_contract": {
                "description": "官方 FAQ 对自建航天仿真数据集的最低表达要求，可由表内字段与配套数据字典共同满足；这些信息不会因此自动成为模型特征。",
                "required_semantics": [
                    {"key": "time", "label": "时间索引"},
                    {"key": "telemetry_or_field", "label": "遥测量名称或字段"},
                    {"key": "value", "label": "观测值"},
                    {"key": "unit", "label": "单位"},
                    {"key": "component_or_condition", "label": "组件或工况标识"},
                    {"key": "degradation_or_life_label", "label": "退化状态或寿命标签"},
                ],
                "expression": "inline_fields_and_dataset_dictionary",
                "prediction_labels_are_not_required": True,
            },
            "accepted_layouts": {
                "long": {
                    "required_semantics": ["time", "telemetry", "value"],
                    "description": "每行一条观测；其他列作为逐观测溯源上下文。",
                },
                "wide": {
                    "required_semantics": ["time", "one_or_more_finite_numeric_telemetry_channels"],
                    "description": "每行一个时间点；数值遥测各占一列并由服务端确定性展开为内部长表。只有模型契约声明的通道参与推理，其他数值通道保留原表头但忽略。",
                    "unknown_column_policy": "finite_numeric_retained_but_ignored_nonnumeric_rejected",
                    "unit_policy": "已知模型通道按冻结契约校验同量纲且同倍率单位，不做静默换算；多通道宽表的单个 unit 列因归属不明而拒绝。",
                },
                "internal": "long",
            },
            "header_aliases": {
                "time": list(TIME_FIELD_ALIASES),
                "telemetry": ["遥测量", "telemetry", "channel", "signal", "metric", "遥测量(telemetry)"],
                "value": ["值", "value", "reading", "measurement", "值(value)"],
            },
            "time_units": [
                {"key": "auto", "label": "由表头判断", "canonical_header": None,
                 "accepted_headers": [], "example": "仅在表头精确声明下列单位时生效；普通 time 必须手动选择"},
                {"key": "cycle", "label": "循环序号", "canonical_header": "cycle",
                 "accepted_headers": list(TIME_HEADER_UNIT_ALIASES["cycle"]), "example": "0, 1, 2, ..."},
                {"key": "millisecond", "label": "毫秒", "canonical_header": "time_ms",
                 "accepted_headers": list(TIME_HEADER_UNIT_ALIASES["millisecond"]), "example": "0, 1000, 2000, ..."},
                {"key": "second", "label": "秒", "canonical_header": "time_s",
                 "accepted_headers": list(TIME_HEADER_UNIT_ALIASES["second"]), "example": "0, 1.5, 3.0, ..."},
                {"key": "minute", "label": "分钟", "canonical_header": "time_min",
                 "accepted_headers": list(TIME_HEADER_UNIT_ALIASES["minute"]), "example": "0, 0.5, 1.0, ..."},
                {"key": "hour", "label": "小时", "canonical_header": "time_h",
                 "accepted_headers": list(TIME_HEADER_UNIT_ALIASES["hour"]), "example": "0, 1, 2, ..."},
                {"key": "day", "label": "天", "canonical_header": "time_day",
                 "accepted_headers": list(TIME_HEADER_UNIT_ALIASES["day"]), "example": "0, 0.25, 0.5, ..."},
                {"key": "bin", "label": "已聚合时间桶", "canonical_header": "time_bin",
                 "accepted_headers": list(TIME_HEADER_UNIT_ALIASES["bin"]),
                 "example": "0, 1, 2, ...；仅用于已按模型契约聚合的特征"},
            ],
            "telemetry_channels": {
                "battery": [
                    {"name": "bat.capacity_ah", "label": "容量", "unit": "Ah", "required": True,
                     "aliases": list(CHANNEL_ALIAS_GROUPS["bat.capacity_ah"])},
                    {"name": "bat.temp_mean_c", "label": "平均温度", "unit": "degC", "required": True,
                     "aliases": list(CHANNEL_ALIAS_GROUPS["bat.temp_mean_c"])},
                    {"name": "bat.charge_time_s", "label": "充电时长", "unit": "s", "required": True,
                     "aliases": list(CHANNEL_ALIAS_GROUPS["bat.charge_time_s"])},
                ],
                "reaction_wheel_raw": [
                    {"name": "rw.speed_rpm", "label": "转速", "unit": "rpm", "required": True,
                     "aliases": list(CHANNEL_ALIAS_GROUPS["rw.speed_rpm"])},
                    {"name": "rw.motor_current_a", "label": "电机电流", "unit": "A", "required": True,
                     "aliases": list(CHANNEL_ALIAS_GROUPS["rw.motor_current_a"])},
                    {"name": "rw.bearing_temp_c", "label": "轴承温度", "unit": "degC", "required": True,
                     "aliases": list(CHANNEL_ALIAS_GROUPS["rw.bearing_temp_c"])},
                ],
                "reaction_wheel_features": list(RWA_CHANNELS),
            },
            "unit_validation": {
                "mode": "exact_dimension_and_scale_no_conversion",
                "optional_when_absent": True,
                "applies_when_declared": True,
                "expected_by_channel": dict(MODEL_CHANNEL_UNITS),
                "accepted_same_scale_spellings": {
                    canonical: list(aliases)
                    for canonical, aliases in UNIT_VALUE_ALIAS_GROUPS.items()
                },
                "examples_rejected_without_conversion": ["mAh for bat.capacity_ah", "ms for bat.charge_time_s"],
            },
            "optional_semantics": {
                "role": "bounded_file_provenance_summary_only",
                "model_used": False,
                "activation": "machine_model_contract_only",
                "description": "单位、组件、工况、轨道、退化状态和寿命/失效标签仅返回文件级计数与最多 8 个不同值的摘要；该摘要不保留逐时对齐，不能据此宣称完成逐时标签评价。当前冻结模型未声明这些输入，因此不参与推理。",
                "summary_scope": "file",
                "time_aligned": False,
                "distinct_values_limit": 8,
                "fields": [dict(field) for field in OPTIONAL_METADATA_FIELDS],
                "aliases": {name: list(aliases) for name, aliases in OPTIONAL_METADATA_ALIAS_GROUPS.items()},
                "target_fields": sorted(TARGET_METADATA_FIELDS),
                "target_and_future_policy": "never_model_feature",
                "orbit_fields": [dict(field) for field in OPTIONAL_METADATA_FIELDS
                                 if field["name"].startswith("orbit")],
            },
            "dataset_documentation": {
                "description": "官方 FAQ 要求自建数据随数据字典说明字段含义、采样频率、单位、缺失值规则、数据划分方法和标签生成依据。这里是必须说明的复现信息，不替代当前模型的机读输入契约。",
                "required_items": [
                    "field_meaning", "sampling_frequency", "engineering_unit", "missing_value_rule",
                    "split_method", "label_generation_basis", "component_or_condition", "degradation_or_life_label",
                ],
                "derived_telemetry_policy": "派生遥测仅在来源、计算或测量链、预测时可获得性、采样特性和退化关系可追溯，且已由模型契约声明时，才可进入模型；标签和未来信息始终禁止。",
            },
            "layout": {
                "one_observation_per_row": True,
                "one_observation_per_row_scope": "internal_and_long_input",
                "accepted_input_layouts": ["long", "wide"],
                "wide_conversion": "deterministic_server_side_unpivot",
                "headers_are_semantic": True,
                "column_order_flexible": True,
                "encodings": ["UTF-8", "GB18030"],
            },
            "tree_model_safety": {
                "reason": "树专家按固定特征顺序和维度训练；改变列语义、窗口长度或时间分箱会改变模型输入，即使表格可读取也不能直接推理。",
                "final_shape_checked_before_inference": True,
                "preparation_order": [
                    "parse_container", "detect_long_or_wide", "map_header_semantics",
                    "validate_units_and_time", "sort_and_check_contiguity",
                    "map_declared_channels", "construct_fixed_windows",
                    "validate_final_shape", "run_model",
                ],
                "unknown_columns": "finite_numeric_retained_but_ignored",
                "missing_required_channels": "逐文件拒绝",
                "fabricated_channel_values": "forbidden",
                "shape_contracts": {
                    name: dict(contract) for name, contract in MODEL_SHAPE_CONTRACTS.items()
                },
            },
            "safety": {
                "summary": "只接受能唯一还原为时间、遥测量、值内部语义的长表或宽表。缺少字段、文本宽列、"
                           "重复观测、多个组件标识、模型单位倍率不符、非有限值或无法安全解析的文件会拒绝；"
                           "标签、未来信息及未声明派生遥测不进入模型。",
                "model_feature_policy": {
                    "allow": "machine_model_contract_only",
                    "optional_metadata": "bounded_file_summary_only_not_time_aligned",
                    "targets_and_future_information": "blocked",
                    "unknown_long_channels": "retained_but_ignored",
                    "unknown_wide_columns": "finite_numeric_retained_but_ignored_nonnumeric_rejected",
                },
                "rejected_formats": sorted(UNSAFE_SERIALIZED_EXTENSIONS),
            },
            "limits": {
                "max_files": MAX_FILES,
                "max_file_bytes": MAX_FILE_BYTES,
                "max_total_bytes": MAX_TOTAL_BYTES,
                "max_records_per_file": MAX_RECORDS_PER_FILE,
                "max_columns": MAX_COLUMNS,
            },
        }

        def public_text(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace("契约", "输入要求")
            if isinstance(value, list):
                return [public_text(item) for item in value]
            if isinstance(value, dict):
                return {key: public_text(item) for key, item in value.items()}
            return value

        return public_text(schema)

    def _production_model_contract(self) -> dict[str, Any]:
        if not hasattr(self.predictor, "production_contract"):
            return {"status": "unavailable", "reason": "生产模型未提供可核验的路线清单"}
        try:
            contract = self.predictor.production_contract()
        except ModelUnavailable as exc:
            return {"status": "unavailable", "reason": exc.message}
        if not isinstance(contract, dict) or contract.get("status") != "validated":
            return {"status": "unavailable", "reason": "生产模型路线清单未通过核验"}
        return contract

    def _public_production_model_contract(self) -> dict[str, Any]:
        """Project the validated production contract into evaluator-facing data.

        Route IDs and internal manifest labels are deliberately retained only in
        the server-side contract.  The browser receives stable component names
        and the provenance needed to explain a prediction, without development
        aliases that have no meaning to an evaluator.
        """
        contract = self._production_model_contract()
        if contract.get("status") != "validated":
            return contract
        routes = contract.get("routes")
        if not isinstance(routes, dict):
            return {"status": "unavailable", "reason": "生产模型公开合同未通过核验"}
        public_routes: dict[str, dict[str, Any]] = {}
        for route in ("bat", "rwa"):
            source = routes.get(route)
            if not isinstance(source, dict):
                return {"status": "unavailable", "reason": "生产模型公开合同未通过核验"}
            public_routes[route] = {
                "model_name": _PUBLIC_MODEL_NAMES[route],
                "component_name": _PUBLIC_COMPONENT_NAMES[route],
                "framework": "PyTorch",
                "n_members": source.get("n_members"),
                "selection_method": "三个独立模型结果取中位数",
                "point_prediction_method": "三个独立模型结果取中位数",
                "range_method": "三个独立模型形成经验预测范围",
                "monotonicity_adjustment": "按退化方向校正预测曲线",
            }
        return {
            "status": "validated",
            "framework": "PyTorch",
            "modelVersion_sha256": contract.get("manifest_sha256"),
            "components": public_routes,
        }

    def _stats(self, route: str) -> tuple[dict[str, Any], tuple[float, float] | None]:
        if hasattr(self.predictor, "route_stats"):
            return self.predictor.route_stats(route)
        # Tests may supply a minimal deterministic predictor with explicit static stats.
        if route == "bat":
            return {channel: {"mean": 0.0, "std": 1.0} for channel in BAT_CHANNELS}, None
        return {channel: {"mean": 0.0, "std": 1.0} for channel in RWA_CHANNELS}, (0.0, 0.0)

    def _prepare(self, parsed: ParsedUpload, line: str, time_unit: str) -> PreparedInput:
        route = _route_for(parsed, line)
        stats, baseline = self._stats(route)
        if route == "bat":
            prepared = _prepare_bat(parsed, time_unit=time_unit, stats=stats)
        elif set(RWA_CHANNELS) <= set(parsed.channels):
            prepared = _prepare_rwa_direct(parsed, time_unit=time_unit, stats=stats)
        else:
            assert baseline is not None
            prepared = _prepare_rwa_raw(parsed, time_unit=time_unit, stats=stats, baseline=baseline)
        _validate_prepared_shape(prepared)
        return prepared

    @staticmethod
    def _csv(results: list[dict[str, Any]]) -> bytes:
        stream = io.StringIO(newline="")
        fields = ("filename", "input_sha256", "container_filename", "container_sha256", "archive_member", "line", "model_name", "framework", "manifest_sha256", "member_sha256s", "selection_aggregation", "production_point_aggregation", "production_point_seed", "interval_member_seeds", "uncertainty_mode", "window_index", "time", "time_order", "rul_unit",
                  "y_pred_rul_raw", "y_pred_rul", "rul_output_clamped", "p10", "p50", "p90", "ensemble_std")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in results:
            for prediction in item.get("predictions", []):
                archive = item.get("input_contract", {}).get("archive") or {}
                writer.writerow({"filename": item["filename"], "input_sha256": item["sha256"],
                                 "container_filename": archive.get("filename", ""),
                                 "container_sha256": archive.get("sha256", ""),
                                 "archive_member": archive.get("member", ""),
                                 "line": item["line"], "model_name": item["public_model_name"],
                                 "framework": item["framework"],
                                 "manifest_sha256": item["manifest_sha256"],
                                 "member_sha256s": ";".join(item["member_sha256s"]),
                                 "selection_aggregation": item["selection_aggregation"],
                                 "production_point_aggregation": item["production_point_aggregation"],
                                 "production_point_seed": item["production_point_seed"] if item["production_point_seed"] is not None else "",
                                 "interval_member_seeds": ";".join(str(seed) for seed in item["interval_member_seeds"]),
                                 "uncertainty_mode": item["uncertainty_mode"],
                                 "rul_unit": item["rul_unit"], **prediction})
        # The browser template uses BOM too.  Keep exported Chinese filenames
        # readable when opened directly in common Windows spreadsheet tools.
        return stream.getvalue().encode("utf-8-sig")

    def _store(self, content: bytes) -> str:
        with self._lock:
            now = time.monotonic()
            expired = [key for key, batch in self._batches.items()
                       if now - batch.created_monotonic > RESULT_TTL_SECONDS]
            for key in expired:
                self._batches.pop(key, None)
            batch_id = secrets.token_hex(16)
            self._batches[batch_id] = StoredBatch(now, content)
            while len(self._batches) > MAX_STORED_BATCHES:
                self._batches.popitem(last=False)
            return batch_id

    def export(self, batch_id: str) -> bytes | None:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None or time.monotonic() - batch.created_monotonic > RESULT_TTL_SECONDS:
                self._batches.pop(batch_id, None)
                return None
            return batch.content

    @staticmethod
    def _time_units_for_uploads(time_unit: str | list[str], count: int) -> list[str]:
        if isinstance(time_unit, str):
            return [time_unit] * count
        if not isinstance(time_unit, list) or not all(isinstance(value, str) for value in time_unit):
            raise TelemetryError("invalid_time_unit", "time_unit 必须是字符串或与上传文件一一对应的字符串列表", status=400)
        if not time_unit:
            return ["auto"] * count
        if len(time_unit) == 1:
            return list(time_unit) * count
        if len(time_unit) != count:
            raise TelemetryError(
                "time_unit_count_mismatch",
                "批量上传时每个文件都要有一个对应的时间单位声明",
                status=400,
                details={"files": count, "time_units": len(time_unit)},
            )
        return list(time_unit)

    def predict_files(self, uploads: list[tuple[str, bytes]], *, line: str = "auto", time_unit: str | list[str] = "auto") -> tuple[int, dict[str, Any]]:
        if not uploads:
            raise TelemetryError("file_required", "请至少上传一个遥测表格文件", status=400)
        if len(uploads) > MAX_FILES:
            raise TelemetryError("too_many_files", f"一次最多上传 {MAX_FILES} 个文件", status=413)
        total = sum(len(content) for _, content in uploads)
        if total > MAX_TOTAL_BYTES:
            raise TelemetryError("batch_too_large", f"批量上传不能超过 {MAX_TOTAL_BYTES // 1024 // 1024} MiB", status=413)
        time_units = self._time_units_for_uploads(time_unit, len(uploads))
        expanded: list[tuple[ParsedUpload, str]] = []
        results: list[dict[str, Any]] = []
        successful: list[dict[str, Any]] = []
        unavailable = 0
        expanded_bytes = 0
        discovered = 0
        for (filename, content), file_time_unit in zip(uploads, time_units):
            try:
                parts = _expand_upload_parts(filename, content)
                if discovered + len(parts) > MAX_FILES:
                    raise TelemetryError("too_many_files", f"解压后一次最多处理 {MAX_FILES} 个数据文件", status=413)
                discovered += len(parts)
                expanded_bytes += sum(len(part.content) for part in parts)
                if expanded_bytes > MAX_TOTAL_BYTES:
                    raise TelemetryError(
                        "expanded_batch_too_large",
                        f"解压后的批量数据不能超过 {MAX_TOTAL_BYTES // 1024 // 1024} MiB",
                        status=413,
                    )
                for part in parts:
                    try:
                        expanded.append((_parse_upload_part(part), file_time_unit))
                    except TelemetryError as exc:
                        results.append({"filename": part.filename, "status": "rejected", "error": exc.as_dict()})
            except TelemetryError as exc:
                results.append({"filename": _safe_upload_filename(filename), "status": "rejected", "error": exc.as_dict()})

        for parsed, file_time_unit in expanded:
            try:
                prepared = self._prepare(parsed, line, file_time_unit)
                with self._inference_lock:
                    predicted = self.predictor.predict(prepared)
                item = {
                    "filename": parsed.filename, "sha256": parsed.sha256, "status": "predicted",
                    "line": predicted["route"], "rul_unit": predicted["rul_unit"],
                    "input_contract": {"mode": prepared.input_mode, "source_format": parsed.source_format,
                                       "source_layout": parsed.table_layout,
                                       "internal_layout": "long",
                                       "embedded_filename": parsed.embedded_filename,
                                       "archive": ({"filename": parsed.container_filename,
                                                    "member": parsed.embedded_filename,
                                                    "format": parsed.source_format.split("/", 1)[0],
                                                    "sha256": parsed.container_sha256}
                                                   if parsed.container_filename else None),
                                       "source_records": prepared.source_records,
                                       "source_time_points": prepared.source_time_points,
                                       "windows": int(len(prepared.matrix)), "ignored_channels": list(prepared.ignored_channels),
                                       "normalised_imputed_cells": prepared.imputed_cells,
                                        "time_unit": prepared.time_unit,
                                        "time_unit_basis": prepared.time_unit_basis,
                                        "optional_metadata": parsed.metadata,
                                        "optional_metadata_scope": "bounded_file_summary_only",
                                        "optional_metadata_time_aligned": False,
                                        "model_upload_channels": list(_model_upload_channels(prepared)),
                                        "model_feature_policy": "machine_model_contract_only",
                                        "target_future_and_metadata_model_used": False,
                                        # This is a bounded view of the
                                        # accepted source rows for the replay
                                        # page only.  It is not a model input
                                        # and is never included in the CSV
                                        # result export.
                                        "replay_trace": _replay_trace(parsed, predicted["route"]),
                                        "optional_metadata_model_used": False},
                    "model": predicted["model"], "public_model_name": _PUBLIC_MODEL_NAMES[predicted["route"]],
                    "rmax": predicted["rmax"], "qhat": predicted["qhat"],
                    "framework": predicted["framework"],
                    "manifest_sha256": predicted["manifest_sha256"],
                    "member_sha256s": list(predicted["member_sha256s"]),
                    "selection_aggregation": predicted["selection_aggregation"],
                    "production_point_aggregation": predicted["production_point_aggregation"],
                    "production_point_seed": predicted["production_point_seed"],
                    "interval_member_seeds": list(predicted["interval_member_seeds"]),
                    "uncertainty_mode": predicted["uncertainty_mode"],
                    "n_members": predicted["n_members"], "output_safety": predicted["output_safety"],
                    "predictions": predicted["predictions"],
                }
                results.append(item)
                successful.append(item)
            except ModelUnavailable as exc:
                unavailable += 1
                results.append({"filename": parsed.filename, "status": "rejected", "error": exc.as_dict()})
            except TelemetryError as exc:
                results.append({"filename": parsed.filename, "status": "rejected", "error": exc.as_dict()})
            except Exception:  # Do not leak implementation detail or turn a malformed upload into a 500 page.
                results.append({"filename": parsed.filename, "status": "rejected",
                                "error": {"code": "prediction_failed", "message": "推理未完成，未生成预测结果"}})
        batch_id = self._store(self._csv(successful)) if successful else None
        counts = {"submitted": len(uploads), "predicted": len(successful),
                  "rejected": len(results) - len(successful)}
        status = "complete" if counts["rejected"] == 0 else ("partial" if successful else "rejected")
        code = 200 if status == "complete" else (207 if status == "partial" else (503 if unavailable == len(results) else 422))
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "status": status,
            "counts": counts,
            "discovered_data_files": len(results),
            "results": results,
        }
        if batch_id is not None:
            payload["batch_id"] = batch_id
            payload["export"] = {"csv": f"/api/telemetry/results/{batch_id}.csv", "expires_seconds": RESULT_TTL_SECONDS}
        return code, payload
