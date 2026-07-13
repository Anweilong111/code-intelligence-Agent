from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def build_llm_cost_evidence(
    sources: list[dict[str, Any]],
    *,
    source_paths: list[str] | None = None,
    input_usd_per_1k_tokens: float | None = None,
    output_usd_per_1k_tokens: float | None = None,
) -> dict[str, Any]:
    paths = source_paths or []
    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, payload in enumerate(sources):
        source_path = paths[index] if index < len(paths) else ""
        _collect_usage_records(
            payload,
            source_path=source_path,
            json_path="$",
            inherited_provider="",
            inherited_model="",
            records=records,
            seen_keys=seen_keys,
            input_usd_per_1k_tokens=input_usd_per_1k_tokens,
            output_usd_per_1k_tokens=output_usd_per_1k_tokens,
        )
    token_records = [record for record in records if record["total_tokens"] > 0]
    cost_records = [
        record for record in token_records if record.get("estimated_cost_usd") is not None
    ]
    total_tokens = sum(_int(record.get("total_tokens")) for record in token_records)
    prompt_tokens = sum(_int(record.get("prompt_tokens")) for record in token_records)
    completion_tokens = sum(
        _int(record.get("completion_tokens")) for record in token_records
    )
    total_cost = round(
        sum(_float(record.get("estimated_cost_usd")) for record in cost_records),
        8,
    )
    status = "pass" if token_records and cost_records else "incomplete"
    return {
        "status": status,
        "reason": _reason(status, token_records, cost_records),
        "source_paths": paths,
        "pricing": {
            "input_usd_per_1k_tokens": input_usd_per_1k_tokens,
            "output_usd_per_1k_tokens": output_usd_per_1k_tokens,
            "source": (
                "configured"
                if input_usd_per_1k_tokens is not None
                and output_usd_per_1k_tokens is not None
                else "not_configured"
            ),
        },
        "summary": {
            "source_count": len(sources),
            "usage_record_count": len(records),
            "token_record_count": len(token_records),
            "cost_record_count": len(cost_records),
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_estimated_cost": total_cost,
            "average_estimated_cost_per_record": (
                round(total_cost / len(cost_records), 8) if cost_records else 0.0
            ),
        },
        "llm_token_cost": {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "average_tokens_per_case": (
                round(total_tokens / len(token_records), 4) if token_records else 0.0
            ),
            "token_case_count": len(token_records),
            "total_estimated_cost": total_cost,
            "average_estimated_cost_per_case": (
                round(total_cost / len(cost_records), 8) if cost_records else 0.0
            ),
            "cost_case_count": len(cost_records),
        },
        "records": records,
        "next_actions": _next_actions(status, token_records, cost_records),
    }


def write_llm_cost_evidence_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "llm_cost_evidence.json"
    markdown_path = root / "llm_cost_evidence.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(render_llm_cost_evidence_markdown(payload), encoding="utf-8")
    return {
        "llm_cost_evidence_json": str(json_path),
        "llm_cost_evidence_markdown": str(markdown_path),
    }


def render_llm_cost_evidence_markdown(payload: dict[str, Any]) -> str:
    summary = _dict(payload.get("summary"))
    token_cost = _dict(payload.get("llm_token_cost"))
    pricing = _dict(payload.get("pricing"))
    lines = [
        "# LLM Cost Evidence",
        "",
        f"- Status: `{_markdown_cell(payload.get('status') or 'unknown')}`",
        f"- Reason: `{_markdown_cell(payload.get('reason') or 'none')}`",
        f"- Usage Records: {_int(summary.get('usage_record_count'))}",
        f"- Cost Records: {_int(summary.get('cost_record_count'))}",
        f"- Total Tokens: {_int(token_cost.get('total_tokens'))}",
        f"- Prompt Tokens: {_int(token_cost.get('prompt_tokens'))}",
        f"- Completion Tokens: {_int(token_cost.get('completion_tokens'))}",
        f"- Estimated Cost USD: {_float(token_cost.get('total_estimated_cost')):.8f}",
        f"- Input USD / 1K Tokens: {_markdown_cell(pricing.get('input_usd_per_1k_tokens'))}",
        f"- Output USD / 1K Tokens: {_markdown_cell(pricing.get('output_usd_per_1k_tokens'))}",
        "",
        "## Records",
        "",
        "| Source | JSON Path | Model | Prompt | Completion | Total | Cost USD |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for record_value in _list(payload.get("records")):
        record = _dict(record_value)
        lines.append(
            "| "
            f"{_markdown_cell(record.get('source_path'))} | "
            f"{_markdown_cell(record.get('json_path'))} | "
            f"{_markdown_cell(record.get('model'))} | "
            f"{_int(record.get('prompt_tokens'))} | "
            f"{_int(record.get('completion_tokens'))} | "
            f"{_int(record.get('total_tokens'))} | "
            f"{_markdown_cell(record.get('estimated_cost_usd'))} |"
        )
    lines.extend(["", "## Next Actions", ""])
    for action in _list(payload.get("next_actions")):
        lines.append(f"- {action}")
    if not _list(payload.get("next_actions")):
        lines.append("- LLM cost evidence is ready for v1_evaluation_summary.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build auditable LLM token and cost evidence from JSON reports."
    )
    parser.add_argument("output_dir")
    parser.add_argument("source_json", nargs="+")
    parser.add_argument("--input-usd-per-1k-tokens", type=float, default=None)
    parser.add_argument("--output-usd-per-1k-tokens", type=float, default=None)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit with status 1 unless token and cost records are present.",
    )
    args = parser.parse_args(argv)

    input_rate = _first_float(
        args.input_usd_per_1k_tokens,
        os.environ.get("CIA_LLM_INPUT_USD_PER_1K_TOKENS"),
    )
    output_rate = _first_float(
        args.output_usd_per_1k_tokens,
        os.environ.get("CIA_LLM_OUTPUT_USD_PER_1K_TOKENS"),
    )
    paths = [str(path) for path in args.source_json]
    payload = build_llm_cost_evidence(
        [_load_json(Path(path)) for path in paths],
        source_paths=paths,
        input_usd_per_1k_tokens=input_rate,
        output_usd_per_1k_tokens=output_rate,
    )
    write_llm_cost_evidence_artifacts(payload, args.output_dir)
    if args.format == "markdown":
        print(render_llm_cost_evidence_markdown(payload), end="")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    if args.require_pass and payload["status"] != "pass":
        raise SystemExit(1)


def _collect_usage_records(
    value: Any,
    *,
    source_path: str,
    json_path: str,
    inherited_provider: str,
    inherited_model: str,
    records: list[dict[str, Any]],
    seen_keys: set[str],
    input_usd_per_1k_tokens: float | None,
    output_usd_per_1k_tokens: float | None,
) -> None:
    if isinstance(value, dict):
        provider = str(value.get("provider") or inherited_provider or "")
        model = str(value.get("model") or inherited_model or "")
        usage = _dict(value.get("usage"))
        if _has_usage_tokens(usage):
            response_id = str(value.get("id") or value.get("response_id") or "")
            dedupe_key = response_id or (
                f"{source_path}:{json_path}:"
                f"{usage.get('prompt_tokens')}:{usage.get('completion_tokens')}:"
                f"{usage.get('total_tokens')}"
            )
            if dedupe_key not in seen_keys:
                seen_keys.add(dedupe_key)
                records.append(
                    _usage_record(
                        usage,
                        source_path=source_path,
                        json_path=json_path,
                        response_id=response_id,
                        provider=provider,
                        model=model,
                        cost_estimate=_dict(value.get("cost_estimate")),
                        input_usd_per_1k_tokens=input_usd_per_1k_tokens,
                        output_usd_per_1k_tokens=output_usd_per_1k_tokens,
                    )
                )
        for key, item in value.items():
            _collect_usage_records(
                item,
                source_path=source_path,
                json_path=f"{json_path}.{key}",
                inherited_provider=provider,
                inherited_model=model,
                records=records,
                seen_keys=seen_keys,
                input_usd_per_1k_tokens=input_usd_per_1k_tokens,
                output_usd_per_1k_tokens=output_usd_per_1k_tokens,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_usage_records(
                item,
                source_path=source_path,
                json_path=f"{json_path}[{index}]",
                inherited_provider=inherited_provider,
                inherited_model=inherited_model,
                records=records,
                seen_keys=seen_keys,
                input_usd_per_1k_tokens=input_usd_per_1k_tokens,
                output_usd_per_1k_tokens=output_usd_per_1k_tokens,
            )


def _usage_record(
    usage: dict[str, Any],
    *,
    source_path: str,
    json_path: str,
    response_id: str,
    provider: str,
    model: str,
    cost_estimate: dict[str, Any],
    input_usd_per_1k_tokens: float | None,
    output_usd_per_1k_tokens: float | None,
) -> dict[str, Any]:
    prompt_tokens = _first_int(
        usage.get("prompt_tokens"),
        usage.get("input_tokens"),
        usage.get("estimated_prompt_tokens"),
    )
    completion_tokens = _first_int(
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
        usage.get("estimated_completion_tokens"),
    )
    total_tokens = _first_int(
        usage.get("total_tokens"),
        (prompt_tokens + completion_tokens if prompt_tokens or completion_tokens else None),
        usage.get("estimated_total_tokens"),
    )
    configured_cost = _configured_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        input_usd_per_1k_tokens=input_usd_per_1k_tokens,
        output_usd_per_1k_tokens=output_usd_per_1k_tokens,
    )
    existing_cost = cost_estimate.get("estimated_cost_usd")
    estimated_cost = configured_cost
    cost_source = "configured_pricing" if configured_cost is not None else ""
    if estimated_cost is None and existing_cost is not None:
        estimated_cost = round(_float(existing_cost), 8)
        cost_source = "existing_cost_estimate"
    return {
        "source_path": source_path,
        "json_path": json_path,
        "response_id": response_id,
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "usage_source": str(usage.get("source") or "provider_usage"),
        "estimated_cost_usd": estimated_cost,
        "cost_source": cost_source,
    }


def _configured_cost(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    input_usd_per_1k_tokens: float | None,
    output_usd_per_1k_tokens: float | None,
) -> float | None:
    if input_usd_per_1k_tokens is None or output_usd_per_1k_tokens is None:
        return None
    return round(
        (prompt_tokens / 1000.0) * input_usd_per_1k_tokens
        + (completion_tokens / 1000.0) * output_usd_per_1k_tokens,
        8,
    )


def _has_usage_tokens(usage: dict[str, Any]) -> bool:
    return any(
        _int(value) > 0
        for value in (
            usage.get("prompt_tokens"),
            usage.get("input_tokens"),
            usage.get("completion_tokens"),
            usage.get("output_tokens"),
            usage.get("total_tokens"),
            usage.get("estimated_total_tokens"),
        )
    )


def _reason(
    status: str,
    token_records: list[dict[str, Any]],
    cost_records: list[dict[str, Any]],
) -> str:
    if status == "pass":
        return "llm_cost_evidence_ready"
    if not token_records:
        return "llm_usage_tokens_missing"
    if not cost_records:
        return "llm_pricing_missing"
    return "llm_cost_evidence_incomplete"


def _next_actions(
    status: str,
    token_records: list[dict[str, Any]],
    cost_records: list[dict[str, Any]],
) -> list[str]:
    if status == "pass":
        return []
    if not token_records:
        return ["Attach a JSON report containing LLM provider usage tokens."]
    if not cost_records:
        return [
            "Set CIA_LLM_INPUT_USD_PER_1K_TOKENS and "
            "CIA_LLM_OUTPUT_USD_PER_1K_TOKENS, or pass explicit pricing flags."
        ]
    return ["Inspect incomplete LLM cost records."]


def _load_json(path: Path) -> dict[str, Any]:
    return _dict(json.loads(path.read_text(encoding="utf-8")))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_int(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        return _int(value)
    return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":  # pragma: no cover
    main()
