# -*- coding: utf-8 -*-
"""Internal shell-free adapter for non-Python CLI interpreters.

The executable name and fixed argv prefix come only from the machine registry;
browser parameters are appended as literal tokens by ``operations.py``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

ALLOWED_LAUNCHERS = {"bash", "cmd", "gmake", "make", "matlab", "npm", "powershell", "pwsh"}


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0] not in ALLOWED_LAUNCHERS:
        print("BRPHM CLI adapter: interpreter is not registered", file=sys.stderr)
        return 64
    launcher = values.pop(0)
    resolved = shutil.which(launcher)
    if not resolved:
        print(f"BRPHM CLI adapter: interpreter is unavailable: {launcher}", file=sys.stderr)
        return 69
    completed = subprocess.run([resolved, *values], shell=False, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
