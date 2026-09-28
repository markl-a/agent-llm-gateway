"""Provider adapters behind one interface. Upgrading an SDK only touches its adapter."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ProviderError(Exception):
    pass


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


class MockProvider:
    """Deterministic provider for tests and local dev. `fail` makes it raise, to exercise fallback."""

    def __init__(self, name: str, fail: bool = False):
        self.name, self.fail = name, fail

    def complete(self, model: str, messages: list[dict], max_tokens: int) -> Completion:
        if self.fail:
            raise ProviderError(f"{self.name} unavailable")
        prompt = " ".join(m["content"] for m in messages)
        text = f"[{model}] ok"
        return Completion(text, max(1, len(prompt) // 4), min(max_tokens, len(text) // 4 + 1))


class AnthropicProvider:
    name = "anthropic"

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic()

    def complete(self, model, messages, max_tokens):
        try:
            system = "\n".join(m["content"] for m in messages if m["role"] == "system")
            msgs = [m for m in messages if m["role"] != "system"]
            r = self.client.messages.create(model=model, max_tokens=max_tokens, system=system or None, messages=msgs)
        except Exception as e:  # network, 5xx, overload
            raise ProviderError(str(e)) from e
        return Completion(r.content[0].text, r.usage.input_tokens, r.usage.output_tokens)


class OpenAICompatProvider:
    """Any OpenAI-compatible endpoint: OpenAI, Azure OpenAI, vLLM, Ollama."""
    name = "openai_compat"

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL"))

    def complete(self, model, messages, max_tokens):
        try:
            r = self.client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
        except Exception as e:
            raise ProviderError(str(e)) from e
        return Completion(r.choices[0].message.content, r.usage.prompt_tokens, r.usage.completion_tokens)
