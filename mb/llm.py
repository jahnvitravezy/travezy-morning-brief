"""One client for every model provider. Each call is budget-checked before it runs
and its real cost and tokens are written to the ledger after."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import instructor
import litellm
from decouple import config
from pydantic import BaseModel

from mb.budget import Budget

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
EST_CALL_USD = 0.05   # headroom checked before each call; the real cost is recorded after

# LiteLLM reads keys from the environment; .env is loaded here, once.
for _k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
    if not os.environ.get(_k) and (_v := config(_k, default="")):
        os.environ[_k] = _v

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult(Generic[T]):
    output: T
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


def load_prompt(version: str) -> str:
    path = PROMPTS / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{version}' not found at {path}")
    return path.read_text(encoding="utf-8")


class LLM:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.client = instructor.from_litellm(litellm.acompletion)

    async def call(self, *, model: str, system: str, user: str, response_model: type[T], run_id: str,
                   agent_id: str, temperature: float = 0.0, timeout_s: float = 30.0) -> LLMResult[T]:
        self.budget.check(EST_CALL_USD)
        start = time.perf_counter()
        out, raw = await self.client.chat.completions.create_with_completion(
            model=model, response_model=response_model, temperature=temperature,
            timeout=timeout_s, max_retries=1,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        latency = int((time.perf_counter() - start) * 1000)
        try:
            cost = float(litellm.completion_cost(completion_response=raw))
        except Exception:
            cost = EST_CALL_USD   # unknown price: count it conservatively, never as free
        u = raw.usage
        self.budget.record(run_id=run_id, kind="llm", item=f"{agent_id}:{model}", cost_usd=cost,
                           units={"in": u.prompt_tokens, "out": u.completion_tokens})
        return LLMResult(out, model, u.prompt_tokens, u.completion_tokens, cost, latency)