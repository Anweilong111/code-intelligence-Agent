from __future__ import annotations

import argparse
import json

from code_intelligence_agent.agents.llm_client import llm_config_audits_for_modes


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit LLM provider/model/key configuration without printing raw API keys."
        )
    )
    parser.add_argument(
        "--patch-mode",
        choices=["rule", "llm", "hybrid"],
        default="rule",
        help="Patch generation mode.",
    )
    parser.add_argument(
        "--judge-mode",
        choices=["none", "llm"],
        default="none",
        help="Case-level LLM-as-judge mode.",
    )
    parser.add_argument(
        "--patch-judge-mode",
        choices=["none", "llm"],
        default="none",
        help="Patch-level LLM judge mode.",
    )
    parser.add_argument(
        "--llm-score-mode",
        choices=["none", "llm"],
        default="none",
        help="Fault-localization LLMScore mode.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    args = parser.parse_args()
    audit = llm_config_audits_for_modes(
        patch_mode=args.patch_mode,
        judge_mode=args.judge_mode,
        patch_judge_mode=args.patch_judge_mode,
        llm_score_mode=args.llm_score_mode,
    )
    if args.format == "markdown":
        print(render_llm_config_audit_markdown(audit))
    else:
        print(json.dumps(audit, indent=2, ensure_ascii=False))


def render_llm_config_audit_markdown(audit: dict) -> str:
    lines = [
        "## LLM Configuration Audit",
        "",
        f"- Enabled Roles: {', '.join(audit.get('enabled_roles', [])) or 'none'}",
        f"- Configuration Complete: {audit.get('configuration_complete', False)}",
        "",
        (
            "| Role | Enabled | Provider | Model | Model Source | Base URL | "
            "API Key Present | API Key Source | Checked API Key Envs | "
            "Key Fingerprint | Warnings |"
        ),
        "| --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for role in audit.get("roles", []):
        warnings = ", ".join(role.get("warnings", []))
        checked_envs = ", ".join(role.get("checked_api_key_envs", []))
        lines.append(
            f"| {role.get('role', '')} | "
            f"{role.get('enabled', False)} | "
            f"{role.get('provider', '')} | "
            f"{role.get('model', '')} | "
            f"{role.get('model_source', '')} | "
            f"{role.get('base_url', '')} | "
            f"{role.get('api_key_present', False)} | "
            f"{role.get('api_key_source', '') or 'none'} | "
            f"{checked_envs or 'none'} | "
            f"{role.get('api_key_fingerprint', '') or 'none'} | "
            f"{warnings or 'none'} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
