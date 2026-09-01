import json
import re

from dashboard.telemetry_upload import TelemetryPredictionService


class _PublicContractPredictor:
    def production_contract(self):
        return {
            "status": "validated",
            "framework": "PyTorch",
            "manifest_sha256": "a" * 64,
            "routes": {
                "bat": {
                    "model_name": "电池部件剩余寿命预测模型（储能系统）",
                    "component_name": "电池部件（储能系统）",
                    "n_members": 3,
                    "member_sha256s": ["b" * 64] * 3,
                    "selection_aggregation": "median3",
                    "production_point_aggregation": "median3",
                    "production_point_seed": None,
                    "interval_member_seeds": [17, 42, 73],
                },
                "rwa": {
                    "model_name": "反作用轮部件剩余寿命预测模型（姿态控制执行器）",
                    "component_name": "反作用轮部件（姿态控制执行器）",
                    "n_members": 3,
                    "member_sha256s": ["c" * 64] * 3,
                    "selection_aggregation": "median3",
                    "production_point_aggregation": "median3",
                    "production_point_seed": None,
                    "interval_member_seeds": [17, 42, 73],
                },
            },
        }


def test_public_schema_does_not_expose_internal_model_identity():
    schema = TelemetryPredictionService(".", predictor=_PublicContractPredictor()).public_upload_schema()
    encoded = json.dumps(schema, ensure_ascii=False).lower()
    for token in ("manifest_sha256", "member_sha256", "interval_member_seeds", "production_point_seed", "s21", "s22"):
        assert token not in encoded
    assert schema["production_model"]["components"]["bat"]["n_members"] == 3
    assert schema["production_model"]["components"]["bat"]["selection_method"] == "三个独立模型结果取中位数"


def test_public_schema_has_no_relative_source_path_or_internal_stage_token():
    schema = TelemetryPredictionService(".", predictor=_PublicContractPredictor()).public_upload_schema()
    encoded = json.dumps(schema, ensure_ascii=False)
    assert not re.search(r"(?:sim|gmat|src|configs|results|scripts|data)[/\\]", encoded, re.I)
    assert not re.search(r"(?<![A-Za-z0-9])(?:t[123](?:[abc])?|gate\\d+|holdout|checkpoint|preflight|receipt|seed)(?![A-Za-z0-9])", encoded, re.I)
