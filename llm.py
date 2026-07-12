"""LLM decision client. Default provider is Claude (Anthropic SDK) with
structured output; an OpenAI-compatible adapter covers DeepSeek/Groq/Ollama.

Swap providers in config.py — strategies never touch provider details.
"""

import json
import os
from typing import Literal

import requests
from dotenv import load_dotenv
from pydantic import BaseModel

import config

load_dotenv()


class AssetDecision(BaseModel):
    symbol: str
    action: Literal["buy", "sell", "hold"]
    confidence: int      # 0-100
    reason: str


class CycleDecisions(BaseModel):
    decisions: list[AssetDecision]


def get_decisions(system_prompt: str, user_content: str) -> list[AssetDecision]:
    """One model call → validated per-asset decisions."""
    if config.AI_PROVIDER == "claude":
        return _claude(system_prompt, user_content)
    return _openai_compatible(system_prompt, user_content)


def _claude(system_prompt: str, user_content: str) -> list[AssetDecision]:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.AI_MODEL,
        max_tokens=config.AI_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        output_format=CycleDecisions,
    )
    parsed = response.parsed_output
    return parsed.decisions if parsed else []


def _openai_compatible(system_prompt: str, user_content: str) -> list[AssetDecision]:
    key = os.getenv(config.OPENAI_COMPAT_KEY_ENV, "")
    schema_hint = (
        '\nRespond with JSON only, shaped as: {"decisions": [{"symbol": str, '
        '"action": "buy"|"sell"|"hold", "confidence": 0-100, "reason": str}]}'
    )
    resp = requests.post(
        f"{config.OPENAI_COMPAT_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": config.OPENAI_COMPAT_MODEL,
            "max_tokens": config.AI_MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt + schema_hint},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return CycleDecisions.model_validate(json.loads(text)).decisions
