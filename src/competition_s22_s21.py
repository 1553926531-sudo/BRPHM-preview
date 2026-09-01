"""Pure-PyTorch BAT S22 and RWA S21 competition routes.

The module owns one contract across public-domain pretraining, target-domain
transfer, and prediction.  It deliberately keeps target holdout material out
of both fitting stages; holdout is only materialized by the prediction CLI.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import torch
from torch import nn


SCHEMA_CONFIG = "competition-s22-s21-config-1.0"
SCHEMA_PRETRAIN = "competition-public-pretrain-checkpoint-1.0"
SCHEMA_TRANSFER = "competition-route-checkpoint-1.0"
SCHEMA_MANIFEST = "competition-s22-s21-manifest-1.0"
SCHEMA_RECEIPT = "competition-s22-s21-receipt-1.0"
FEATURE_SCHEMA = "competition-channel-statistics-age-1.0"
BOOSTER_SCHEMA = "torch-histogram-gradient-boosting-1.1"
STATISTICS = (
    "last", "first", "delta", "mean", "std", "min", "max", "q25",
    "q50", "q75", "slope", "tail_mean", "head_mean",
)
INVARIANT_REDUCERS = ("mean", "std", "min", "max")
AGE_FEATURES = ("age_ratio", "age_ratio_squared", "age_ratio_sqrt")
EXPECTED_ROUTES = {"bat": "S22", "rwa": "S21"}
DEFAULT_SEEDS = (17, 42, 73)
# Updated whenever the isolated research-derived route contract changes.
CANONICAL_CONFIG_SHA256 = "0827b6d60c9662032a97c873e1537a30984ac372b437173b13e749b32368d29d"
LEGACY_IMPLEMENTATION_SHA256 = "a0c20be5ecdf33075705941fed48a57ce2cdd659ad5d6b4502a11a6faf92a0cd"
FROZEN_TREE_CONTRACTS = {
    "bat": {
        "feature": "channel_statistics_v1",
        "n_trees": 800,
        "max_leaf_nodes": 31,
        "max_bins": 255,
        "min_samples_leaf": 5,
        "learning_rate": 0.03,
        "l2_leaf": 0.005,
    },
    "rwa": {
        "feature": "flat_window",
        "n_trees": 800,
        "max_leaf_nodes": 63,
        "max_bins": 255,
        "min_samples_leaf": 20,
        "learning_rate": 0.03,
        "l2_leaf": 0.001,
    },
}
FROZEN_ROUTE_BEHAVIOR = {
    "bat": {
        "selection_aggregation": "median3",
        "production_point_aggregation": "median3",
        "production_point_seed": None,
        "interval_member_seeds": list(DEFAULT_SEEDS),
        "production_refit_epoch": 169,
        "segmentation": {
            "age_field": "t_end", "threshold": 139.0,
            "low_target_weight": 0.2, "high_target_weight": 0.7,
        },
        "postprocess": "isotonic_nonincreasing",
    },
    "rwa": {
        "selection_aggregation": "median3",
        "production_point_aggregation": "median3",
        "production_point_seed": None,
        "interval_member_seeds": list(DEFAULT_SEEDS),
        "production_refit_epoch": 61,
        "segmentation": {
            "age_field": "t_end", "threshold": 0.25243055555555555,
            "low_target_weight": 0.7, "high_target_weight": 1.0,
        },
        "postprocess": "isotonic_nonincreasing",
    },
}


class CompetitionContractError(RuntimeError):
    """Raised when a competition route artifact violates the frozen contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    """Hash tensor bytes and canonical metadata for materialized prediction input."""
    digest = hashlib.sha256()
    digest.update(b"competition-materialized-payload-1.0\0")
    for name in ("x", "y_rul"):
        value = payload.get(name)
        digest.update(name.encode("ascii") + b"\0")
        if value is None:
            digest.update(b"none\0")
            continue
        if not isinstance(value, torch.Tensor):
            raise CompetitionContractError(f"payload {name} must be a torch tensor")
        tensor = value.detach().cpu().contiguous()
        digest.update(str(tensor.dtype).encode("ascii") + b"\0")
        digest.update(json.dumps(list(tensor.shape), separators=(",", ":")).encode("ascii") + b"\0")
        digest.update(tensor.numpy().tobytes(order="C"))
        digest.update(b"\0")
    meta = payload.get("meta")
    if not isinstance(meta, Mapping):
        raise CompetitionContractError("payload metadata must be a mapping")
    try:
        canonical_meta = json.dumps(
            meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompetitionContractError("payload metadata is not canonical JSON") from exc
    digest.update(b"meta\0")
    digest.update(canonical_meta)
    return digest.hexdigest()


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _implementation_contract(root: Path) -> dict[str, str]:
    implementation = Path(__file__).resolve()
    return {
        "implementation": relative_path(implementation, root),
        "implementation_sha256": sha256(implementation),
    }


def _validate_implementation_contract(
    artifact: Mapping[str, Any], root: Path, label: str,
) -> None:
    expected = _implementation_contract(root)
    implementation = artifact.get("implementation")
    implementation_sha256 = artifact.get("implementation_sha256")
    if (
        implementation == expected["implementation"]
        and implementation_sha256 == expected["implementation_sha256"]
    ):
        return
    if implementation == "src/competition_s22_s21.py" and implementation_sha256 == LEGACY_IMPLEMENTATION_SHA256:
        return
    if implementation != expected["implementation"]:
        raise CompetitionContractError(f"{label} implementation path drift")
    raise CompetitionContractError(f"{label} implementation hash drift")


def _json_dump(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompetitionContractError(f"cannot read competition config: {config_path}") from exc
    if config.get("schema") != SCHEMA_CONFIG:
        raise CompetitionContractError("competition config schema mismatch")
    lines = config.get("lines")
    if not isinstance(lines, dict) or set(lines) != set(EXPECTED_ROUTES):
        raise CompetitionContractError("competition config must define exactly BAT and RWA")
    for component, route in EXPECTED_ROUTES.items():
        spec = lines[component]
        if not isinstance(spec, dict) or spec.get("route_id") != route:
            raise CompetitionContractError(f"{component.upper()} route must be {route}")
        if spec.get("framework") != "pytorch":
            raise CompetitionContractError(f"{component.upper()} must use PyTorch")
        seeds = spec.get("seeds")
        if not isinstance(seeds, list) or len(seeds) != 3 or any(type(v) is not int for v in seeds):
            raise CompetitionContractError(f"{component.upper()} must define three integer seeds")
        if tuple(seeds) != DEFAULT_SEEDS:
            raise CompetitionContractError(f"{component.upper()} seeds must be 17/42/73")
        frozen_behavior = FROZEN_ROUTE_BEHAVIOR[component]
        if "aggregation" in spec:
            raise CompetitionContractError(
                f"{component.upper()} config uses retired aggregation field"
            )
        if any(spec.get(key) != value for key, value in frozen_behavior.items()):
            raise CompetitionContractError(f"{component.upper()} frozen route behavior drift")
        transfer_config = spec.get("transfer")
        if not isinstance(transfer_config, dict):
            raise CompetitionContractError(f"{component.upper()} transfer config is missing")
        for key in ("shared_aux_weight", "shared_output_weight"):
            value = transfer_config.get(key)
            if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                raise CompetitionContractError(f"{component.upper()} {key} must be in [0,1]")
        tree_config = spec.get("tree_expert")
        if not isinstance(tree_config, dict) or "max_leaf_nodes" not in tree_config:
            raise CompetitionContractError(f"{component.upper()} tree expert config is incomplete")
        retired_tree_keys = {"max_depth", "feature_subsample", "seed"} & set(tree_config)
        if retired_tree_keys:
            raise CompetitionContractError(
                f"{component.upper()} tree config contains unsupported fields: {sorted(retired_tree_keys)}"
            )
        if tree_config != FROZEN_TREE_CONTRACTS[component]:
            raise CompetitionContractError(f"{component.upper()} tree expert contract drift")
    if sha256(config_path) != CANONICAL_CONFIG_SHA256:
        raise CompetitionContractError("competition canonical config hash drift")
    config["_path"] = str(config_path)
    return config


def validate_route_args(config: Mapping[str, Any], bat_route: str, rwa_route: str) -> None:
    requested = {"bat": str(bat_route).upper(), "rwa": str(rwa_route).upper()}
    for component, expected in EXPECTED_ROUTES.items():
        actual = str(config["lines"][component]["route_id"]).upper()
        if requested[component] != expected or actual != expected:
            raise CompetitionContractError(
                f"route identity is frozen: BAT=S22 and RWA=S21; got {requested}"
            )


def resolve_root(config: Mapping[str, Any], root: str | Path | None) -> Path:
    if root is not None:
        return Path(root).resolve()
    config_path = Path(str(config["_path"]))
    # configs/competition/<file>.json -> repository root
    return config_path.parents[2]


def resolve_data_root(root: Path, config: Mapping[str, Any]) -> Path:
    value = config.get("data_root")
    if not isinstance(value, str) or not value:
        raise CompetitionContractError("config data_root is missing")
    candidate = (root / value).resolve()
    if candidate.is_dir():
        return candidate
    package_data = root / "data" / "processed"
    if package_data.is_dir():
        return root.resolve()
    return candidate


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def torch_load(path: str | Path) -> Any:
    return torch.load(Path(path), map_location="cpu", weights_only=False)


def load_tensor(path: str | Path, *, expected_task: str | None = None) -> dict[str, Any]:
    tensor_path = Path(path)
    try:
        payload = torch_load(tensor_path)
    except Exception as exc:
        raise CompetitionContractError(f"cannot load tensor: {tensor_path}") from exc
    if not isinstance(payload, dict):
        raise CompetitionContractError(f"tensor payload must be a mapping: {tensor_path}")
    x, y, meta = payload.get("x"), payload.get("y_rul"), payload.get("meta")
    if not isinstance(x, torch.Tensor) or x.ndim != 3 or x.dtype != torch.float32:
        raise CompetitionContractError(f"x must be float32 [N,L,C]: {tensor_path}")
    if not isinstance(y, torch.Tensor) or y.ndim != 1 or y.shape[0] != x.shape[0]:
        raise CompetitionContractError(f"y_rul must align with x: {tensor_path}")
    if not isinstance(meta, dict) or not isinstance(meta.get("index"), list):
        raise CompetitionContractError(f"tensor metadata is incomplete: {tensor_path}")
    if len(meta["index"]) != x.shape[0]:
        raise CompetitionContractError(f"metadata index must align with x: {tensor_path}")
    if not torch.isfinite(x).all() or not torch.isfinite(y).all():
        raise CompetitionContractError(f"tensor contains non-finite values: {tensor_path}")
    if expected_task is not None and str(payload.get("task")) != expected_task:
        raise CompetitionContractError(
            f"tensor task mismatch: expected {expected_task}, got {payload.get('task')}"
        )
    return payload


def _load_tensor_snapshot(
    path: str | Path, *, expected_task: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    tensor_path = Path(path).resolve()
    try:
        before = sha256(tensor_path)
    except OSError as exc:
        raise CompetitionContractError(f"cannot hash tensor before materialization: {tensor_path}") from exc
    payload = load_tensor(tensor_path, expected_task=expected_task)
    try:
        after = sha256(tensor_path)
    except OSError as exc:
        raise CompetitionContractError(f"tensor disappeared while materializing: {tensor_path}") from exc
    if before != after:
        raise CompetitionContractError(f"tensor changed while materializing: {tensor_path}")
    snapshot = {
        "path": str(tensor_path),
        "file_sha256": before,
        "payload_sha256": _payload_sha256(payload),
        "task": str(payload.get("task", "")),
    }
    return payload, snapshot


def _verify_tensor_snapshot(
    snapshot: Mapping[str, str], payload: Mapping[str, Any],
) -> None:
    path = Path(str(snapshot.get("path", "")))
    try:
        current = sha256(path)
    except OSError as exc:
        raise CompetitionContractError(f"tensor disappeared after materialization: {path}") from exc
    if current != snapshot.get("file_sha256"):
        raise CompetitionContractError(f"tensor changed after materialization: {path}")
    if _payload_sha256(payload) != snapshot.get("payload_sha256"):
        raise CompetitionContractError(f"materialized tensor payload changed in memory: {path}")
    if str(payload.get("task", "")) != snapshot.get("task"):
        raise CompetitionContractError(f"materialized tensor task changed in memory: {path}")


def _unit_ids(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(row.get("unit_id", "")) for row in rows}


def assert_unit_disjoint(train: Mapping[str, Any], val: Mapping[str, Any]) -> None:
    overlap = _unit_ids(train["meta"]["index"]) & _unit_ids(val["meta"]["index"])
    if overlap:
        raise CompetitionContractError(f"train/validation unit leakage: {sorted(overlap)[:3]}")


def _channel_statistics_with_edge(x: np.ndarray, edge: int) -> np.ndarray:
    if x.ndim != 3:
        raise CompetitionContractError("feature input must have shape [N,L,C]")
    _, length, _ = x.shape
    edge = max(1, min(int(edge), length))
    time_axis = np.linspace(-1.0, 1.0, length, dtype=np.float32)
    slope_denominator = float(np.sum(time_axis * time_axis))
    q25, q50, q75 = (np.quantile(x, q, axis=1) for q in (0.25, 0.50, 0.75))
    values = (
        x[:, -1, :], x[:, 0, :], x[:, -1, :] - x[:, 0, :],
        x.mean(axis=1), x.std(axis=1), x.min(axis=1), x.max(axis=1),
        q25, q50, q75,
        np.einsum("l,nlc->nc", time_axis, x) / slope_denominator,
        x[:, -edge:, :].mean(axis=1), x[:, :edge, :].mean(axis=1),
    )
    # [N,C,S], with a stable statistic-major flattening contract below.
    return np.stack(values, axis=2).astype(np.float32)


def _channel_statistics(x: np.ndarray) -> np.ndarray:
    return _channel_statistics_with_edge(x, max(2, min(8, x.shape[1] // 5)))


def _booster_features(x: torch.Tensor | np.ndarray, feature: str) -> np.ndarray:
    array = x.detach().cpu().numpy() if isinstance(x, torch.Tensor) else np.asarray(x)
    array = np.asarray(array, dtype=np.float32)
    if array.ndim != 3:
        raise CompetitionContractError("booster input must have shape [N,L,C]")
    if feature == "flat_window":
        return array.reshape(array.shape[0], -1).astype(np.float32)
    if feature == "channel_statistics_v1_fixed5":
        statistics = _channel_statistics_with_edge(array, 5)
        return statistics.transpose(0, 2, 1).reshape(array.shape[0], -1).astype(np.float32)
    if feature == "channel_statistics_v1":
        # Match the research route: edge=max(2, min(8, L//5)).  Keeping the
        # legacy fixed-five branch above preserves old artifact compatibility
        # without allowing it into the current frozen BAT contract.
        statistics = _channel_statistics(array)
        return statistics.transpose(0, 2, 1).reshape(array.shape[0], -1).astype(np.float32)
    raise CompetitionContractError(f"unknown tree expert feature contract: {feature}")


def age_values(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    return np.asarray([float(row.get("t_end", 0.0)) for row in rows], dtype=np.float32)


def feature_names(channels: Sequence[str]) -> list[str]:
    return [f"{channel}:{stat}" for stat in STATISTICS for channel in channels] + list(AGE_FEATURES)


def extract_features(
    x: torch.Tensor | np.ndarray,
    rows: Sequence[Mapping[str, Any]],
    *,
    channels: Sequence[str],
    age_scale: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    array = x.detach().cpu().numpy() if isinstance(x, torch.Tensor) else np.asarray(x)
    array = np.asarray(array, dtype=np.float32)
    if array.shape[0] != len(rows) or array.shape[2] != len(channels):
        raise CompetitionContractError("feature rows/channels do not match the frozen contract")
    if not math.isfinite(float(age_scale)) or float(age_scale) <= 0:
        raise CompetitionContractError("age_scale must be positive and finite")
    per_channel = _channel_statistics(array)
    ages = age_values(rows)
    ratio = ages / float(age_scale)
    age_features = np.stack((ratio, ratio * ratio, np.sqrt(np.maximum(ratio, 0.0))), axis=1)
    direct = np.concatenate((per_channel.transpose(0, 2, 1).reshape(array.shape[0], -1), age_features), axis=1)
    invariant_parts = (
        per_channel.mean(axis=1), per_channel.std(axis=1),
        per_channel.min(axis=1), per_channel.max(axis=1),
    )
    invariant = np.concatenate((*invariant_parts, age_features), axis=1)
    return direct.astype(np.float32), invariant.astype(np.float32), ages


def _normalizer(values: np.ndarray) -> dict[str, list[float]]:
    mean = np.asarray(values.mean(axis=0), dtype=np.float32)
    std = np.asarray(values.std(axis=0), dtype=np.float32)
    std[std < 1e-6] = 1.0
    return {"mean": mean.tolist(), "std": std.tolist()}


def _normalize(values: np.ndarray, normalizer: Mapping[str, Sequence[float]]) -> np.ndarray:
    mean = np.asarray(normalizer["mean"], dtype=np.float32)
    std = np.asarray(normalizer["std"], dtype=np.float32)
    if values.shape[1] != mean.size or mean.shape != std.shape or np.any(std <= 0):
        raise CompetitionContractError("normalization shape/scale mismatch")
    return ((values - mean) / std).astype(np.float32)


def _identity_normalizer(dimension: int) -> dict[str, list[float]]:
    return {
        "mean": np.zeros(int(dimension), dtype=np.float32).tolist(),
        "std": np.ones(int(dimension), dtype=np.float32).tolist(),
    }


def _booster_bins(
    features: torch.Tensor,
    validation: torch.Tensor | None,
    *,
    bin_count: int,
) -> tuple[torch.Tensor, torch.Tensor | None, torch.Tensor]:
    thresholds: list[torch.Tensor] = []
    train_bins: list[torch.Tensor] = []
    validation_bins: list[torch.Tensor] = []
    for feature_index in range(features.shape[1]):
        # sklearn converts the input to float64 before computing thresholds.
        column = features[:, feature_index].to(dtype=torch.float64).contiguous()
        distinct = torch.unique(column, sorted=True)
        if len(distinct) <= int(bin_count):
            boundary = (distinct[:-1] + distinct[1:]) * 0.5
        else:
            # Construct percentages first, as sklearn does with
            # np.linspace(0, 100, ...); dividing an already-normalized grid
            # changes exact-integer order-statistic positions by one ULP.
            quantiles = torch.linspace(
                0.0, 100.0, int(bin_count) + 1, dtype=torch.float64
            )[1:-1] / 100.0
            # Match numpy.percentile(method="midpoint"): sort once, then take
            # the midpoint of the two bracketing order statistics. torch's
            # quantile implementation uses a different interpolation path on
            # repeated float32 telemetry values.
            ordered = torch.sort(column).values
            positions = quantiles * float(len(ordered) - 1)
            lower = torch.floor(positions).to(dtype=torch.long)
            upper = torch.ceil(positions).to(dtype=torch.long)
            boundary = (ordered[lower] + ordered[upper]) * 0.5
        if len(boundary) < int(bin_count) - 1:
            boundary = torch.cat((
                boundary,
                torch.full(
                    (int(bin_count) - 1 - len(boundary),),
                    torch.inf,
                    dtype=column.dtype,
                ),
            ))
        thresholds.append(boundary)
        train_bins.append(torch.bucketize(column, boundary))
        if validation is not None:
            validation_bins.append(torch.bucketize(
                validation[:, feature_index].to(dtype=torch.float32).contiguous(), boundary
            ))
    return (
        torch.stack(train_bins, dim=1),
        torch.stack(validation_bins, dim=1) if validation is not None else None,
        torch.stack(thresholds, dim=0),
    )


def _best_tree_split(
    bins: torch.Tensor,
    residual: torch.Tensor,
    indices: torch.Tensor,
    *,
    bin_count: int,
    min_samples_leaf: int,
    l2_regularization: float,
) -> tuple[float, int, int] | None:
    if len(indices) < 2 * min_samples_leaf:
        return None
    feature_count = bins.shape[1]
    selected_bins = bins[indices].T
    selected_residual = residual[indices].to(dtype=torch.float64)
    counts = torch.zeros(feature_count, bin_count, dtype=torch.float64)
    sums = torch.zeros(feature_count, bin_count, dtype=torch.float64)
    counts.scatter_add_(1, selected_bins, torch.ones_like(selected_bins, dtype=torch.float64))
    sums.scatter_add_(1, selected_bins, selected_residual.expand(feature_count, -1))
    left_count = counts.cumsum(dim=1)[:, :-1]
    left_sum = sums.cumsum(dim=1)[:, :-1]
    total_count = counts.sum(dim=1, keepdim=True)
    total_sum = sums.sum(dim=1, keepdim=True)
    right_count = total_count - left_count
    right_sum = total_sum - left_sum
    regularizer = float(l2_regularization)
    gain = (
        left_sum.square() / (left_count + regularizer).clamp_min(1.0)
        + right_sum.square() / (right_count + regularizer).clamp_min(1.0)
        - total_sum.square() / (total_count + regularizer).clamp_min(1.0)
    )
    gain[(left_count < min_samples_leaf) | (right_count < min_samples_leaf)] = -1.0
    flat_index = int(gain.argmax())
    best_gain = float(gain.flatten()[flat_index])
    if best_gain <= 1e-12:
        return None
    return best_gain, flat_index // (bin_count - 1), flat_index % (bin_count - 1)


def _fit_booster_tree(
    bins: torch.Tensor,
    residual: torch.Tensor,
    *,
    bin_count: int,
    max_leaf_nodes: int,
    min_samples_leaf: int,
    l2_regularization: float,
) -> list[tuple[int, int, int, int, float]]:
    nodes: list[tuple[int, int, int, int, float] | None] = [None]
    leaves: dict[int, torch.Tensor] = {0: torch.arange(len(bins), dtype=torch.long)}
    candidates = {
        0: _best_tree_split(
            bins, residual, leaves[0], bin_count=bin_count,
            min_samples_leaf=min_samples_leaf, l2_regularization=l2_regularization,
        )
    }
    while len(leaves) < max_leaf_nodes:
        splittable = [(value[0], node_id, value) for node_id, value in candidates.items() if value is not None]
        if not splittable:
            break
        _, node_id, split = max(splittable, key=lambda value: value[0])
        _, feature_index, boundary_index = split
        indices = leaves.pop(node_id)
        candidates.pop(node_id)
        mask = bins[indices, feature_index] <= boundary_index
        left_id = len(nodes)
        right_id = left_id + 1
        nodes[node_id] = (feature_index, boundary_index, left_id, right_id, 0.0)
        nodes.extend((None, None))
        leaves[left_id] = indices[mask]
        leaves[right_id] = indices[~mask]
        for child_id in (left_id, right_id):
            candidates[child_id] = _best_tree_split(
                bins, residual, leaves[child_id], bin_count=bin_count,
                min_samples_leaf=min_samples_leaf, l2_regularization=l2_regularization,
            )
    for node_id, indices in leaves.items():
        value = float(
            residual[indices].to(dtype=torch.float64).sum()
            / (len(indices) + float(l2_regularization))
        )
        nodes[node_id] = (-1, -1, -1, -1, value)
    if any(node is None for node in nodes):
        raise CompetitionContractError("PyTorch booster produced an incomplete tree")
    return [node for node in nodes if node is not None]


def _apply_binned_tree(
    bins: torch.Tensor,
    nodes: Sequence[Sequence[float | int]],
) -> torch.Tensor:
    output = torch.empty(len(bins), dtype=torch.float64)
    stack: list[tuple[int, torch.Tensor]] = [(0, torch.arange(len(bins), dtype=torch.long))]
    while stack:
        node_id, indices = stack.pop()
        feature, boundary, left, right, value = nodes[node_id]
        if int(feature) < 0:
            output[indices] = float(value)
            continue
        mask = bins[indices, int(feature)] <= int(boundary)
        stack.append((int(left), indices[mask]))
        stack.append((int(right), indices[~mask]))
    return output


def fit_torch_histogram_booster(
    train_features: np.ndarray,
    train_labels: np.ndarray,
    validation_features: np.ndarray,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    """Fit a histogram gradient booster using torch tensors and operators only."""
    features = torch.from_numpy(np.asarray(train_features, dtype=np.float32))
    validation = torch.from_numpy(np.asarray(validation_features, dtype=np.float32))
    labels = torch.from_numpy(np.asarray(train_labels, dtype=np.float64))
    bin_count = int(config["bin_count"])
    train_bins, validation_bins, thresholds = _booster_bins(
        features, validation, bin_count=bin_count
    )
    if validation_bins is None:
        raise CompetitionContractError("booster validation bins were not built")
    initial = float(labels.mean())
    learning_rate = float(config["learning_rate"])
    train_prediction = torch.full_like(labels, initial)
    validation_prediction = torch.full((len(validation),), initial, dtype=torch.float64)
    trees: list[list[tuple[int, int, int, int, float]]] = []
    for _ in range(int(config["iterations"])):
        tree = _fit_booster_tree(
            train_bins, labels - train_prediction,
            bin_count=bin_count,
            max_leaf_nodes=int(config["max_leaf_nodes"]),
            min_samples_leaf=int(config["min_samples_leaf"]),
            l2_regularization=float(config["l2_regularization"]),
        )
        train_prediction += learning_rate * _apply_binned_tree(train_bins, tree)
        validation_prediction += learning_rate * _apply_binned_tree(validation_bins, tree)
        trees.append(tree)
    state = {
        "schema": BOOSTER_SCHEMA,
        "framework": "pytorch",
        "input_dimension": int(features.shape[1]),
        "bin_count": bin_count,
        "thresholds": thresholds,
        "initial_prediction": initial,
        "learning_rate": learning_rate,
        "iterations": len(trees),
        "max_leaf_nodes": int(config["max_leaf_nodes"]),
        "min_samples_leaf": int(config["min_samples_leaf"]),
        "l2_regularization": float(config["l2_regularization"]),
        "trees": trees,
    }
    return state, validation_prediction.numpy().astype(np.float32)


def predict_torch_histogram_booster(state: Mapping[str, Any], features: np.ndarray) -> np.ndarray:
    if state.get("schema") != BOOSTER_SCHEMA or state.get("framework") != "pytorch":
        raise CompetitionContractError("booster state is not a PyTorch histogram model")
    values = torch.from_numpy(np.asarray(features, dtype=np.float32))
    thresholds = state.get("thresholds")
    if not isinstance(thresholds, torch.Tensor) or thresholds.shape[0] != values.shape[1]:
        raise CompetitionContractError("booster feature contract mismatch")
    columns = [
        torch.bucketize(values[:, index].contiguous(), thresholds[index])
        for index in range(values.shape[1])
    ]
    bins = torch.stack(columns, dim=1)
    output = torch.full((len(values),), float(state["initial_prediction"]), dtype=torch.float64)
    learning_rate = float(state["learning_rate"])
    for tree in state["trees"]:
        output += learning_rate * _apply_binned_tree(bins, tree)
    return output.numpy().astype(np.float32)


def _booster_cache_matches(
    state: Mapping[str, Any],
    *,
    feature: str,
    input_dimension: int,
    config: Mapping[str, Any],
    cached_training: Mapping[str, Any],
    train_tensor_sha256: str,
) -> bool:
    """Return true only when every training-affecting booster field is identical."""
    try:
        return (
            state.get("schema") == BOOSTER_SCHEMA
            and state.get("framework") == "pytorch"
            and state.get("feature") == feature
            and int(state.get("input_dimension", -1)) == int(input_dimension)
            and int(state.get("iterations", -1)) == int(config["iterations"])
            and float(state.get("learning_rate", float("nan"))) == float(config["learning_rate"])
            and int(state.get("bin_count", -1)) == int(config["bin_count"])
            and int(state.get("max_leaf_nodes", -1)) == int(config["max_leaf_nodes"])
            and int(state.get("min_samples_leaf", -1)) == int(config["min_samples_leaf"])
            and float(state.get("l2_regularization", float("nan"))) == float(config["l2_regularization"])
            and cached_training.get("train_tensor_sha256") == train_tensor_sha256
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class SharedEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden: Sequence[int], dropout: float) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        dim = int(input_dim)
        for width in hidden:
            layers.extend((nn.Linear(dim, int(width)), nn.LayerNorm(int(width)), nn.SiLU(), nn.Dropout(float(dropout))))
            dim = int(width)
        self.network = nn.Sequential(*layers)
        self.output_dim = dim

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


class PublicPretrainModel(nn.Module):
    def __init__(self, input_dim: int, hidden: Sequence[int], dropout: float) -> None:
        super().__init__()
        self.encoder = SharedEncoder(input_dim, hidden, dropout)
        self.head = nn.Linear(self.encoder.output_dim, 1)

    def forward(self, invariant: torch.Tensor) -> torch.Tensor:
        return self.head(self.encoder(invariant)).squeeze(-1)


class TargetSegmentedModel(nn.Module):
    """Transferred invariant encoder plus a target-specific PyTorch expert."""

    def __init__(
        self,
        *,
        invariant_dim: int,
        direct_dim: int,
        shared_hidden: Sequence[int],
        direct_hidden: Sequence[int],
        dropout: float,
        threshold: float,
        low_target_weight: float,
        high_target_weight: float,
        shared_output_weight: float,
    ) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        dim = int(direct_dim)
        for width in direct_hidden:
            layers.extend((nn.Linear(dim, int(width)), nn.LayerNorm(int(width)), nn.SiLU(), nn.Dropout(float(dropout))))
            dim = int(width)
        layers.append(nn.Linear(dim, 1))
        self.target_expert = nn.Sequential(*layers)
        # The target expert is initialized first so its RNG trajectory is
        # identical to the original independent S22/S21 target MLP.  Retain
        # the following CPU RNG state as well: otherwise the auxiliary shared
        # branch changes target-expert dropout masks during CPU training.
        target_rng_state = torch.get_rng_state().clone()
        self.encoder = SharedEncoder(invariant_dim, shared_hidden, dropout)
        self.shared_head = nn.Linear(self.encoder.output_dim, 1)
        torch.set_rng_state(target_rng_state)
        self.register_buffer("threshold", torch.tensor(float(threshold), dtype=torch.float32))
        self.register_buffer("low_target_weight", torch.tensor(float(low_target_weight), dtype=torch.float32))
        self.register_buffer("high_target_weight", torch.tensor(float(high_target_weight), dtype=torch.float32))
        self.register_buffer("shared_output_weight", torch.tensor(float(shared_output_weight), dtype=torch.float32))

    def components(self, direct: torch.Tensor, invariant: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return self.shared_prediction(invariant), self.target_prediction(direct)

    def shared_prediction(self, invariant: torch.Tensor) -> torch.Tensor:
        return self.shared_head(self.encoder(invariant)).squeeze(-1)

    def target_prediction(self, direct: torch.Tensor) -> torch.Tensor:
        return self.target_expert(direct).squeeze(-1)

    def forward(self, direct: torch.Tensor, invariant: torch.Tensor, age: torch.Tensor) -> torch.Tensor:
        del age  # Segmentation blends this neural expert with the torch booster.
        shared, target = self.components(direct, invariant)
        return (1.0 - self.shared_output_weight) * target + self.shared_output_weight * shared


def _device(value: str) -> torch.device:
    try:
        device = torch.device(value)
    except (TypeError, RuntimeError) as exc:
        raise CompetitionContractError(f"invalid torch device: {value}") from exc
    if device.type == "cuda" and not torch.cuda.is_available():
        raise CompetitionContractError("CUDA requested but unavailable")
    return device


def _loader(
    arrays: Sequence[np.ndarray],
    *,
    batch_size: int,
    seed: int,
    shuffle: bool,
) -> torch.utils.data.DataLoader:
    tensors = tuple(torch.from_numpy(np.asarray(value, dtype=np.float32)) for value in arrays)
    return torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(*tensors),
        batch_size=int(batch_size),
        shuffle=shuffle,
        generator=torch.Generator().manual_seed(seed) if shuffle else None,
        num_workers=0,
    )


def _sample_indices(size: int, maximum: int | None, seed: int) -> np.ndarray:
    if maximum is None or maximum <= 0 or size <= maximum:
        return np.arange(size, dtype=np.int64)
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(size, size=int(maximum), replace=False))


def _fit_public(
    model: PublicPretrainModel,
    invariant: np.ndarray,
    labels: np.ndarray,
    *,
    seed: int,
    device: torch.device,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
) -> list[float]:
    loader = _loader((invariant, labels), batch_size=batch_size, seed=seed, shuffle=True)
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    history: list[float] = []
    for _ in range(max(1, int(epochs))):
        model.train()
        total = count = 0
        for xb, yb in loader:
            optimizer.zero_grad(set_to_none=True)
            prediction = model(xb.to(device))
            loss = torch.nn.functional.smooth_l1_loss(prediction, yb.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach().cpu()) * len(xb)
            count += len(xb)
        history.append(total / max(count, 1))
    model.cpu().eval()
    return history


def _predict_target_model(
    model: TargetSegmentedModel,
    direct: np.ndarray,
    invariant: np.ndarray,
    ages: np.ndarray,
    *,
    device: torch.device,
    batch_size: int,
) -> np.ndarray:
    loader = _loader((direct, invariant, ages), batch_size=batch_size, seed=0, shuffle=False)
    predictions: list[np.ndarray] = []
    model.to(device).eval()
    with torch.inference_mode():
        for direct_batch, invariant_batch, age_batch in loader:
            output = model(direct_batch.to(device), invariant_batch.to(device), age_batch.to(device))
            predictions.append(output.detach().cpu().numpy())
    return np.concatenate(predictions).astype(np.float32)


def _predict_target_expert(
    model: TargetSegmentedModel,
    direct: np.ndarray,
    *,
    device: torch.device,
    batch_size: int,
) -> np.ndarray:
    loader = _loader((direct,), batch_size=batch_size, seed=0, shuffle=False)
    predictions: list[np.ndarray] = []
    model.to(device).eval()
    with torch.inference_mode():
        for (direct_batch,) in loader:
            output = model.target_prediction(direct_batch.to(device))
            predictions.append(output.detach().cpu().numpy())
    return np.concatenate(predictions).astype(np.float32)


def _smooth_l1_mean(prediction: np.ndarray, target: np.ndarray) -> float:
    prediction_array = np.asarray(prediction, dtype=np.float64)
    target_array = np.asarray(target, dtype=np.float64)
    if prediction_array.shape != target_array.shape:
        raise CompetitionContractError("SmoothL1 prediction and target must align")
    absolute = np.abs(prediction_array - target_array)
    return float(np.mean(np.where(absolute < 1.0, 0.5 * absolute * absolute, absolute - 0.5)))


def _fit_target(
    model: TargetSegmentedModel,
    train_arrays: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    val_arrays: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    *,
    seed: int,
    device: torch.device,
    epochs: int,
    patience: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    shared_aux_weight: float,
) -> tuple[dict[str, torch.Tensor], dict[str, Any]]:
    direct, invariant, ages, labels = train_arrays
    val_direct, _val_invariant, _val_ages, val_labels = val_arrays
    loader = _loader((direct, invariant, ages, labels), batch_size=batch_size, seed=seed, shuffle=True)
    model.to(device)
    optimizer = torch.optim.AdamW(
        model.target_expert.parameters(), lr=learning_rate, weight_decay=weight_decay,
    )
    best_loss = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = 0
    stale = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, max(1, int(epochs)) + 1):
        model.train()
        total = count = 0
        for direct_batch, invariant_batch, age_batch, label_batch in loader:
            optimizer.zero_grad(set_to_none=True)
            del invariant_batch, age_batch
            direct_device = direct_batch.to(device)
            label_device = label_batch.to(device)
            output = model.target_prediction(direct_device)
            loss = torch.nn.functional.smooth_l1_loss(output, label_device)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.target_expert.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach().cpu()) * len(direct_batch)
            count += len(direct_batch)
        # Match the historical target-MLP early-stopping path exactly.  A
        # validation DataLoader allocates a base seed from the global CPU RNG,
        # which would alter dropout masks in the following training epoch.
        model.eval()
        with torch.no_grad():
            validation_prediction = model.target_prediction(
                torch.from_numpy(np.asarray(val_direct, dtype=np.float32)).to(device)
            ).detach().cpu().numpy().astype(np.float32)
        validation_loss = _smooth_l1_mean(validation_prediction, val_labels)
        history.append({"epoch": float(epoch), "train_smooth_l1": total / max(count, 1), "validation_smooth_l1": validation_loss})
        if validation_loss < best_loss - 1e-6:
            best_loss = validation_loss
            best_epoch = epoch
            best_state = {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
        if stale >= max(1, int(patience)):
            break
    if best_state is None:
        raise CompetitionContractError("target transfer produced no checkpoint")
    model.cpu().load_state_dict(best_state, strict=True)
    return best_state, {
        "selected_epoch": best_epoch,
        "validation_smooth_l1_normalized": best_loss,
        "epochs_ran": len(history),
        "last": history[-1],
    }


def _fit_target_fixed_epochs(
    model: TargetSegmentedModel,
    arrays: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    *,
    seed: int,
    device: torch.device,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    shared_aux_weight: float,
) -> tuple[dict[str, torch.Tensor], dict[str, Any]]:
    """Refit a frozen architecture on train plus validation for exact epochs."""
    direct, invariant, ages, labels = arrays
    epoch_count = int(epochs)
    if epoch_count <= 0 or not (len(direct) == len(invariant) == len(ages) == len(labels)):
        raise CompetitionContractError("target refit arrays or epoch count are invalid")
    loader = _loader(
        (direct, invariant, ages, labels),
        batch_size=batch_size,
        seed=seed,
        shuffle=True,
    )
    model.to(device)
    optimizer = torch.optim.AdamW(
        model.target_expert.parameters(), lr=learning_rate, weight_decay=weight_decay,
    )
    for _ in range(epoch_count):
        model.train()
        for direct_batch, invariant_batch, age_batch, label_batch in loader:
            optimizer.zero_grad(set_to_none=True)
            del age_batch
            direct_device = direct_batch.to(device)
            del invariant_batch
            label_device = label_batch.to(device)
            target = model.target_prediction(direct_device)
            loss = torch.nn.functional.smooth_l1_loss(target, label_device)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.target_expert.parameters(), 1.0)
            optimizer.step()
    model.cpu().eval()
    state = {
        name: tensor.detach().cpu().clone()
        for name, tensor in model.state_dict().items()
    }
    return state, {
        "fit_scope": "train_plus_validation",
        "epochs_ran": epoch_count,
        "sample_count": len(direct),
    }


def _fit_shared_fixed_epochs(
    model: TargetSegmentedModel,
    arrays: tuple[np.ndarray, np.ndarray],
    *,
    seed: int,
    device: torch.device,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    loss_weight: float = 1.0,
) -> tuple[dict[str, torch.Tensor], dict[str, Any]]:
    """Adapt the public encoder without perturbing the frozen target MLP."""
    invariant, labels = arrays
    epoch_count = int(epochs)
    if (
        epoch_count <= 0
        or len(invariant) != len(labels)
        or not 0.0 < float(loss_weight) <= 1.0
    ):
        raise CompetitionContractError("shared adapter arrays, epochs, or loss weight are invalid")
    seed_everything(seed)
    loader = _loader(
        (invariant, labels), batch_size=batch_size, seed=seed, shuffle=True,
    )
    model.to(device)
    parameters = tuple(model.encoder.parameters()) + tuple(model.shared_head.parameters())
    optimizer = torch.optim.AdamW(
        parameters, lr=learning_rate, weight_decay=weight_decay,
    )
    for _ in range(epoch_count):
        model.train()
        for invariant_batch, label_batch in loader:
            optimizer.zero_grad(set_to_none=True)
            shared = model.shared_prediction(invariant_batch.to(device))
            loss = float(loss_weight) * torch.nn.functional.smooth_l1_loss(
                shared, label_batch.to(device),
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(parameters, 1.0)
            optimizer.step()
    model.cpu().eval()
    state = {
        name: tensor.detach().cpu().clone()
        for name, tensor in model.state_dict().items()
    }
    return state, {
        "fit_scope": "target_shared_adapter",
        "epochs_ran": epoch_count,
        "sample_count": len(invariant),
        "loss_weight": float(loss_weight),
        "initialization": "public_pretrain_checkpoint",
    }


def _pava_nonincreasing(values: np.ndarray) -> np.ndarray:
    levels: list[float] = []
    weights: list[int] = []
    for value in np.asarray(values, dtype=np.float64):
        levels.append(float(value))
        weights.append(1)
        while len(levels) >= 2 and levels[-2] < levels[-1]:
            total = weights[-2] + weights[-1]
            levels[-2] = (weights[-2] * levels[-2] + weights[-1] * levels[-1]) / total
            weights[-2] = total
            levels.pop()
            weights.pop()
    return np.repeat(np.asarray(levels, dtype=np.float32), np.asarray(weights, dtype=np.int64))


def monotone_project(
    prediction: np.ndarray,
    rows: Sequence[Mapping[str, Any]],
    mode: str,
) -> np.ndarray:
    output = np.asarray(prediction, dtype=np.float32).copy()
    if mode == "none":
        return output
    groups: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(str(row.get("unit_id", "")), []).append(index)
    for indices in groups.values():
        order = sorted(indices, key=lambda idx: float(rows[idx].get("t_end", idx)))
        if mode == "isotonic_nonincreasing":
            output[order] = _pava_nonincreasing(output[order])
        elif mode == "causal_running_min":
            running = float("inf")
            for idx in order:
                running = min(running, float(output[idx]))
                output[idx] = running
        else:
            raise CompetitionContractError(f"unknown postprocess mode: {mode}")
    return output


def aggregate_members(
    member_predictions: Sequence[np.ndarray],
    mode: str,
    *,
    member_seeds: Sequence[int] | None = None,
) -> np.ndarray:
    if len(member_predictions) != 3:
        raise CompetitionContractError("S22/S21 production routes require exactly three members")
    if mode == "seed17":
        if member_seeds is None or len(member_seeds) != len(member_predictions):
            raise CompetitionContractError("seed17 aggregation requires member seed identities")
        try:
            seeds = tuple(int(seed) for seed in member_seeds)
        except (TypeError, ValueError) as exc:
            raise CompetitionContractError("member seed identities must be integers") from exc
        if set(seeds) != set(DEFAULT_SEEDS):
            raise CompetitionContractError("seed17 aggregation requires the three frozen member seeds")
        return np.asarray(member_predictions[seeds.index(17)], dtype=np.float32).copy()
    stack = np.stack(member_predictions, axis=0)
    if mode == "median3":
        return np.median(stack, axis=0).astype(np.float32)
    if mode == "mean3":
        return np.mean(stack, axis=0).astype(np.float32)
    raise CompetitionContractError(f"unknown three-seed aggregation: {mode}")


def age_segmented_blend(
    tree_prediction: np.ndarray,
    mlp_prediction: np.ndarray,
    ages: np.ndarray,
    *,
    threshold: float,
    low_mlp_weight: float,
    high_mlp_weight: float,
) -> np.ndarray:
    tree = np.asarray(tree_prediction, dtype=np.float32)
    mlp = np.asarray(mlp_prediction, dtype=np.float32)
    age = np.asarray(ages, dtype=np.float32)
    if tree.shape != mlp.shape or tree.shape != age.shape:
        raise CompetitionContractError("tree, MLP, and age predictions must align")
    weight = np.where(age <= float(threshold), low_mlp_weight, high_mlp_weight).astype(np.float32)
    return ((1.0 - weight) * tree + weight * mlp).astype(np.float32)


def regression_metrics(
    prediction_normalized: np.ndarray,
    truth: np.ndarray,
    *,
    rmax: float,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    prediction = np.clip(np.asarray(prediction_normalized, dtype=np.float64), 0.0, 1.0) * float(rmax)
    target = np.asarray(truth, dtype=np.float64)
    error = prediction - target
    groups: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(str(row.get("unit_id", "")), []).append(index)
    upward = pairs = 0
    unit_rmse: list[float] = []
    for indices in groups.values():
        unit_rmse.append(float(np.sqrt(np.mean(error[indices] ** 2))))
        order = sorted(indices, key=lambda idx: float(rows[idx].get("t_end", idx)))
        for left, right in zip(order, order[1:]):
            pairs += 1
            upward += int(prediction[right] > prediction[left] + 1e-9)
    return {
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "mae": float(np.mean(np.abs(error))),
        "bias": float(np.mean(error)),
        "unit_macro_rmse": float(np.mean(unit_rmse)),
        "rul_upward_fraction": float(upward / pairs) if pairs else None,
        "n_windows": int(target.size),
        "n_units": len(groups),
    }


def _line_paths(root: Path, data_root: Path, spec: Mapping[str, Any]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name in (
        "public_tensor", "target_train_tensor", "target_val_tensor",
        "target_norm", "target_preprocess",
    ):
        value = spec.get(name)
        if not isinstance(value, str) or not value:
            raise CompetitionContractError(f"line config is missing {name}")
        paths[name] = (data_root / value).resolve()
    return paths


def _feature_contract(payload: Mapping[str, Any], age_scale: float) -> dict[str, Any]:
    channels = [str(value) for value in payload["meta"].get("channels", [])]
    x = payload["x"]
    if len(channels) != x.shape[2]:
        raise CompetitionContractError("channel metadata does not match tensor shape")
    return {
        "schema": FEATURE_SCHEMA,
        "window_length": int(x.shape[1]),
        "channels": channels,
        "statistics": list(STATISTICS),
        "flatten_order": "statistic_then_channel_then_age",
        "age_features": list(AGE_FEATURES),
        "age_scale": float(age_scale),
        "direct_dimension": len(channels) * len(STATISTICS) + len(AGE_FEATURES),
        "invariant_reducers": list(INVARIANT_REDUCERS),
        "invariant_dimension": len(STATISTICS) * len(INVARIANT_REDUCERS) + len(AGE_FEATURES),
    }


def pretrain(
    *,
    root: Path,
    config: Mapping[str, Any],
    output_dir: Path,
    device_name: str,
    epochs_override: int | None = None,
) -> dict[str, Any]:
    if epochs_override is not None:
        raise CompetitionContractError("production epoch override is forbidden")
    device = _device(device_name)
    data_root = resolve_data_root(root, config)
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "schema": SCHEMA_RECEIPT,
        "stage": "public_pretrain",
        "status": "pass",
        "created_at": utc_now(),
        "framework": {"name": "pytorch", "version": torch.__version__},
        "config": relative_path(Path(str(config["_path"])), root),
        "config_sha256": sha256(config["_path"]),
        **_implementation_contract(root),
        "holdout_read": False,
        "lines": {},
    }
    input_snapshots: list[tuple[dict[str, str], Mapping[str, Any]]] = []
    for component in ("bat", "rwa"):
        spec = config["lines"][component]
        paths = _line_paths(root, data_root, spec)
        public, public_snapshot = _load_tensor_snapshot(
            paths["public_tensor"], expected_task=str(spec["public_task"]),
        )
        input_snapshots.append((public_snapshot, public))
        rows = public["meta"]["index"]
        age_scale = max(float(row.get("t_end", 0.0)) for row in rows)
        contract = _feature_contract(public, age_scale)
        train_cfg = spec["pretrain"]
        # Subsample before feature materialization.  The RWA public tensor has
        # about 140k windows, so doing this later creates a needless memory peak.
        base_indices = _sample_indices(len(rows), train_cfg.get("max_samples"), 0)
        sampled_rows = [rows[int(index)] for index in base_indices]
        _, invariant, _ = extract_features(
            public["x"][base_indices], sampled_rows,
            channels=contract["channels"], age_scale=age_scale,
        )
        normalizer = _normalizer(invariant)
        invariant = _normalize(invariant, normalizer)
        labels = public["y_rul"][base_indices].float().numpy().astype(np.float32) / float(public["meta"]["rmax"])
        line_checkpoints: list[dict[str, Any]] = []
        for seed in spec["seeds"]:
            seed_everything(seed)
            model = PublicPretrainModel(
                contract["invariant_dimension"], train_cfg["hidden"], train_cfg["dropout"]
            )
            epochs = int(epochs_override or train_cfg["epochs"])
            history = _fit_public(
                model, invariant, labels, seed=seed, device=device,
                epochs=epochs, batch_size=int(train_cfg["batch_size"]),
                learning_rate=float(train_cfg["learning_rate"]),
                weight_decay=float(train_cfg["weight_decay"]),
            )
            checkpoint_path = output_dir / component / f"public_pretrain_{component}_s{seed}.pt"
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint = {
                "schema": SCHEMA_PRETRAIN,
                "framework": "pytorch",
                "torch_version": torch.__version__,
                "stage": "public_pretrain",
                "component": component,
                "route_id": spec["route_id"],
                "seed": seed,
                "config_sha256": sha256(config["_path"]),
                **_implementation_contract(root),
                "source": {
                    "path": relative_path(paths["public_tensor"], root),
                    "sha256": public_snapshot["file_sha256"],
                    "payload_sha256": public_snapshot["payload_sha256"],
                    "task": public["task"],
                    "rmax": float(public["meta"]["rmax"]),
                    "sample_count": int(len(base_indices)),
                },
                "feature_contract": contract,
                "normalization": {"invariant": normalizer},
                "architecture": {
                    "kind": "channel_invariant_shared_encoder",
                    "shared_hidden": list(train_cfg["hidden"]),
                    "dropout": float(train_cfg["dropout"]),
                },
                "training": {
                    "epochs": epochs,
                    "final_smooth_l1": history[-1],
                    "holdout_read": False,
                },
                "model_state": model.state_dict(),
            }
            _verify_tensor_snapshot(public_snapshot, public)
            torch.save(checkpoint, checkpoint_path)
            line_checkpoints.append({
                "seed": seed,
                "path": relative_path(checkpoint_path, root),
                "sha256": sha256(checkpoint_path),
                "final_smooth_l1": history[-1],
            })
        receipt["lines"][component] = {
            "route_id": spec["route_id"],
            "source_tensor": relative_path(paths["public_tensor"], root),
            "source_sha256": public_snapshot["file_sha256"],
            "source_payload_sha256": public_snapshot["payload_sha256"],
            "feature_contract": contract,
            "members": line_checkpoints,
        }
    for snapshot, payload in input_snapshots:
        _verify_tensor_snapshot(snapshot, payload)
    _json_dump(output_dir / "pretrain_receipt.json", receipt)
    return receipt


def _pretrain_member_index(receipt: Mapping[str, Any], component: str) -> dict[int, Mapping[str, Any]]:
    if receipt.get("schema") != SCHEMA_RECEIPT or receipt.get("stage") != "public_pretrain":
        raise CompetitionContractError("public pretrain receipt schema/stage mismatch")
    try:
        members = receipt["lines"][component]["members"]
    except (KeyError, TypeError) as exc:
        raise CompetitionContractError(f"public pretrain receipt lacks {component}") from exc
    if not isinstance(members, list) or len(members) != 3:
        raise CompetitionContractError("public pretrain receipt must contain three members")
    result = {int(item["seed"]): item for item in members}
    if len(result) != 3:
        raise CompetitionContractError("public pretrain receipt contains duplicate seeds")
    return result


def _validate_pretrain_receipt_contract(
    receipt: Mapping[str, Any], config: Mapping[str, Any], root: Path,
) -> None:
    if receipt.get("schema") != SCHEMA_RECEIPT or receipt.get("stage") != "public_pretrain":
        raise CompetitionContractError("public pretrain receipt schema/stage mismatch")
    if receipt.get("status") != "pass":
        raise CompetitionContractError("public pretrain receipt is not a passing run")
    framework = receipt.get("framework")
    if not isinstance(framework, Mapping) or framework.get("name") != "pytorch":
        raise CompetitionContractError("public pretrain receipt is not PyTorch")
    if receipt.get("holdout_read") is not False:
        raise CompetitionContractError("public pretrain receipt does not prove holdout isolation")
    if receipt.get("config_sha256") != sha256(config["_path"]):
        raise CompetitionContractError("public pretrain receipt config hash drift")
    _validate_implementation_contract(receipt, root, "public pretrain receipt")
    data_root = resolve_data_root(root, config)
    for component, spec in config["lines"].items():
        try:
            line = receipt["lines"][component]
        except (KeyError, TypeError) as exc:
            raise CompetitionContractError(f"public pretrain receipt lacks {component}") from exc
        if line.get("route_id") != spec["route_id"]:
            raise CompetitionContractError("public pretrain route identity drift")
        paths = _line_paths(root, data_root, spec)
        expected_source_path = relative_path(paths["public_tensor"], root)
        expected_source_hash = sha256(paths["public_tensor"])
        if (
            line.get("source_tensor") != expected_source_path
            or line.get("source_sha256") != expected_source_hash
        ):
            raise CompetitionContractError("public pretrain source tensor hash/path drift")
        members = _pretrain_member_index(receipt, component)
        if set(members) != {int(seed) for seed in spec["seeds"]}:
            raise CompetitionContractError("public pretrain seed set drift")
        for seed, member in members.items():
            try:
                checkpoint_path = _manifest_artifact_path(
                    root, member.get("path"), "public pretrain checkpoint",
                )
            except AttributeError as exc:
                raise CompetitionContractError("public pretrain member ledger is malformed") from exc
            if not checkpoint_path.is_file() or sha256(checkpoint_path) != member.get("sha256"):
                raise CompetitionContractError("public pretrain checkpoint hash drift")
            checkpoint = torch_load(checkpoint_path)
            if not isinstance(checkpoint, Mapping):
                raise CompetitionContractError("public pretrain checkpoint is not a mapping")
            if (
                checkpoint.get("schema") != SCHEMA_PRETRAIN
                or checkpoint.get("framework") != "pytorch"
                or checkpoint.get("stage") != "public_pretrain"
                or checkpoint.get("component") != component
                or checkpoint.get("route_id") != spec["route_id"]
                or checkpoint.get("seed") != seed
                or checkpoint.get("config_sha256") != sha256(config["_path"])
                or checkpoint.get("feature_contract") != line.get("feature_contract")
                or checkpoint.get("training", {}).get("holdout_read") is not False
            ):
                raise CompetitionContractError("public pretrain checkpoint contract drift")
            _validate_implementation_contract(checkpoint, root, "public pretrain checkpoint")
            source = checkpoint.get("source")
            if (
                not isinstance(source, Mapping)
                or source.get("path") != expected_source_path
                or source.get("sha256") != expected_source_hash
                or source.get("task") != spec["public_task"]
            ):
                raise CompetitionContractError("public pretrain checkpoint source drift")


def _build_target_model(
    *,
    spec: Mapping[str, Any],
    contract: Mapping[str, Any],
    pretrain_checkpoint: Mapping[str, Any],
) -> TargetSegmentedModel:
    transfer_cfg = spec["transfer"]
    architecture = pretrain_checkpoint["architecture"]
    model = TargetSegmentedModel(
        invariant_dim=int(contract["invariant_dimension"]),
        direct_dim=int(contract["direct_dimension"]),
        shared_hidden=architecture["shared_hidden"],
        direct_hidden=transfer_cfg["direct_hidden"],
        dropout=float(transfer_cfg["dropout"]),
        threshold=float(spec["segmentation"]["threshold"]),
        low_target_weight=float(spec["segmentation"]["low_target_weight"]),
        high_target_weight=float(spec["segmentation"]["high_target_weight"]),
        shared_output_weight=float(transfer_cfg["shared_output_weight"]),
    )
    source_state = pretrain_checkpoint["model_state"]
    encoder_state = {
        key.removeprefix("encoder."): value
        for key, value in source_state.items() if key.startswith("encoder.")
    }
    model.encoder.load_state_dict(encoder_state, strict=True)
    model.shared_head.load_state_dict({
        "weight": source_state["head.weight"], "bias": source_state["head.bias"]
    }, strict=True)
    return model


def _resolve_production_refit_epoch(spec: Mapping[str, Any]) -> int:
    value = spec.get("production_refit_epoch")
    if type(value) is not int or value <= 0:
        raise CompetitionContractError("production refit epoch is missing or invalid")
    return value


def transfer(
    *,
    root: Path,
    config: Mapping[str, Any],
    pretrain_dir: Path,
    output_dir: Path,
    device_name: str,
    epochs_override: int | None = None,
) -> dict[str, Any]:
    if epochs_override is not None:
        raise CompetitionContractError("production epoch override is forbidden")
    device = _device(device_name)
    data_root = resolve_data_root(root, config)
    pretrain_receipt_path = pretrain_dir / "pretrain_receipt.json"
    try:
        pretrain_receipt = json.loads(pretrain_receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompetitionContractError("public pretrain receipt cannot be read") from exc
    _validate_pretrain_receipt_contract(pretrain_receipt, config, root)
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "schema": SCHEMA_RECEIPT,
        "stage": "target_transfer",
        "status": "pass",
        "created_at": utc_now(),
        "framework": {"name": "pytorch", "version": torch.__version__},
        "config": relative_path(Path(str(config["_path"])), root),
        "config_sha256": sha256(config["_path"]),
        **_implementation_contract(root),
        "pretrain_receipt": relative_path(pretrain_receipt_path, root),
        "pretrain_receipt_sha256": sha256(pretrain_receipt_path),
        "holdout_read": False,
        "validation_used_for": "selection_and_epoch_freeze_only_then_train_plus_validation_refit",
        "lines": {},
    }
    manifest: dict[str, Any] = {
        "schema": SCHEMA_MANIFEST,
        "created_at": utc_now(),
        "framework": {"name": "pytorch", "version": torch.__version__},
        "config": relative_path(Path(str(config["_path"])), root),
        "config_sha256": sha256(config["_path"]),
        **_implementation_contract(root),
        "transfer_receipt": relative_path(output_dir / "transfer_receipt.json", root),
        "holdout_used_for_training_or_selection": False,
        "routes": {},
    }
    input_snapshots: list[tuple[dict[str, str], Mapping[str, Any]]] = []
    for component in ("bat", "rwa"):
        spec = config["lines"][component]
        paths = _line_paths(root, data_root, spec)
        train, train_snapshot = _load_tensor_snapshot(
            paths["target_train_tensor"], expected_task=str(spec["target_task"]),
        )
        val, val_snapshot = _load_tensor_snapshot(
            paths["target_val_tensor"], expected_task=str(spec["target_task"]),
        )
        input_snapshots.extend(((train_snapshot, train), (val_snapshot, val)))
        assert_unit_disjoint(train, val)
        if tuple(train["meta"]["channels"]) != tuple(val["meta"]["channels"]):
            raise CompetitionContractError(f"{component} train/validation channel drift")
        rmax = float(train["meta"]["rmax"])
        if rmax != float(val["meta"]["rmax"]):
            raise CompetitionContractError(f"{component} train/validation rmax drift")
        train_rows, val_rows = train["meta"]["index"], val["meta"]["index"]
        age_scale = max(float(row.get("t_end", 0.0)) for row in train_rows)
        contract = _feature_contract(train, age_scale)
        train_direct, train_invariant, train_ages = extract_features(
            train["x"], train_rows, channels=contract["channels"], age_scale=age_scale
        )
        val_direct, val_invariant, val_ages = extract_features(
            val["x"], val_rows, channels=contract["channels"], age_scale=age_scale
        )
        normalization = {
            # Processed tensors are already normalized.  Keeping the original
            # engineered-value scale reproduces the successful S22/S21 MLP
            # path; LayerNorm remains inside each neural block.
            "direct": _identity_normalizer(train_direct.shape[1]),
            "invariant": _normalizer(train_invariant),
        }
        train_direct = _normalize(train_direct, normalization["direct"])
        val_direct = _normalize(val_direct, normalization["direct"])
        train_invariant = _normalize(train_invariant, normalization["invariant"])
        val_invariant = _normalize(val_invariant, normalization["invariant"])
        train_labels = train["y_rul"].float().numpy().astype(np.float32) / rmax
        val_labels = val["y_rul"].float().numpy().astype(np.float32) / rmax
        tree_cfg = spec["tree_expert"]
        tree_feature = str(tree_cfg["feature"])
        booster_train = _booster_features(train["x"], tree_feature)
        booster_val = _booster_features(val["x"], tree_feature)
        booster_config = {
            "iterations": int(tree_cfg["n_trees"]),
            "learning_rate": float(tree_cfg["learning_rate"]),
            "bin_count": int(tree_cfg["max_bins"]),
            "max_leaf_nodes": int(tree_cfg["max_leaf_nodes"]),
            "min_samples_leaf": int(tree_cfg["min_samples_leaf"]),
            "l2_regularization": float(tree_cfg["l2_leaf"]),
        }
        selection_booster_state, booster_validation = fit_torch_histogram_booster(
            booster_train, train_labels, booster_val, booster_config,
        )
        selection_booster_state["feature"] = tree_feature
        member_index = _pretrain_member_index(pretrain_receipt, component)
        raw_predictions: list[np.ndarray] = []
        selection_summaries: dict[int, dict[str, Any]] = {}
        pretrain_records: dict[int, tuple[Path, Mapping[str, Any]]] = {}
        transfer_cfg = spec["transfer"]
        for seed in spec["seeds"]:
            pretrain_item = member_index[seed]
            pretrain_path = (root / str(pretrain_item["path"])).resolve()
            if not pretrain_path.is_file() or sha256(pretrain_path) != pretrain_item.get("sha256"):
                raise CompetitionContractError("public pretrain checkpoint hash drift")
            pretrain_checkpoint = torch_load(pretrain_path)
            if (
                pretrain_checkpoint.get("schema") != SCHEMA_PRETRAIN
                or pretrain_checkpoint.get("component") != component
                or pretrain_checkpoint.get("route_id") != spec["route_id"]
                or pretrain_checkpoint.get("seed") != seed
            ):
                raise CompetitionContractError("public pretrain checkpoint identity drift")
            pretrain_records[int(seed)] = (pretrain_path, pretrain_checkpoint)
            seed_everything(seed)
            model = _build_target_model(
                spec=spec, contract=contract, pretrain_checkpoint=pretrain_checkpoint
            )
            _, fit_summary = _fit_target(
                model,
                (train_direct, train_invariant, train_ages, train_labels),
                (val_direct, val_invariant, val_ages, val_labels),
                seed=seed, device=device,
                epochs=int(epochs_override or transfer_cfg["epochs"]),
                patience=int(transfer_cfg["patience"]),
                batch_size=int(transfer_cfg["batch_size"]),
                learning_rate=float(transfer_cfg["learning_rate"]),
                weight_decay=float(transfer_cfg["weight_decay"]),
                shared_aux_weight=float(transfer_cfg["shared_aux_weight"]),
            )
            _, shared_fit_summary = _fit_shared_fixed_epochs(
                model,
                (train_invariant, train_labels),
                seed=seed,
                device=device,
                epochs=int(fit_summary["selected_epoch"]),
                batch_size=int(transfer_cfg["batch_size"]),
                learning_rate=float(transfer_cfg["learning_rate"]),
                weight_decay=float(transfer_cfg["weight_decay"]),
                loss_weight=float(transfer_cfg["shared_aux_weight"]),
            )
            neural_prediction = _predict_target_model(
                model, val_direct, val_invariant, val_ages,
                device=device, batch_size=int(transfer_cfg["batch_size"]),
            )
            target_weight = np.where(
                val_ages <= float(spec["segmentation"]["threshold"]),
                float(spec["segmentation"]["low_target_weight"]),
                float(spec["segmentation"]["high_target_weight"]),
            ).astype(np.float32)
            prediction = target_weight * neural_prediction + (1.0 - target_weight) * booster_validation
            raw_predictions.append(prediction)
            selection_summaries[int(seed)] = {
                **fit_summary,
                "target_fit_scope": "target_train_only",
                "target_objective": "smooth_l1_target_expert_only",
                "shared_adapter": shared_fit_summary,
            }
        aggregated = aggregate_members(raw_predictions, str(spec["selection_aggregation"]))
        projected = monotone_project(aggregated, val_rows, str(spec["postprocess"]))
        metrics = regression_metrics(projected, val["y_rul"].numpy(), rmax=rmax, rows=val_rows)

        selection_epoch_median = int(np.median([
            summary["selected_epoch"] for summary in selection_summaries.values()
        ]))
        # These epochs are part of the frozen historical route.  Preserve them
        # across CPU/GPU runtime differences and new subset diagnostics rather
        # than letting a device- or sample-specific early-stop trace redefine
        # the production artifact.
        refit_epoch = _resolve_production_refit_epoch(spec)
        combined_direct = np.concatenate((train_direct, val_direct), axis=0)
        combined_invariant = np.concatenate((train_invariant, val_invariant), axis=0)
        combined_ages = np.concatenate((train_ages, val_ages), axis=0)
        combined_labels = np.concatenate((train_labels, val_labels), axis=0)
        combined_booster = np.concatenate((booster_train, booster_val), axis=0)
        production_booster_state, _ = fit_torch_histogram_booster(
            combined_booster, combined_labels, combined_booster, booster_config,
        )
        production_booster_state["feature"] = tree_feature

        members: list[dict[str, Any]] = []
        for seed in spec["seeds"]:
            pretrain_path, pretrain_checkpoint = pretrain_records[int(seed)]
            seed_everything(seed)
            model = _build_target_model(
                spec=spec, contract=contract, pretrain_checkpoint=pretrain_checkpoint,
            )
            _, refit_summary = _fit_target_fixed_epochs(
                model,
                (combined_direct, combined_invariant, combined_ages, combined_labels),
                seed=seed,
                device=device,
                epochs=refit_epoch,
                batch_size=int(transfer_cfg["batch_size"]),
                learning_rate=float(transfer_cfg["learning_rate"]),
                weight_decay=float(transfer_cfg["weight_decay"]),
                shared_aux_weight=float(transfer_cfg["shared_aux_weight"]),
            )
            _, shared_refit_summary = _fit_shared_fixed_epochs(
                model,
                (combined_invariant, combined_labels),
                seed=seed,
                device=device,
                epochs=refit_epoch,
                batch_size=int(transfer_cfg["batch_size"]),
                learning_rate=float(transfer_cfg["learning_rate"]),
                weight_decay=float(transfer_cfg["weight_decay"]),
                loss_weight=float(transfer_cfg["shared_aux_weight"]),
            )
            checkpoint_path = output_dir / component / f"{spec['route_id'].lower()}_{component}_s{seed}.pt"
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint = {
                "schema": SCHEMA_TRANSFER,
                "framework": "pytorch",
                "torch_version": torch.__version__,
                "stage": "target_transfer",
                "component": component,
                "route_id": spec["route_id"],
                "model_label": spec["model_label"],
                "seed": seed,
                "config_sha256": sha256(config["_path"]),
                **_implementation_contract(root),
                "feature_contract": contract,
                "normalization": normalization,
                "architecture": {
                    "kind": "transferred_shared_expert_plus_target_mlp",
                    "shared_hidden": list(pretrain_checkpoint["architecture"]["shared_hidden"]),
                    "direct_hidden": list(transfer_cfg["direct_hidden"]),
                    "dropout": float(transfer_cfg["dropout"]),
                    "shared_aux_weight": float(transfer_cfg["shared_aux_weight"]),
                    "shared_output_weight": float(transfer_cfg["shared_output_weight"]),
                },
                "route": {
                    "segmentation": deepcopy(spec["segmentation"]),
                    "selection_aggregation": spec["selection_aggregation"],
                    "production_point_aggregation": spec["production_point_aggregation"],
                    "production_point_seed": spec["production_point_seed"],
                    "interval_member_seeds": list(spec["interval_member_seeds"]),
                    "postprocess": spec["postprocess"],
                    "rmax": rmax,
                    "rul_unit": spec["rul_unit"],
                },
                "booster_state": production_booster_state,
                "training": {
                    "selection": selection_summaries[int(seed)],
                    "production_refit": {
                        **refit_summary,
                        "epoch_rule": "frozen_historical_validation_epoch",
                        "refit_epoch": refit_epoch,
                        "selection_epoch_median": selection_epoch_median,
                    },
                    "shared_adapter_refit": shared_refit_summary,
                    "train_tensor": relative_path(paths["target_train_tensor"], root),
                    "train_tensor_sha256": train_snapshot["file_sha256"],
                    "train_payload_sha256": train_snapshot["payload_sha256"],
                    "validation_tensor": relative_path(paths["target_val_tensor"], root),
                    "validation_tensor_sha256": val_snapshot["file_sha256"],
                    "validation_payload_sha256": val_snapshot["payload_sha256"],
                    "holdout_read": False,
                },
                "pretraining": {
                    "checkpoint": relative_path(pretrain_path, root),
                    "checkpoint_sha256": sha256(pretrain_path),
                    "source_tensor": pretrain_checkpoint["source"],
                },
                "model_state": model.state_dict(),
            }
            _verify_tensor_snapshot(train_snapshot, train)
            _verify_tensor_snapshot(val_snapshot, val)
            torch.save(checkpoint, checkpoint_path)
            members.append({
                "seed": seed,
                "path": relative_path(checkpoint_path, root),
                "sha256": sha256(checkpoint_path),
                "selected_epoch": selection_summaries[int(seed)]["selected_epoch"],
                "refit_epoch": refit_epoch,
            })
        line_receipt = {
            "route_id": spec["route_id"],
            "model_label": spec["model_label"],
            "framework": "pytorch",
            "feature_contract": contract,
            "selection_aggregation": spec["selection_aggregation"],
            "production_point_aggregation": spec["production_point_aggregation"],
            "production_point_seed": spec["production_point_seed"],
            "interval_member_seeds": list(spec["interval_member_seeds"]),
            "segmentation": deepcopy(spec["segmentation"]),
            "postprocess": spec["postprocess"],
            "booster": {
                "schema": production_booster_state["schema"],
                "framework": production_booster_state["framework"],
                "feature": production_booster_state["feature"],
                "input_dimension": production_booster_state["input_dimension"],
                "iterations": production_booster_state["iterations"],
                "learning_rate": production_booster_state["learning_rate"],
                "bin_count": production_booster_state["bin_count"],
                "max_leaf_nodes": production_booster_state["max_leaf_nodes"],
                "min_samples_leaf": production_booster_state["min_samples_leaf"],
                "l2_regularization": production_booster_state["l2_regularization"],
                "selection_fit_scope": "target_train_only",
                "production_fit_scope": "target_train_plus_validation",
                "reused_validated_checkpoint": False,
            },
            "selection_validation_metrics": metrics,
            "refit": {
                "scope": "target_train_plus_validation",
                "epoch_rule": "frozen_historical_validation_epoch",
                "epoch": refit_epoch,
                "selection_epoch_median": selection_epoch_median,
                "sample_count": len(combined_labels),
                "holdout_read": False,
            },
            "members": members,
            "rmax": rmax,
            "rul_unit": spec["rul_unit"],
            "normalization": normalization,
            "target_train_tensor": relative_path(paths["target_train_tensor"], root),
            "target_train_tensor_sha256": train_snapshot["file_sha256"],
            "target_train_payload_sha256": train_snapshot["payload_sha256"],
            "target_val_tensor": relative_path(paths["target_val_tensor"], root),
            "target_val_tensor_sha256": val_snapshot["file_sha256"],
            "target_val_payload_sha256": val_snapshot["payload_sha256"],
            "target_norm": relative_path(paths["target_norm"], root),
            "target_norm_sha256": sha256(paths["target_norm"]),
            "target_preprocess": relative_path(paths["target_preprocess"], root),
            "target_preprocess_sha256": sha256(paths["target_preprocess"]),
        }
        receipt["lines"][component] = line_receipt
        manifest["routes"][component] = {
            **line_receipt,
        }
    for snapshot, payload in input_snapshots:
        _verify_tensor_snapshot(snapshot, payload)
    receipt_path = output_dir / "transfer_receipt.json"
    _json_dump(receipt_path, receipt)
    manifest["transfer_receipt_sha256"] = sha256(receipt_path)
    _json_dump(output_dir / "manifest.json", manifest)
    return receipt


@dataclass
class LoadedRoute:
    component: str
    route_id: str
    label: str
    models: list[TargetSegmentedModel]
    boosters: list[dict[str, Any]]
    feature_contract: dict[str, Any]
    normalization: dict[str, Any]
    selection_aggregation: str
    production_point_aggregation: str
    production_point_seed: int | None
    interval_member_seeds: tuple[int, ...]
    postprocess: str
    rmax: float
    rul_unit: str


def _posix_artifact_alias_relative(value: str, source: str) -> Path | None:
    artifact = PurePosixPath(value)
    alias = PurePosixPath(source)
    if not artifact.is_absolute() or not alias.is_absolute():
        return None
    try:
        return Path(str(artifact.relative_to(alias)))
    except ValueError:
        return None


def _manifest_artifact_path(
    root: Path,
    value: Any,
    label: str,
    *,
    allowed_external_roots: Sequence[Path] = (),
    artifact_aliases: Mapping[str, str] | None = None,
) -> Path:
    if not isinstance(value, str) or not value:
        raise CompetitionContractError(f"production manifest {label} path is missing")
    path = Path(value)
    # Keep the lexical root check separate from symlink resolution.  The
    # isolated Rack checkout intentionally exposes registered data/config
    # trees through read-only links, so those links may resolve outside the
    # checkout while still remaining contract-bound and hash-verified.
    lexical: Path | None = None
    if artifact_aliases:
        for source, target in artifact_aliases.items():
            if not isinstance(source, str) or not isinstance(target, str):
                raise CompetitionContractError(f"production manifest {label} aliases are invalid")
            relative = _posix_artifact_alias_relative(value, source)
            if relative is None and path.is_absolute():
                source_path = Path(source)
                try:
                    relative = path.relative_to(source_path)
                except ValueError:
                    continue
            if relative is None:
                continue
            lexical = Path(os.path.abspath(str(root / target / relative)))
            break
    if lexical is None:
        lexical = Path(os.path.abspath(str(path if path.is_absolute() else root / path)))
    resolved = lexical.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        for allowed_root in allowed_external_roots:
            try:
                resolved.relative_to(Path(allowed_root).resolve())
                return resolved
            except ValueError:
                continue
        raise CompetitionContractError(
            f"production manifest {label} escapes the project root"
        ) from None
    return resolved


def _read_manifest(root: Path, path: str | Path) -> tuple[dict[str, Any], Path]:
    root = root.resolve()
    manifest_path = Path(path)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = manifest_path.resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompetitionContractError(f"cannot read manifest: {manifest_path}") from exc
    if manifest.get("schema") != SCHEMA_MANIFEST or manifest.get("framework", {}).get("name") != "pytorch":
        raise CompetitionContractError("production manifest is not the PyTorch S22/S21 contract")
    artifact_aliases = manifest.get("artifact_aliases", {})
    if not isinstance(artifact_aliases, Mapping):
        raise CompetitionContractError("production manifest artifact aliases are invalid")
    config_path = _manifest_artifact_path(
        root, manifest.get("config"), "config", artifact_aliases=artifact_aliases,
    )
    transfer_receipt_path = _manifest_artifact_path(
        root, manifest.get("transfer_receipt"), "transfer receipt", artifact_aliases=artifact_aliases,
    )
    if not config_path.is_file() or sha256(config_path) != manifest.get("config_sha256"):
        raise CompetitionContractError("production manifest config hash drift")
    if (
        not transfer_receipt_path.is_file()
        or sha256(transfer_receipt_path) != manifest.get("transfer_receipt_sha256")
    ):
        raise CompetitionContractError("production manifest transfer receipt hash drift")
    config = load_config(config_path)
    try:
        transfer_receipt = json.loads(transfer_receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompetitionContractError("production transfer receipt cannot be read") from exc
    if (
        transfer_receipt.get("schema") != SCHEMA_RECEIPT
        or transfer_receipt.get("stage") != "target_transfer"
    ):
        raise CompetitionContractError("production transfer receipt schema/stage mismatch")
    _validate_implementation_contract(manifest, root, "production manifest")
    _validate_implementation_contract(transfer_receipt, root, "production transfer receipt")
    if (
        transfer_receipt.get("status") != "pass"
        or transfer_receipt.get("framework", {}).get("name") != "pytorch"
        or transfer_receipt.get("config_sha256") != manifest.get("config_sha256")
        or transfer_receipt.get("holdout_read") is not False
        or manifest.get("holdout_used_for_training_or_selection") is not False
    ):
        raise CompetitionContractError("production transfer receipt contract drift")
    pretrain_receipt_path = _manifest_artifact_path(
        root,
        transfer_receipt.get("pretrain_receipt"),
        "public pretrain receipt",
        artifact_aliases=artifact_aliases,
    )
    if (
        not pretrain_receipt_path.is_file()
        or sha256(pretrain_receipt_path) != transfer_receipt.get("pretrain_receipt_sha256")
    ):
        raise CompetitionContractError("production public pretrain receipt hash drift")
    try:
        pretrain_receipt = json.loads(pretrain_receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompetitionContractError("production public pretrain receipt cannot be read") from exc
    if (
        pretrain_receipt.get("schema") != SCHEMA_RECEIPT
        or pretrain_receipt.get("stage") != "public_pretrain"
        or pretrain_receipt.get("status") != "pass"
        or pretrain_receipt.get("framework", {}).get("name") != "pytorch"
        or pretrain_receipt.get("config_sha256") != manifest.get("config_sha256")
        or pretrain_receipt.get("holdout_read") is not False
    ):
        raise CompetitionContractError("production public pretrain receipt contract drift")
    _validate_implementation_contract(pretrain_receipt, root, "production public pretrain receipt")
    allowed_external_roots: list[Path] = []
    data_root = resolve_data_root(root, config)
    for spec in config["lines"].values():
        for asset in (
            "target_train_tensor", "target_val_tensor", "target_norm", "target_preprocess",
        ):
            value = spec.get(asset)
            if isinstance(value, str) and value:
                allowed_external_roots.append((data_root / value).resolve().parent)
    routes = manifest.get("routes")
    if not isinstance(routes, dict) or set(routes) != {"bat", "rwa"}:
        raise CompetitionContractError("production manifest must contain BAT and RWA")
    for component, route in EXPECTED_ROUTES.items():
        try:
            receipt_line = transfer_receipt["lines"][component]
            receipt_route = receipt_line["route_id"]
        except (KeyError, TypeError) as exc:
            raise CompetitionContractError("production transfer receipt route ledger is incomplete") from exc
        if (
            routes[component].get("route_id") != route
            or config["lines"][component].get("route_id") != route
            or receipt_route != route
        ):
            raise CompetitionContractError("production route identity drift")
        for key, value in receipt_line.items():
            if routes[component].get(key) != value:
                raise CompetitionContractError("production manifest/transfer receipt route drift")
        for asset in (
            "target_train_tensor", "target_val_tensor", "target_norm", "target_preprocess",
        ):
            asset_path = _manifest_artifact_path(
                root,
                routes[component].get(asset),
                asset,
                allowed_external_roots=allowed_external_roots,
                artifact_aliases=artifact_aliases,
            )
            if (
                not asset_path.is_file()
                or sha256(asset_path) != routes[component].get(f"{asset}_sha256")
            ):
                raise CompetitionContractError(f"production {asset} hash drift")
    return manifest, manifest_path


def load_route_ensemble(root: str | Path, manifest_path: str | Path, component: str) -> LoadedRoute:
    root_path = Path(root).resolve()
    manifest, _ = _read_manifest(root_path, manifest_path)
    if component not in EXPECTED_ROUTES:
        raise CompetitionContractError(f"unsupported component: {component}")
    route = manifest["routes"][component]
    artifact_aliases = manifest.get("artifact_aliases", {})
    config_path = _manifest_artifact_path(
        root_path, manifest.get("config"), "config", artifact_aliases=artifact_aliases,
    )
    config = load_config(config_path)
    spec = config["lines"][component]
    if (
        route.get("model_label") != spec["model_label"]
        or route.get("selection_aggregation") != spec["selection_aggregation"]
        or route.get("production_point_aggregation") != spec["production_point_aggregation"]
        or route.get("production_point_seed") != spec["production_point_seed"]
        or route.get("interval_member_seeds") != spec["interval_member_seeds"]
        or route.get("segmentation") != spec["segmentation"]
        or route.get("postprocess") != spec["postprocess"]
        or route.get("rul_unit") != spec["rul_unit"]
    ):
        raise CompetitionContractError("production route/config contract drift")
    members = route.get("members")
    if not isinstance(members, list) or len(members) != 3:
        raise CompetitionContractError("production route must contain exactly three members")
    expected_seeds = {17, 42, 73}
    if {item.get("seed") for item in members} != expected_seeds:
        raise CompetitionContractError("production route seed set drift")
    contract = route["feature_contract"]
    models: list[TargetSegmentedModel] = []
    boosters: list[dict[str, Any]] = []
    for item in sorted(members, key=lambda value: int(value["seed"])):
        checkpoint_path = _manifest_artifact_path(
            root_path, item.get("path"), "route checkpoint", artifact_aliases=artifact_aliases,
        )
        if not checkpoint_path.is_file() or sha256(checkpoint_path) != item.get("sha256"):
            raise CompetitionContractError("production checkpoint hash drift")
        checkpoint = torch_load(checkpoint_path)
        if (
            checkpoint.get("schema") != SCHEMA_TRANSFER
            or checkpoint.get("framework") != "pytorch"
            or checkpoint.get("component") != component
            or checkpoint.get("route_id") != EXPECTED_ROUTES[component]
            or checkpoint.get("seed") != item["seed"]
            or checkpoint.get("model_label") != route["model_label"]
            or checkpoint.get("config_sha256") != manifest["config_sha256"]
            or checkpoint.get("feature_contract") != contract
            or checkpoint.get("normalization") != route["normalization"]
            or checkpoint.get("route", {}).get("segmentation") != route["segmentation"]
            or checkpoint.get("route", {}).get("selection_aggregation") != route["selection_aggregation"]
            or checkpoint.get("route", {}).get("production_point_aggregation") != route["production_point_aggregation"]
            or checkpoint.get("route", {}).get("production_point_seed") != route["production_point_seed"]
            or checkpoint.get("route", {}).get("interval_member_seeds") != route["interval_member_seeds"]
            or checkpoint.get("route", {}).get("postprocess") != route["postprocess"]
            or float(checkpoint.get("route", {}).get("rmax", float("nan"))) != float(route["rmax"])
            or checkpoint.get("route", {}).get("rul_unit") != route["rul_unit"]
            or checkpoint.get("training", {}).get("train_tensor") != route["target_train_tensor"]
            or checkpoint.get("training", {}).get("train_tensor_sha256") != route["target_train_tensor_sha256"]
            or checkpoint.get("training", {}).get("validation_tensor") != route["target_val_tensor"]
            or checkpoint.get("training", {}).get("validation_tensor_sha256") != route["target_val_tensor_sha256"]
            or checkpoint.get("training", {}).get("holdout_read") is not False
            or checkpoint.get("training", {}).get("selection", {}).get("selected_epoch") != item.get("selected_epoch")
            or checkpoint.get("training", {}).get("production_refit", {}).get("fit_scope") != "train_plus_validation"
            or checkpoint.get("training", {}).get("production_refit", {}).get("epoch_rule") != "frozen_historical_validation_epoch"
            or checkpoint.get("training", {}).get("production_refit", {}).get("refit_epoch") != route.get("refit", {}).get("epoch")
            or checkpoint.get("training", {}).get("production_refit", {}).get("selection_epoch_median") != route.get("refit", {}).get("selection_epoch_median")
            or checkpoint.get("training", {}).get("production_refit", {}).get("sample_count") != route.get("refit", {}).get("sample_count")
            or item.get("refit_epoch") != route.get("refit", {}).get("epoch")
        ):
            raise CompetitionContractError("production checkpoint contract drift")
        if route.get("refit", {}).get("epoch") != int(spec["production_refit_epoch"]):
            raise CompetitionContractError("production refit epoch drift")
        _validate_implementation_contract(checkpoint, root_path, "production checkpoint")
        architecture = checkpoint["architecture"]
        if (
            architecture.get("kind") != "transferred_shared_expert_plus_target_mlp"
            or architecture.get("shared_hidden") != list(spec["pretrain"]["hidden"])
            or architecture.get("direct_hidden") != list(spec["transfer"]["direct_hidden"])
            or float(architecture.get("dropout", float("nan"))) != float(spec["transfer"]["dropout"])
            or float(architecture.get("shared_aux_weight", float("nan"))) != float(spec["transfer"]["shared_aux_weight"])
            or float(architecture.get("shared_output_weight", float("nan"))) != float(spec["transfer"]["shared_output_weight"])
        ):
            raise CompetitionContractError("production checkpoint architecture drift")
        pretraining = checkpoint.get("pretraining", {})
        pretrain_path = _manifest_artifact_path(
            root_path,
            pretraining.get("checkpoint"),
            "public pretrain checkpoint",
            artifact_aliases=artifact_aliases,
        )
        if (
            not pretrain_path.is_file()
            or sha256(pretrain_path) != pretraining.get("checkpoint_sha256")
        ):
            raise CompetitionContractError("production pretraining checkpoint hash drift")
        segmentation = checkpoint["route"]["segmentation"]
        model = TargetSegmentedModel(
            invariant_dim=int(contract["invariant_dimension"]),
            direct_dim=int(contract["direct_dimension"]),
            shared_hidden=architecture["shared_hidden"],
            direct_hidden=architecture["direct_hidden"],
            dropout=float(architecture["dropout"]),
            threshold=float(segmentation["threshold"]),
            low_target_weight=float(segmentation["low_target_weight"]),
            high_target_weight=float(segmentation["high_target_weight"]),
            shared_output_weight=float(architecture["shared_output_weight"]),
        )
        try:
            model.load_state_dict(checkpoint["model_state"], strict=True)
        except (KeyError, RuntimeError, TypeError) as exc:
            raise CompetitionContractError("production neural state contract drift") from exc
        model.eval()
        models.append(model)
        booster_state = checkpoint.get("booster_state")
        booster_contract = route.get("booster", {})
        if (
            not isinstance(booster_state, dict)
            or booster_state.get("schema") != BOOSTER_SCHEMA
            or booster_state.get("framework") != "pytorch"
            or booster_state.get("feature") != spec["tree_expert"]["feature"]
            or booster_state.get("schema") != booster_contract.get("schema")
            or booster_state.get("framework") != booster_contract.get("framework")
            or booster_state.get("input_dimension") != booster_contract.get("input_dimension")
            or booster_state.get("iterations") != booster_contract.get("iterations")
            or booster_state.get("learning_rate") != booster_contract.get("learning_rate")
            or booster_state.get("bin_count") != booster_contract.get("bin_count")
            or booster_state.get("max_leaf_nodes") != booster_contract.get("max_leaf_nodes")
            or booster_state.get("min_samples_leaf") != booster_contract.get("min_samples_leaf")
            or booster_state.get("l2_regularization") != booster_contract.get("l2_regularization")
        ):
            raise CompetitionContractError("production checkpoint lacks the PyTorch booster")
        boosters.append(booster_state)
    return LoadedRoute(
        component=component,
        route_id=route["route_id"],
        label=route["model_label"],
        models=models,
        boosters=boosters,
        feature_contract=contract,
        normalization=route["normalization"],
        selection_aggregation=route["selection_aggregation"],
        production_point_aggregation=route["production_point_aggregation"],
        production_point_seed=route["production_point_seed"],
        interval_member_seeds=tuple(route["interval_member_seeds"]),
        postprocess=route["postprocess"],
        rmax=float(route["rmax"]),
        rul_unit=str(route["rul_unit"]),
    )


def predict_loaded_route(
    loaded: LoadedRoute,
    x: torch.Tensor | np.ndarray,
    rows: Sequence[Mapping[str, Any]],
    *,
    device_name: str = "cpu",
    batch_size: int = 1024,
) -> dict[str, Any]:
    array = x.detach().cpu().numpy() if isinstance(x, torch.Tensor) else np.asarray(x)
    contract = loaded.feature_contract
    if array.ndim != 3 or array.shape[1] != int(contract["window_length"]) or array.shape[2] != len(contract["channels"]):
        raise CompetitionContractError("prediction tensor does not match the frozen feature contract")
    direct, invariant, ages = extract_features(
        array, rows, channels=contract["channels"], age_scale=float(contract["age_scale"])
    )
    direct = _normalize(direct, loaded.normalization["direct"])
    invariant = _normalize(invariant, loaded.normalization["invariant"])
    device = _device(device_name)
    member_predictions: list[np.ndarray] = []
    for model, booster in zip(loaded.models, loaded.boosters):
        neural = _predict_target_model(
            model, direct, invariant, ages, device=device, batch_size=batch_size
        )
        booster_features = _booster_features(array, str(booster.get("feature")))
        tree = predict_torch_histogram_booster(booster, booster_features)
        target_weight = np.where(
            ages <= float(model.threshold),
            float(model.low_target_weight),
            float(model.high_target_weight),
        ).astype(np.float32)
        member_predictions.append(target_weight * neural + (1.0 - target_weight) * tree)
    raw = aggregate_members(
        member_predictions,
        loaded.production_point_aggregation,
        member_seeds=loaded.interval_member_seeds,
    )
    projected = monotone_project(raw, rows, loaded.postprocess)
    clipped = np.clip(projected, 0.0, 1.0)
    return {
        "component": loaded.component,
        "route_id": loaded.route_id,
        "model_label": loaded.label,
        "selection_aggregation": loaded.selection_aggregation,
        "production_point_aggregation": loaded.production_point_aggregation,
        "production_point_seed": loaded.production_point_seed,
        "interval_member_seeds": list(loaded.interval_member_seeds),
        "rul_unit": loaded.rul_unit,
        "raw_normalized": raw,
        "prediction_normalized": clipped,
        "prediction": clipped * loaded.rmax,
        "member_predictions_normalized": member_predictions,
    }


def _materialize_holdout(root: Path, data_root: Path, component: str) -> dict[str, Any]:
    try:
        import yaml
        from src.uncertainty.t2c import build_holdout_payload
    except ImportError as exc:
        raise CompetitionContractError("holdout materialization dependencies are unavailable") from exc
    preprocess_path = data_root / "configs" / "preprocess" / f"{component}_target.yaml"
    norm_path = data_root / "data" / "processed" / "norm_stats" / f"{component}_target.json"
    try:
        preprocess = yaml.safe_load(preprocess_path.read_text(encoding="utf-8"))
        norm = json.loads(norm_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise CompetitionContractError("holdout preprocess contract cannot be read") from exc
    return build_holdout_payload(data_root, target_config=preprocess, norm_doc=norm, line=component)


def _prediction_payload(
    root: Path,
    data_root: Path,
    component: str,
    split: str,
    input_path: str | Path | None,
) -> tuple[dict[str, Any], str, str, str]:
    if input_path is not None:
        path = Path(input_path)
        if not path.is_absolute():
            path = root / path
        payload = load_tensor(path)
        return payload, relative_path(path, root), sha256(path), "file_sha256"
    if split == "validation":
        path = data_root / "data" / "processed" / f"{component}_target_val.pt"
        return load_tensor(path), relative_path(path, root), sha256(path), "file_sha256"
    if split == "holdout":
        payload = _materialize_holdout(root, data_root, component)
        return (
            payload,
            "sealed_holdout_materialized_from_frozen_manifest",
            _payload_sha256(payload),
            "materialized_payload_sha256",
        )
    raise CompetitionContractError("split must be validation or holdout")


def _prediction_holdout_read(*, split: str, input_path: str | Path | None) -> bool | None:
    if input_path is not None:
        return None
    return split == "holdout"


def predict(
    *,
    root: Path,
    config: Mapping[str, Any],
    manifest_path: str | Path,
    output_dir: Path,
    device_name: str,
    split: str,
    component_selection: str,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    if input_path is not None and component_selection == "both":
        raise CompetitionContractError("--input requires one explicit component")
    data_root = resolve_data_root(root, config)
    output_dir.mkdir(parents=True, exist_ok=True)
    components = ("bat", "rwa") if component_selection == "both" else (component_selection,)
    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = root / manifest_file
    manifest_file = manifest_file.resolve()
    manifest_hash = sha256(manifest_file)
    receipt: dict[str, Any] = {
        "schema": SCHEMA_RECEIPT,
        "stage": "prediction",
        "status": "pass",
        "created_at": utc_now(),
        "framework": {"name": "pytorch", "version": torch.__version__},
        "manifest": relative_path(manifest_file, root),
        "manifest_sha256": manifest_hash,
        "split": split,
        "holdout_read": _prediction_holdout_read(split=split, input_path=input_path),
        "input_origin": "external_unknown" if input_path is not None else f"registered_{split}",
        "lines": {},
    }
    for component in components:
        loaded = load_route_ensemble(root, manifest_path, component)
        payload, source, source_hash, source_hash_kind = _prediction_payload(
            root, data_root, component, split, input_path
        )
        if tuple(payload["meta"]["channels"]) != tuple(loaded.feature_contract["channels"]):
            raise CompetitionContractError(f"{component} input channel order drift")
        rows = payload["meta"]["index"]
        result = predict_loaded_route(loaded, payload["x"], rows, device_name=device_name)
        csv_path = output_dir / f"{component}_{split}_predictions.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("window_id", "unit_id", "t_end", "route_id", "prediction", "rul_unit", "truth"))
            truth = payload.get("y_rul")
            for index, row in enumerate(rows):
                writer.writerow((
                    row.get("window_id", index), row.get("unit_id", ""), row.get("t_end", ""),
                    loaded.route_id, float(result["prediction"][index]), loaded.rul_unit,
                    float(truth[index]) if isinstance(truth, torch.Tensor) else "",
                ))
        line_receipt: dict[str, Any] = {
            "route_id": loaded.route_id,
            "model_label": loaded.label,
            "input": source,
            "input_sha256": source_hash,
            "input_sha256_kind": source_hash_kind,
            "payload_sha256": _payload_sha256(payload),
            "predictions": relative_path(csv_path, root),
            "predictions_sha256": sha256(csv_path),
            "n_windows": len(rows),
            "rul_unit": loaded.rul_unit,
        }
        if isinstance(payload.get("y_rul"), torch.Tensor):
            line_receipt["metrics"] = regression_metrics(
                result["prediction_normalized"], payload["y_rul"].numpy(),
                rmax=loaded.rmax, rows=rows,
            )
        receipt["lines"][component] = line_receipt
    if sha256(manifest_file) != manifest_hash:
        raise CompetitionContractError("production manifest changed during prediction")
    _json_dump(output_dir / f"prediction_{split}_receipt.json", receipt)
    return receipt


def _common_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", default="configs/competition/s22_s21.json")
    parser.add_argument("--root", default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--bat-route", default="S22")
    parser.add_argument("--rwa-route", default="S21")
    return parser


def pretrain_main(argv: Sequence[str] | None = None) -> int:
    parser = _common_parser("Public-domain pretraining for BAT S22 and RWA S21")
    parser.add_argument("--output-dir", default="results/competition/s22_s21_20260828/pretrain")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    validate_route_args(config, args.bat_route, args.rwa_route)
    root = resolve_root(config, args.root)
    receipt = pretrain(
        root=root, config=config, output_dir=(root / args.output_dir).resolve(),
        device_name=args.device,
    )
    print(json.dumps({"status": receipt["status"], "stage": receipt["stage"], "lines": list(receipt["lines"])}, ensure_ascii=False))
    return 0


def transfer_main(argv: Sequence[str] | None = None) -> int:
    parser = _common_parser("Target-domain transfer for BAT S22 and RWA S21")
    parser.add_argument("--pretrain-dir", default="results/competition/s22_s21_20260828/pretrain")
    parser.add_argument("--output-dir", default="results/competition/s22_s21_20260828/production")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    validate_route_args(config, args.bat_route, args.rwa_route)
    root = resolve_root(config, args.root)
    receipt = transfer(
        root=root, config=config, pretrain_dir=(root / args.pretrain_dir).resolve(),
        output_dir=(root / args.output_dir).resolve(), device_name=args.device,
    )
    print(json.dumps({"status": receipt["status"], "stage": receipt["stage"], "lines": list(receipt["lines"])}, ensure_ascii=False))
    return 0


def predict_main(argv: Sequence[str] | None = None) -> int:
    parser = _common_parser("Prediction for frozen BAT S22 and RWA S21")
    parser.add_argument("--manifest", default="results/competition/s22_s21_20260828/production/manifest.json")
    parser.add_argument("--output-dir", default="results/competition/s22_s21_20260828/prediction")
    parser.add_argument("--split", choices=("validation", "holdout"), default="validation")
    parser.add_argument("--component", choices=("both", "bat", "rwa"), default="both")
    parser.add_argument("--input", default=None)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    validate_route_args(config, args.bat_route, args.rwa_route)
    root = resolve_root(config, args.root)
    receipt = predict(
        root=root, config=config, manifest_path=args.manifest,
        output_dir=(root / args.output_dir).resolve(), device_name=args.device,
        split=args.split, component_selection=args.component, input_path=args.input,
    )
    print(json.dumps({"status": receipt["status"], "stage": receipt["stage"], "lines": receipt["lines"]}, ensure_ascii=False))
    return 0


__all__ = [
    "CompetitionContractError", "LoadedRoute", "TargetSegmentedModel",
    "aggregate_members", "extract_features", "feature_names", "load_config",
    "load_route_ensemble", "monotone_project", "predict_loaded_route",
    "pretrain_main", "transfer_main", "predict_main", "regression_metrics",
]
