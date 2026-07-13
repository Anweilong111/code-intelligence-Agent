from __future__ import annotations

import json
import sys
from pathlib import Path


SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "ArithmeticError": ArithmeticError,
    "Exception": Exception,
    "IndexError": IndexError,
    "KeyError": KeyError,
    "TypeError": TypeError,
    "ValueError": ValueError,
    "ZeroDivisionError": ZeroDivisionError,
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: boundary_probe_bootstrap.py <payload.json>")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    namespace = {"__builtins__": SAFE_BUILTINS}
    try:
        exec(compile(payload["source"], "<candidate>", "exec"), namespace)
        function = namespace[payload["function_name"]]
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "unsupported",
                    "reason": "candidate_function_bootstrap_failed",
                    "exception_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            )
        )
        raise SystemExit(2) from exc

    forbidden = set(payload.get("forbidden_exceptions", []))
    results = []
    failed = False
    for index, case in enumerate(payload.get("cases", [])):
        try:
            value = function(*case.get("args", []), **case.get("kwargs", {}))
            results.append(
                {
                    "case_index": index,
                    "status": "pass",
                    "result_type": type(value).__name__,
                    "result_preview": repr(value)[:300],
                }
            )
        except Exception as exc:
            exception_type = type(exc).__name__
            is_forbidden = exception_type in forbidden
            failed = failed or is_forbidden
            results.append(
                {
                    "case_index": index,
                    "status": "fail" if is_forbidden else "warning",
                    "exception_type": exception_type,
                    "message": str(exc)[:300],
                }
            )
    print(
        json.dumps(
            {
                "status": "fail" if failed else "pass",
                "case_count": len(results),
                "forbidden_exceptions": sorted(forbidden),
                "results": results,
            }
        )
    )
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
