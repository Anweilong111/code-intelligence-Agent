from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "v4-phase1-linux-httpie-reproduction.yml"
)


def test_httpie_workflow_is_read_only_bounded_and_secret_free():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in text
    assert "type: choice" in text
    assert "CASE_SET: ${{ inputs.case_set || 'all-httpie-selection' }}" in text
    assert "secrets." not in text
    assert "github.token" not in text
    assert "persist-credentials" not in text
    assert "setup.py" not in text
    assert "setup.sh" not in text
    assert "API_KEY" not in text
    assert "eval " not in text
    for case_id in (2, 1, 3, 4, 5):
        assert f"bugsinpy-httpie-{case_id}" in text


def test_httpie_workflow_pins_actions_and_builds_three_exact_runtimes():
    text = WORKFLOW.read_text(encoding="utf-8")

    action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE)
    assert action_refs == [
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    ]
    assert "python=3.7.3" in text
    assert text.count("v4-bootstrap-runtime plan") == 1
    assert text.count("v4-bootstrap-runtime run") == 1
    assert "representatives=(" in text
    assert "--case-id \"${case_id}\"" in text
    assert "v4-reproduce run" in text
    assert "--targeted-timeout 180" in text
    assert "--regression-timeout 900" in text
    assert text.count("--require-pass") == 2


def test_httpie_workflow_preserves_nonpass_evidence_and_checks_accepted_cases():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "collection_status=0" in text
    assert "batch_summary.json" in text
    assert "reproduction_evidence_fingerprint" in text
    assert "x['catalog_status']=='accepted'" in text
    assert "catalog_status" in text
    assert "outputs_v4/linux-httpie/reproduction/*/v4_reproduction.json" in text
