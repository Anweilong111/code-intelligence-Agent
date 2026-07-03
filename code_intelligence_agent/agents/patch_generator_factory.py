from __future__ import annotations

from code_intelligence_agent.agents.llm_client import create_patch_client
from code_intelligence_agent.agents.llm_patch_generator import LLMPatchGenerator
from code_intelligence_agent.agents.patch_generator import PatchGenerator


def build_patch_generator(mode: str):
    if mode == "rule":
        return PatchGenerator()
    if mode == "llm":
        return LLMPatchGenerator(create_patch_client())
    raise ValueError(f"Unsupported patch mode: {mode}")
