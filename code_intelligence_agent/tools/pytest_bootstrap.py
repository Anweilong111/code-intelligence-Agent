from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: pytest_bootstrap.py <repo> [pytest args...]", file=sys.stderr)
        return 2

    repo = Path(args[0]).resolve()
    pytest_args = args[1:]

    import pytest

    os.chdir(repo)
    repo_text = str(repo)
    if sys.path and sys.path[0] == "":
        sys.path[0] = repo_text
    elif repo_text not in sys.path:
        sys.path.insert(0, repo_text)
    return int(pytest.main(pytest_args))


if __name__ == "__main__":
    raise SystemExit(main())
