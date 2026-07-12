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
    if config.AI_PROVIDER == "hermes_ssh":
        return _hermes_ssh(system_prompt, user_content)
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


def _shell_quote(value: str) -> str:
    """POSIX single-quote a value for the remote shell command."""
    return "'" + value.replace("'", "'\\''") + "'"


def _extract_json(text: str) -> str:
    """hermes chat is a general agent, not a raw completion API -- despite
    instructions it may wrap the answer in extra text. Pull out the first
    top-level {...} block rather than assuming stdout is pure JSON."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in hermes response: {text[:200]!r}")
    return text[start:end + 1]


def _hermes_ssh(system_prompt: str, user_content: str) -> list[AssetDecision]:
    """Routes the decision through a Hermes Agent instance over SSH, the same
    pattern this homelab's AgentOS project already uses in production:
    `hermes chat -q "<prompt>" -Q --yolo`. -Q prints only the answer to
    stdout; --yolo auto-approves any tool call so the headless call can't
    hang waiting for an interactive approval that will never come."""
    import subprocess

    schema_hint = (
        '\nRespond with JSON only, no other text, shaped as: {"decisions": [{"symbol": str, '
        '"action": "buy"|"sell"|"hold", "confidence": 0-100, "reason": str}]}'
    )
    prompt = f"{system_prompt}\n{schema_hint}\n\n{user_content}"
    remote_cmd = (
        f"cd {config.HERMES_WORKDIR} && {config.HERMES_BIN_PATH} chat "
        f"-q {_shell_quote(prompt)} -Q --source papertrader --yolo"
    )
    result = subprocess.run(
        [
            "ssh", "-i", config.HERMES_SSH_KEY_PATH,
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
            "-p", str(config.HERMES_SSH_PORT),
            f"{config.HERMES_SSH_USER}@{config.HERMES_SSH_HOST}",
            remote_cmd,
        ],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ssh/hermes exited {result.returncode}")
    return CycleDecisions.model_validate(json.loads(_extract_json(result.stdout))).decisions


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
