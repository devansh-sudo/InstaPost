"""Thin wrapper around the Claude API: structured output, budget checks, cost logging."""

from functools import lru_cache

import anthropic
from pydantic import BaseModel

from instapost import budget
from instapost.config import get_settings, require

# Models with server-side refusal fallback. Haiku has none, so it uses the plain endpoint.
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5"}


class RefusalError(RuntimeError):
    pass


@lru_cache
def _client() -> anthropic.Anthropic:
    require("anthropic_api_key")
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def structured[T: BaseModel](
    *,
    model: str,
    system: str,
    content: str | list,
    schema: type[T],
    purpose: str,
    effort: str = "medium",
    max_tokens: int = 8000,
    estimated_usd: float = 0.05,
) -> T:
    """Run one request whose answer must match `schema`. Raises on refusal or budget cap."""
    budget.ensure_room(estimated_usd)
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        # The system prompt is stable per purpose, so cache it.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort},
        output_format=schema,
    )
    if model in _FALLBACK_MODELS:
        response = _client().beta.messages.parse(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    else:
        response = _client().messages.parse(**kwargs)

    budget.record(purpose, response.model, budget.claude_cost(model, response.usage),
                  request_id=response._request_id)
    if response.stop_reason == "refusal":
        raise RefusalError(f"{purpose}: model declined the request")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"{purpose}: incomplete structured output ({response.stop_reason})")
    return response.parsed_output
