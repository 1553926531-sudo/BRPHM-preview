#!/usr/bin/env python3
"""Run public-domain PyTorch pretraining for the frozen S22/S21 routes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.competition_s22_s21 import pretrain_main


if __name__ == "__main__":
    raise SystemExit(pretrain_main())
