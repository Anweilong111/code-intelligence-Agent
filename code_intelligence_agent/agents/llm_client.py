from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Protocol

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
ALIBABA_DASHSCOPE_CHAT_COMPLETIONS_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
ALIBABA_BEST_QWEN_MODEL = "qwen3-max-thinking"
ALIBABA_BEST_JUDGE_MODEL = ALIBABA_BEST_QWEN_MODEL
DEEPSEEK_BEST_MODEL = "deepseek-v4-pro"
DEEPSEEK_BEST_JUDGE_MODEL = DEEPSEEK_BEST_MODEL

PATCH_SYSTEM_PROMPT = (
    "You are a code repair assistant. Return only valid JSON with a "
    "fixed_source string, or a fixed_sources list when explicitly requested."
)
JUDGE_SYSTEM_PROMPT = (
    "You are a rigorous code-intelligence benchmark judge. Return only valid "
    "JSON with score, verdict, and reason fields."
)
LOCALIZATION_SYSTEM_PROMPT = (
    "You are a code fault-localization scorer. Return only valid JSON with "
    "scores for the provided function candidates."
)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    metadata: dict


@dataclass(frozen=True)
class LLMConfigAudit:
    role: str
    enabled: bool
    provider: str
    model: str
    model_source: str
    base_url: str
    base_url_source: str
    api_key_env: str
    checked_api_key_envs: list[str]
    api_key_present: bool
    api_key_source: str
    api_key_fingerprint: str
    api_key_length: int
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "model_source": self.model_source,
            "base_url": self.base_url,
            "base_url_source": self.base_url_source,
            "api_key_env": self.api_key_env,
            "checked_api_key_envs": self.checked_api_key_envs,
            "api_key_present": self.api_key_present,
            "api_key_source": self.api_key_source,
            "api_key_fingerprint": self.api_key_fingerprint,
            "api_key_length": self.api_key_length,
            "warnings": self.warnings,
        }


class LLMClient(Protocol):
    def complete(self, prompt: str) -> LLMResponse:
        ...


class OpenAICompatibleLLMClient:
    """Small HTTP client for OpenAI-compatible chat-completions endpoints."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 60,
        timeout_env: str | None = None,
        provider: str | None = None,
        system_prompt: str | None = None,
        api_key_env: str = "CIA_LLM_API_KEY",
        model_env: str = "CIA_LLM_MODEL",
        base_url_env: str = "CIA_LLM_BASE_URL",
        provider_env: str = "CIA_LLM_PROVIDER",
    ) -> None:
        self.provider = _normalize_provider(
            provider or os.environ.get(provider_env) or "openai"
        )
        self.api_key = api_key or _api_key_from_env(self.provider, api_key_env)
        self.model = _normalize_model_for_provider(
            self.provider,
            model
            or os.environ.get(model_env)
            or _default_model_for_provider(self.provider),
        )
        self.base_url = _normalize_chat_completions_url(
            base_url
            or os.environ.get(base_url_env)
            or _default_base_url_for_provider(self.provider)
        )
        self.timeout = _timeout_from_env(timeout_env, timeout)
        self.system_prompt = system_prompt or PATCH_SYSTEM_PROMPT
        if not self.api_key:
            raise ValueError(f"{api_key_env} is required for LLM requests.")

    def complete(self, prompt: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": self.system_prompt,
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
        return LLMResponse(text=text, metadata={"model": self.model, "raw": body})


def create_judge_client() -> OpenAICompatibleLLMClient:
    """Build the default judge client from environment variables."""

    return OpenAICompatibleLLMClient(
        provider=os.environ.get("CIA_JUDGE_PROVIDER", "deepseek"),
        api_key=os.environ.get("CIA_JUDGE_API_KEY"),
        model=os.environ.get("CIA_JUDGE_MODEL"),
        base_url=os.environ.get("CIA_JUDGE_BASE_URL"),
        timeout_env="CIA_JUDGE_TIMEOUT",
        api_key_env="CIA_JUDGE_API_KEY",
        model_env="CIA_JUDGE_MODEL",
        base_url_env="CIA_JUDGE_BASE_URL",
        provider_env="CIA_JUDGE_PROVIDER",
        system_prompt=JUDGE_SYSTEM_PROMPT,
    )


def create_patch_client() -> OpenAICompatibleLLMClient:
    """Build the default patch-generation/refinement client."""

    return OpenAICompatibleLLMClient(
        provider=os.environ.get("CIA_LLM_PROVIDER", "deepseek"),
        api_key=os.environ.get("CIA_LLM_API_KEY"),
        model=os.environ.get("CIA_LLM_MODEL"),
        base_url=os.environ.get("CIA_LLM_BASE_URL"),
        timeout_env="CIA_LLM_TIMEOUT",
        api_key_env="CIA_LLM_API_KEY",
        model_env="CIA_LLM_MODEL",
        base_url_env="CIA_LLM_BASE_URL",
        provider_env="CIA_LLM_PROVIDER",
        system_prompt=PATCH_SYSTEM_PROMPT,
    )


def create_localization_client() -> OpenAICompatibleLLMClient:
    """Build the default fault-localization scorer client."""

    return OpenAICompatibleLLMClient(
        provider=os.environ.get("CIA_LOCALIZATION_LLM_PROVIDER", "deepseek"),
        api_key=(
            os.environ.get("CIA_LOCALIZATION_LLM_API_KEY")
            or os.environ.get("CIA_JUDGE_API_KEY")
        ),
        model=os.environ.get("CIA_LOCALIZATION_LLM_MODEL"),
        base_url=os.environ.get("CIA_LOCALIZATION_LLM_BASE_URL"),
        timeout_env="CIA_LOCALIZATION_LLM_TIMEOUT",
        api_key_env="CIA_LOCALIZATION_LLM_API_KEY",
        model_env="CIA_LOCALIZATION_LLM_MODEL",
        base_url_env="CIA_LOCALIZATION_LLM_BASE_URL",
        provider_env="CIA_LOCALIZATION_LLM_PROVIDER",
        system_prompt=LOCALIZATION_SYSTEM_PROMPT,
    )


def create_alibaba_judge_client() -> OpenAICompatibleLLMClient:
    """Build an Alibaba/Qwen judge client from the shared judge env variables."""

    return OpenAICompatibleLLMClient(
        provider=os.environ.get("CIA_JUDGE_PROVIDER", "alibaba"),
        api_key=os.environ.get("CIA_JUDGE_API_KEY"),
        model=os.environ.get("CIA_JUDGE_MODEL"),
        base_url=os.environ.get("CIA_JUDGE_BASE_URL"),
        timeout_env="CIA_JUDGE_TIMEOUT",
        api_key_env="CIA_JUDGE_API_KEY",
        model_env="CIA_JUDGE_MODEL",
        base_url_env="CIA_JUDGE_BASE_URL",
        provider_env="CIA_JUDGE_PROVIDER",
        system_prompt=JUDGE_SYSTEM_PROMPT,
    )


def create_alibaba_localization_client() -> OpenAICompatibleLLMClient:
    """Build an Alibaba/Qwen fault-localization scorer client."""

    return OpenAICompatibleLLMClient(
        provider=os.environ.get("CIA_LOCALIZATION_LLM_PROVIDER", "alibaba"),
        api_key=(
            os.environ.get("CIA_LOCALIZATION_LLM_API_KEY")
            or os.environ.get("CIA_JUDGE_API_KEY")
        ),
        model=os.environ.get("CIA_LOCALIZATION_LLM_MODEL"),
        base_url=os.environ.get("CIA_LOCALIZATION_LLM_BASE_URL"),
        timeout_env="CIA_LOCALIZATION_LLM_TIMEOUT",
        api_key_env="CIA_LOCALIZATION_LLM_API_KEY",
        model_env="CIA_LOCALIZATION_LLM_MODEL",
        base_url_env="CIA_LOCALIZATION_LLM_BASE_URL",
        provider_env="CIA_LOCALIZATION_LLM_PROVIDER",
        system_prompt=LOCALIZATION_SYSTEM_PROMPT,
    )


def llm_config_audit(role: str, enabled: bool = True) -> LLMConfigAudit:
    """Resolve one LLM role configuration without exposing raw secrets."""

    profile = _llm_config_profile(role)
    provider_env = profile["provider_env"]
    model_env = profile["model_env"]
    base_url_env = profile["base_url_env"]
    api_key_env = profile["api_key_env"]
    provider = _normalize_provider(
        os.environ.get(provider_env) or profile["default_provider"]
    )
    model_value = os.environ.get(model_env)
    model = _normalize_model_for_provider(
        provider, model_value or _default_model_for_provider(provider)
    )
    base_url_value = os.environ.get(base_url_env)
    base_url = _normalize_chat_completions_url(
        base_url_value or _default_base_url_for_provider(provider)
    )
    key, key_source, checked_envs = _resolve_api_key_with_source(
        provider,
        api_key_env,
        fallback_envs=profile["fallback_api_key_envs"],
    )
    warnings = []
    if enabled and not key:
        warnings.append(f"missing_api_key:{api_key_env}")
    return LLMConfigAudit(
        role=profile["role"],
        enabled=enabled,
        provider=provider,
        model=model,
        model_source=model_env if model_value else "default",
        base_url=base_url,
        base_url_source=base_url_env if base_url_value else "default",
        api_key_env=api_key_env,
        checked_api_key_envs=checked_envs,
        api_key_present=bool(key),
        api_key_source=key_source,
        api_key_fingerprint=_api_key_fingerprint(key),
        api_key_length=len(key) if key else 0,
        warnings=warnings,
    )


def llm_config_audits_for_modes(
    *,
    patch_mode: str = "rule",
    judge_mode: str = "none",
    patch_judge_mode: str = "none",
    llm_score_mode: str = "none",
) -> dict:
    patch_enabled = str(patch_mode).lower() in {"llm", "hybrid"}
    judge_enabled = (
        str(judge_mode).lower() == "llm"
        or str(patch_judge_mode).lower() == "llm"
    )
    localization_enabled = str(llm_score_mode).lower() == "llm"
    audits = [
        llm_config_audit("patch_generation", enabled=patch_enabled),
        llm_config_audit("judge", enabled=judge_enabled),
        llm_config_audit("localization", enabled=localization_enabled),
    ]
    enabled_roles = [item.role for item in audits if item.enabled]
    missing_enabled_keys = [
        item.role
        for item in audits
        if item.enabled and not item.api_key_present
    ]
    return {
        "enabled_roles": enabled_roles,
        "configuration_complete": not missing_enabled_keys,
        "missing_enabled_api_key_roles": missing_enabled_keys,
        "roles": [item.to_dict() for item in audits],
    }


class StaticLLMClient:
    """Test helper and offline client that returns a fixed response."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(text=self.text, metadata={"mode": "static"})


class SequenceLLMClient:
    """Test helper and offline client that returns responses in order."""

    def __init__(self, texts: list[str]) -> None:
        if not texts:
            raise ValueError("SequenceLLMClient requires at least one response.")
        self.texts = list(texts)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LLMResponse:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.texts) - 1)
        return LLMResponse(
            text=self.texts[index],
            metadata={"mode": "sequence", "index": index},
        )


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower().replace("_", "-")
    if normalized in {"dashscope", "qwen", "aliyun", "alibaba-cloud"}:
        return "alibaba"
    if normalized in {
        "deep-seek",
        "deepseek-ai",
        "deepseek-v4",
        "deepseek-v4-pro",
        "deepseekv4pro",
    }:
        return "deepseek"
    return normalized


def _normalize_model_for_provider(provider: str, model: str) -> str:
    normalized = str(model).strip()
    if provider != "deepseek":
        return normalized
    alias = normalized.lower().replace("_", "").replace("-", "").replace(" ", "")
    if alias in {"deepseekv4pro", "deepseek4pro", "v4pro"}:
        return DEEPSEEK_BEST_MODEL
    if alias in {"deepseekv4flash", "deepseek4flash", "v4flash"}:
        return "deepseek-v4-flash"
    return normalized


def _timeout_from_env(timeout_env: str | None, default: int) -> int:
    if not timeout_env:
        return default
    value = os.environ.get(timeout_env)
    if value is None or not value.strip():
        return default
    try:
        timeout = int(value)
    except ValueError:
        return default
    return timeout if timeout > 0 else default


def _api_key_from_env(provider: str, primary_env: str) -> str | None:
    key, _, _ = _resolve_api_key_with_source(provider, primary_env)
    return key


def _resolve_api_key_with_source(
    provider: str,
    primary_env: str,
    fallback_envs: tuple[str, ...] = (),
) -> tuple[str | None, str, list[str]]:
    env_names = _api_key_env_names(provider, primary_env, fallback_envs)
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value, env_name, env_names
    return None, "", env_names


def _api_key_env_names(
    provider: str,
    primary_env: str,
    fallback_envs: tuple[str, ...] = (),
) -> list[str]:
    env_names = [primary_env, *fallback_envs]
    if provider == "alibaba":
        env_names.extend(["DASHSCOPE_API_KEY", "ALIBABA_API_KEY"])
    if provider == "deepseek":
        env_names.append("DEEPSEEK_API_KEY")
    deduped = []
    for env_name in env_names:
        if env_name not in deduped:
            deduped.append(env_name)
    return deduped


def _default_model_for_provider(provider: str) -> str:
    if provider == "deepseek":
        return DEEPSEEK_BEST_JUDGE_MODEL
    if provider == "alibaba":
        return ALIBABA_BEST_JUDGE_MODEL
    return "gpt-4.1-mini"


def _default_base_url_for_provider(provider: str) -> str:
    if provider == "deepseek":
        return DEEPSEEK_CHAT_COMPLETIONS_URL
    if provider == "alibaba":
        return ALIBABA_DASHSCOPE_CHAT_COMPLETIONS_URL
    return OPENAI_CHAT_COMPLETIONS_URL


def _normalize_chat_completions_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    if stripped in {"https://api.deepseek.com", "https://api.deepseek.com/beta"}:
        return f"{stripped}/chat/completions"
    if stripped.endswith("/v1") or stripped.endswith("/compatible-mode"):
        return f"{stripped}/chat/completions"
    if stripped.endswith("/compatible-mode/v1"):
        return f"{stripped}/chat/completions"
    return stripped


def _api_key_fingerprint(api_key: str | None) -> str:
    if not api_key:
        return ""
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _llm_config_profile(role: str) -> dict:
    normalized = role.strip().lower().replace("-", "_")
    aliases = {
        "patch": "patch_generation",
        "patch_generator": "patch_generation",
        "case_judge": "judge",
        "patch_judge": "judge",
        "llm_score": "localization",
        "fault_localization": "localization",
    }
    normalized = aliases.get(normalized, normalized)
    profiles = {
        "patch_generation": {
            "role": "patch_generation",
            "default_provider": "deepseek",
            "provider_env": "CIA_LLM_PROVIDER",
            "api_key_env": "CIA_LLM_API_KEY",
            "model_env": "CIA_LLM_MODEL",
            "base_url_env": "CIA_LLM_BASE_URL",
            "fallback_api_key_envs": (),
        },
        "judge": {
            "role": "judge",
            "default_provider": "deepseek",
            "provider_env": "CIA_JUDGE_PROVIDER",
            "api_key_env": "CIA_JUDGE_API_KEY",
            "model_env": "CIA_JUDGE_MODEL",
            "base_url_env": "CIA_JUDGE_BASE_URL",
            "fallback_api_key_envs": (),
        },
        "localization": {
            "role": "localization",
            "default_provider": "deepseek",
            "provider_env": "CIA_LOCALIZATION_LLM_PROVIDER",
            "api_key_env": "CIA_LOCALIZATION_LLM_API_KEY",
            "model_env": "CIA_LOCALIZATION_LLM_MODEL",
            "base_url_env": "CIA_LOCALIZATION_LLM_BASE_URL",
            "fallback_api_key_envs": ("CIA_JUDGE_API_KEY",),
        },
    }
    if normalized not in profiles:
        raise ValueError(f"Unsupported LLM config audit role: {role}")
    return profiles[normalized]
