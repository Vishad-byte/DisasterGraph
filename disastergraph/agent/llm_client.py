from __future__ import annotations

import importlib
from typing import Any

from disastergraph.config import Settings


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        provider = (settings.llm_provider or "openrouter").strip().lower()
        if provider == "claude":
            self.provider = "claude"
        elif provider == "openrouter":
            self.provider = "openrouter"
        else:
            self.provider = "openrouter"

    def complete_json(self, prompt: str) -> str:
        if self.provider == "claude":
            return self._complete_claude(prompt)
        return self._complete_openrouter(prompt)

    def _complete_claude(self, prompt: str) -> str:
        anthropic_mod = importlib.import_module("anthropic")
        client = anthropic_mod.Anthropic(api_key=self.settings.claude_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(response.content[0].text)

    def _complete_openrouter(self, prompt: str) -> str:
        httpx = importlib.import_module("httpx")

        api_key = self.settings.openrouter_api_key
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")

        base = self.settings.openrouter_base_url.rstrip("/")
        url = f"{base}/chat/completions"

        headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.openrouter_site_url:
            headers["HTTP-Referer"] = self.settings.openrouter_site_url
        if self.settings.openrouter_app_name:
            headers["X-Title"] = self.settings.openrouter_app_name

        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices:
            raise ValueError("OpenRouter returned no choices")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenRouter returned empty content")
        return content
