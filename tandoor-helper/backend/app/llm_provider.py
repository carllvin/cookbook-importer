from __future__ import annotations

from typing import Optional

from .config import settings
from .schemas import TokenUsage

_PROVIDER_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _active_provider() -> str:
    provider = (settings.ai_provider or "anthropic").strip().lower()
    return provider if provider in _PROVIDER_KEY_VARS else "anthropic"


def is_configured() -> bool:
    """Checks whether an API key is set for the currently selected provider."""
    provider = _active_provider()
    if provider == "openai":
        return bool(settings.openai_api_key)
    if provider == "gemini":
        return bool(settings.gemini_api_key)
    return bool(settings.anthropic_api_key)


def missing_key_hint() -> str:
    provider = _active_provider()
    var = _PROVIDER_KEY_VARS[provider]
    return (
        f"{var} is not set, but AI_PROVIDER={provider} is selected. "
        f"Please configure it in the .env file."
    )


def complete_text(system_prompt: Optional[str], user_content: str, max_tokens: int = 4096) -> tuple[str, TokenUsage]:
    """Sends a prompt to the configured AI provider and returns (response_text, token_usage).
    system_prompt may be empty/None (in which case no system prompt is sent)."""
    provider = _active_provider()
    if provider == "openai":
        return _complete_openai(system_prompt, user_content, max_tokens)
    if provider == "gemini":
        return _complete_gemini(system_prompt, user_content, max_tokens)
    return _complete_anthropic(system_prompt, user_content, max_tokens)


def _complete_anthropic(system_prompt: Optional[str], user_content: str, max_tokens: int) -> tuple[str, TokenUsage]:
    import anthropic

    if not settings.anthropic_api_key:
        raise RuntimeError(missing_key_hint())

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    kwargs = {}
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": user_content}],
        **kwargs,
    )
    text = "".join(block.text for block in response.content if block.type == "text")

    usage = TokenUsage()
    if getattr(response, "usage", None) is not None:
        usage.input_tokens = getattr(response.usage, "input_tokens", 0) or 0
        usage.output_tokens = getattr(response.usage, "output_tokens", 0) or 0
    return text, usage


def _complete_openai(system_prompt: Optional[str], user_content: str, max_tokens: int) -> tuple[str, TokenUsage]:
    from openai import OpenAI

    if not settings.openai_api_key:
        raise RuntimeError(missing_key_hint())

    client = OpenAI(api_key=settings.openai_api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_content})

    # Newer OpenAI models (gpt-5/o-series) require 'max_completion_tokens' instead of
    # 'max_tokens'. We try both so the app works regardless of the chosen model,
    # without the user having to figure that out themselves.
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            max_completion_tokens=max_tokens,
            messages=messages,
        )
    except Exception:
        response = client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=max_tokens,
            messages=messages,
        )
    text = response.choices[0].message.content or ""

    usage = TokenUsage()
    if getattr(response, "usage", None) is not None:
        usage.input_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
        usage.output_tokens = getattr(response.usage, "completion_tokens", 0) or 0
    return text, usage


def _complete_gemini(system_prompt: Optional[str], user_content: str, max_tokens: int) -> tuple[str, TokenUsage]:
    from google import genai
    from google.genai import types

    if not settings.gemini_api_key:
        raise RuntimeError(missing_key_hint())

    client = genai.Client(api_key=settings.gemini_api_key)
    config_kwargs = {"max_output_tokens": max_tokens}
    if system_prompt:
        config_kwargs["system_instruction"] = system_prompt

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=user_content,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = response.text or ""

    usage = TokenUsage()
    meta = getattr(response, "usage_metadata", None)
    if meta is not None:
        usage.input_tokens = getattr(meta, "prompt_token_count", 0) or 0
        usage.output_tokens = getattr(meta, "candidates_token_count", 0) or 0
    return text, usage
