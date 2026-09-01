from pathlib import Path

from src.competition_s22_s21 import load_config, validate_route_args


ROOT = Path(__file__).resolve().parents[1]


def test_competition_routes_are_fixed_and_pytorch() -> None:
    config = load_config(ROOT / "configs" / "competition" / "s22_s21.json")
    assert config["framework"] == "pytorch"
    assert config["lines"]["bat"]["route_id"] == "S22"
    assert config["lines"]["rwa"]["route_id"] == "S21"
    validate_route_args(config, "S22", "S21")
