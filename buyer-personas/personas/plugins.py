"""AI provider plugins.

Each provider turns aggregated persona facts into narrative copy. Providers
receive ONLY the aggregate fact sheet built by profile.facts_sheet(): no
customer names, IDs, emails, or raw transactions ever leave the machine
through a provider.

Built-in choices (set with --ai):
  template                       No AI. Deterministic text. Always works.
  ollama[:model]                 Local model via Ollama (default model: llama3.1)
  openai-compatible[:model]      Any OpenAI-style server: LM Studio, vLLM, OpenAI.
                                 Set PERSONA_BASE_URL (default http://localhost:1234/v1)
                                 and PERSONA_API_KEY if needed.
  anthropic[:model]              Anthropic API. Reads ANTHROPIC_API_KEY.
  file:/path/to/plugin.py        Custom provider. The file must define PROVIDER
                                 (an AIProvider instance or subclass).
"""

from __future__ import annotations

import importlib.util
import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, system: str, prompt: str) -> str | None:
        """Return text, or None to tell the pipeline to use built-in copy."""

    def describe(self) -> str:
        return self.name


class TemplateProvider(AIProvider):
    name = "template"

    def generate(self, system: str, prompt: str) -> str | None:
        return None


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach AI provider at {url}: {exc}") from exc


class OllamaProvider(AIProvider):
    def __init__(self, model: str = "llama3.1", host: str | None = None):
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.name = f"ollama:{model}"

    def generate(self, system: str, prompt: str) -> str:
        data = _post_json(
            f"{self.host}/api/chat",
            {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            {},
        )
        return data["message"]["content"]


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, model: str = "local-model", base_url: str | None = None, api_key_env: str = "PERSONA_API_KEY"):
        self.model = model
        self.base_url = (base_url or os.environ.get("PERSONA_BASE_URL") or "http://localhost:1234/v1").rstrip("/")
        self.api_key = os.environ.get(api_key_env, "")
        self.name = f"openai-compatible:{model}"

    def generate(self, system: str, prompt: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = _post_json(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
            },
            headers,
        )
        return data["choices"][0]["message"]["content"]


class AnthropicProvider(AIProvider):
    def __init__(self, model: str = "claude-haiku-5-5"):
        self.model = model
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use the anthropic provider")
        self.name = f"anthropic:{model}"

    def generate(self, system: str, prompt: str) -> str:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.model,
                "max_tokens": 1200,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )
        return "".join(block.get("text", "") for block in data["content"])


def load_file_provider(path: str) -> AIProvider:
    spec = importlib.util.spec_from_file_location("persona_custom_provider", Path(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load plugin file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = getattr(module, "PROVIDER", None)
    if provider is None:
        raise RuntimeError(f"{path} must define PROVIDER")
    if isinstance(provider, type):
        provider = provider()
    if not isinstance(provider, AIProvider):
        raise RuntimeError(f"PROVIDER in {path} must subclass AIProvider")
    return provider


def get_provider(spec: str = "template") -> AIProvider:
    kind, _, arg = spec.partition(":")
    kind = kind.lower()
    if kind == "template":
        return TemplateProvider()
    if kind == "ollama":
        return OllamaProvider(arg or "llama3.1")
    if kind in ("openai-compatible", "openai"):
        return OpenAICompatibleProvider(arg or "local-model")
    if kind == "anthropic":
        return AnthropicProvider(arg or "claude-haiku-5-5")
    if kind == "file":
        return load_file_provider(arg)
    raise ValueError(f"Unknown AI provider '{spec}'. See personas/plugins.py for options.")
