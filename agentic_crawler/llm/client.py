"""
LLM クライアント — Ollama（ローカル）と Portkey（会社）を一つのインターフェースで切り替える。

切り替え方法: config.py の LLM_BACKEND を変えるだけ
  LLM_BACKEND = "ollama"    # ← 自宅・ローカル qwen3:14b
  LLM_BACKEND = "portkey"   # ← 会社 Portkey（Gemini / Claude）

使用例:
    from llm.client import llm_call, llm_call_json, llm_call_structured
"""

import time
import json
import random
import logging
from typing import Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from config import (
    LLM_BACKEND,
    # Ollama
    OLLAMA_BASE_URL,
    # Portkey
    PORTKEY_API_KEY,
    PORTKEY_PRIMARY_MODEL,
    PORTKEY_EXTRACT_MODEL,
    OLLAMA_PRIMARY_MODEL,
    # OpenAI 互換
    OPENAI_COMPAT_BASE_URL,
    OPENAI_COMPAT_API_KEY,
    OPENAI_COMPAT_PRIMARY_MODEL,
    OPENAI_COMPAT_EXTRACT_MODEL,
    # 共通
    LLM_SLEEP,
    MAX_RETRY,
    RETRY_BACKOFF,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


# ── クライアント初期化 ───────────────────────────────────────────────

def _make_ollama_client() -> OpenAI:
    """ローカル Ollama（OpenAI 互換 API）"""
    return OpenAI(
        base_url=OLLAMA_BASE_URL,
        api_key="ollama",   # Ollama は認証不要、ダミー値
    )


def _make_portkey_client():
    """会社 Portkey ゲートウェイ"""
    from portkey_ai import Portkey
    return Portkey(
        api_key=PORTKEY_API_KEY,
    )


def _make_openai_compat_client() -> OpenAI:
    """OpenAI 互換 API（JV Vortex / 任意のゲートウェイ）"""
    return OpenAI(
        base_url=OPENAI_COMPAT_BASE_URL,
        api_key=OPENAI_COMPAT_API_KEY,
    )


def _get_client_and_model(model_role: str = "primary"):
    """
    バックエンドに応じてクライアントとモデル ID を返す。
    model_role: "primary" | "extract"
    """
    if LLM_BACKEND == "ollama":
        return _make_ollama_client(), OLLAMA_PRIMARY_MODEL

    if LLM_BACKEND == "openai_compat":
        if model_role == "extract":
            return _make_openai_compat_client(), OPENAI_COMPAT_EXTRACT_MODEL
        return _make_openai_compat_client(), OPENAI_COMPAT_PRIMARY_MODEL

    # portkey
    if model_role == "extract":
        return _make_portkey_client(), PORTKEY_EXTRACT_MODEL
    return _make_portkey_client(), PORTKEY_PRIMARY_MODEL


# ── 共通呼び出し ────────────────────────────────────────────────────

def llm_call(
    prompt: str,
    system: str = "You are a helpful assistant specialized in Japanese university admissions.",
    model_role: str = "primary",
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> str:
    """テキスト生成。失敗時はリトライ。"""
    client, model_id = _get_client_and_model(model_role)

        # Qwen3 の thinking モードを無効化（/no_think をシステムプロンプトに付加）
    if LLM_BACKEND == "ollama":
        system = "/no_think\n" + system

    # 部分模型参数兼容性处理：
    #   - Claude opus-4 / o1 / o3 / o4 系列：不支持 temperature
    #   - GPT-5 / o1 / o3 / o4 系列（Azure OpenAI）：用 max_completion_tokens 而非 max_tokens
    #   - Claude Sonnet / Haiku 等：支持标准参数，无需特殊处理
    _NO_TEMPERATURE_MODELS = ["claude-opus-4", "claude-opus", "o1", "o3", "o4"]
    _MAX_COMPLETION_TOKENS_MODELS = ["gpt-5", "o1", "o3", "o4"]
    _NORMAL_MODELS = ["claude-sonnet", "claude-haiku", "claude-3"]
    is_normal = any(kw in model_id for kw in _NORMAL_MODELS)
    skip_temperature = (not is_normal) and any(kw in model_id for kw in _NO_TEMPERATURE_MODELS)
    use_max_completion_tokens = (not is_normal) and any(kw in model_id for kw in _MAX_COMPLETION_TOKENS_MODELS)

    for attempt in range(1, MAX_RETRY + 1):
        try:
            create_kwargs = {
                "model": model_id,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
            }
            if use_max_completion_tokens:
                create_kwargs["max_completion_tokens"] = max_tokens
            else:
                create_kwargs["max_tokens"] = max_tokens
            if not skip_temperature:
                create_kwargs["temperature"] = temperature

            response = client.chat.completions.create(**create_kwargs)
            time.sleep(LLM_SLEEP)
            return response.choices[0].message.content or ""
        except Exception as e:
            wait = RETRY_BACKOFF ** attempt + random.uniform(0, 1)
            logger.warning(f"LLM call failed (attempt {attempt}/{MAX_RETRY}): {e}. Retry in {wait:.1f}s")
            time.sleep(wait)

    logger.error("LLM call failed after all retries.")
    return ""


def llm_call_json(
    prompt: str,
    system: str = "You are a helpful assistant. Always respond with valid JSON only.",
    model_role: str = "primary",
    max_tokens: int = 4096,
) -> dict | list | None:
    """JSON を返す LLM 呼び出し。パース失敗時は None。"""
    raw = llm_call(prompt, system=system, model_role=model_role, max_tokens=max_tokens)
    raw = raw.strip()

    # <think>...</think> ブロックを除去（Qwen3 の思考モード対応）
    import re
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # コードブロックを除去
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        logger.warning(f"Failed to parse LLM JSON: {raw[:200]}")
        return None


def llm_call_structured(
    prompt: str,
    response_model: Type[T],
    system: str = "You are a helpful assistant. Always respond with valid JSON only.",
    model_role: str = "primary",
) -> T | None:
    """Pydantic モデルで構造化出力を得る。"""
    schema_hint = f"\nRespond ONLY with JSON matching this schema:\n{response_model.model_json_schema()}"
    raw = llm_call_json(prompt + schema_hint, system=system, model_role=model_role)
    if raw is None:
        return None
    try:
        return response_model.model_validate(raw)
    except Exception as e:
        logger.warning(f"Structured parse failed: {e}. Raw: {str(raw)[:200]}")
        return None

