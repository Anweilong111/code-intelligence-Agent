from __future__ import annotations

import json
import modulefinder
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: import_validation_bootstrap.py <repo> <relative-file>")
    root = Path(sys.argv[1]).resolve()
    relative = Path(sys.argv[2])
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SystemExit("target path escapes repository root") from exc
    if not target.exists() or target.suffix.lower() != ".py":
        raise SystemExit("target Python file is unavailable")
    search_path = [str(root)]
    src = root / "src"
    if src.is_dir():
        search_path.insert(0, str(src))
    finder = modulefinder.ModuleFinder(path=[*search_path, *sys.path])
    try:
        finder.run_script(str(target))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
            )
        )
        raise SystemExit(1) from exc
    bad_modules = sorted(str(name) for name in finder.badmodules)
    print(
        json.dumps(
            {
                "status": "pass" if not bad_modules else "warning",
                "module_count": len(finder.modules),
                "bad_modules": bad_modules[:100],
            }
        )
    )
    raise SystemExit(0 if not bad_modules else 1)


if __name__ == "__main__":
    main()
