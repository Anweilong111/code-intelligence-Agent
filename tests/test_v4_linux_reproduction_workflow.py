from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "v4-phase1-linux-reproduction.yml"


def test_linux_reproduction_workflow_is_read_only_and_secret_free():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in text
    assert "secrets." not in text
    assert "github.token" not in text
    assert "persist-credentials" not in text
    assert "setup.py" not in text
    assert "setup.sh" not in text
    assert "API_KEY" not in text


def test_linux_reproduction_workflow_pins_external_action_and_three_gates():
    text = WORKFLOW.read_text(encoding="utf-8")

    action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE)
    assert action_refs == [
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    ]
    assert "python=3.7.0" in text
    assert "v4-bootstrap-runtime run" in text
    assert "v4-reproduce run" in text
    assert "--targeted-timeout 180" in text
    assert "--regression-timeout 900" in text
    assert text.count("--require-pass") == 2
