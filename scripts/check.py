"""Verificación local: ruff, mypy strict y pytest (sin e2e).

Uso:
    python scripts/check.py          # las 3 gates
    python scripts/check.py --skip-tests  # solo ruff + mypy
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], desc: str) -> bool:
    print(f"\n\033[1;36m[{desc}]\033[0m")
    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode == 0


def main() -> int:
    skip_tests = "--skip-tests" in sys.argv

    ok = True
    ok &= run(["ruff", "check", "."], "ruff check")
    ok &= run(["mypy", "--strict", "."], "mypy --strict")
    if not skip_tests:
        ok &= run(
            ["pytest", "-m", "not e2e", "-q"], "pytest -m 'not e2e'"
        )

    print()
    if ok:
        print("\033[1;32mTodas las gates pasaron.\033[0m")
        return 0
    else:
        print("\033[1;31mHay gates que fallaron.\033[0m", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
