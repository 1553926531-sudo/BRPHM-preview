#!/usr/bin/env python3
"""Run prediction from the frozen pure-PyTorch S22/S21 manifest."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.competition_s22_s21 import predict_main


if __name__ == "__main__":
    raise SystemExit(predict_main())
