from __future__ import annotations

import importlib
import json
import logging
import re
import time
from typing import Any

from disastergraph.config import Settings

LOGGER = logging.getLogger(__name__)


def _clean_json_markdown(text: str) -> str:
    """Strip markdown code fence if LLM returns ```json { ... } ```."""
    stripped = text.strip()
    if stripped.startswith("```"):
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped)
        if match:
            return match.group(1).strip()
    return stripped


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        provider = (settings.llm_provider or "openrouter").strip().lower()
        if provider in {"claude", "openrouter", "groq", "gemini"}:
            self.provider = provider
        else:
            self.provider = "openrouter"

    def complete_json(self, prompt: str) -> str:
        if self.provider == "claude":
            raw = self._complete_claude(prompt)
        elif self.provider == "groq":
            raw = self._complete_groq(prompt)
        elif self.provider == "gemini":
            raw = self._complete_gemini(prompt)
        else:
            raw = self._complete_openrouter(prompt)

        cleaned = _clean_json_markdown(raw)
        # Verify it's valid JSON
        try:
            json.loads(cleaned)
        except Exception:
            # If not valid JSON, try extracting first JSON object
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                cleaned = match.group(0)
        return cleaned

    def _complete_claude(self, prompt: str) -> str:
        anthropic_mod = importlib.import_module("anthropic")
        client = anthropic_mod.Anthropic(api_key=self.settings.claude_api_key)
        response = client.messages.create(
            model=self.settings.llm_model or "claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(response.content[0].text)

    def _complete_groq(self, prompt: str) -> str:
        httpx = importlib.import_module("httpx")
        api_key = self.settings.groq_api_key
        if not api_key:
            raise ValueError("GROQ_API_KEY is required when LLM_PROVIDER=groq")

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        model = self.settings.llm_model if ("llama" in self.settings.llm_model or "mixtral" in self.settings.llm_model) else "llama-3.3-70b-versatile"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        return self._post_openai_compat(httpx, url, headers, payload, provider_name="Groq")

    def _complete_gemini(self, prompt: str) -> str:
        httpx = importlib.import_module("httpx")
        api_key = self.settings.gemini_api_key or self.settings.openrouter_api_key
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")

        # Use Gemini's OpenAI-compatible endpoint
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        model = self.settings.llm_model if "gemini" in self.settings.llm_model else "gemini-2.0-flash"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        return self._post_openai_compat(httpx, url, headers, payload, provider_name="Gemini")

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
        return self._post_openai_compat(httpx, url, headers, payload, provider_name="OpenRouter")

    def _post_openai_compat(
        self,
        httpx: Any,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        provider_name: str,
        retries: int = 3,
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 429:
                        wait_sec = 2.0 ** (attempt + 1)
                        LOGGER.warning("%s rate-limited (429). Retrying in %.1fs...", provider_name, wait_sec)
                        time.sleep(wait_sec)
                        continue
                    resp.raise_for_status()
                    data = resp.json()

                choices = data.get("choices", []) if isinstance(data, dict) else []
                if not choices:
                    raise ValueError(f"{provider_name} returned no choices")
                message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                content = message.get("content", "") if isinstance(message, dict) else ""
                if not isinstance(content, str) or not content.strip():
                    raise ValueError(f"{provider_name} returned empty content")
                return content
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(1.5)

        raise last_exc or RuntimeError(f"{provider_name} completion failed")
