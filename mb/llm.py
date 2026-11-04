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


def _llm_retries() -> int:
    from mb.limits import limits
    return int(limits()["llm"]["max_retries"])


def load_prompt(version: str) -> str:
    path = PROMPTS / f"{version}.md"
    if not path.exists():               # older versions live in prompts/archive
        path = PROMPTS / "archive" / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{version}' not found at {path}")
    return path.read_text(encoding="utf-8")


class LLM:
    def __init__(self, budget: Budget, models: dict | None = None):
        self.budget = budget
        self.models = models or {}   # models.yaml: which models accept a temperature
        self.client = instructor.from_litellm(litellm.acompletion)                       # forced tool call
        self.json_client = instructor.from_litellm(litellm.acompletion, mode=instructor.Mode.MD_JSON)

    async def call(self, *, model: str, system: str, user: str, response_model: type[T], run_id: str,
                   agent_id: str, temperature: float = 0.0, timeout_s: float = 30.0) -> LLMResult[T]:
        self.budget.check(EST_CALL_USD)
        start = time.perf_counter()
        spec = self.models.get(model)
        extra = {"temperature": temperature} if spec is None or spec.temperature else {}
        extra |= dict(spec.params) if spec is not None else {}
        client = self.json_client if spec is not None and spec.structured == "json" else self.client
        out, raw = await client.chat.completions.create_with_completion(
            model=model, response_model=response_model, **extra,
            timeout=timeout_s, max_retries=_llm_retries(),
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

class _Ok(BaseModel):
    ok: bool


async def preflight(llm: LLM, models: set[str], run_id: str = "preflight") -> dict[str, str]:
    """One tiny call per model before a run. Returns {model: problem} for any that can't answer,
    so a run with a broken model (no key, no quota, an unsupported setting) stops in seconds
    instead of quietly degrading every brief and producing numbers that mean nothing."""
    problems = {}
    for m in sorted(models):
        try:
            await llm.call(model=m, system="Reply with ok=true.", user="ok?", response_model=_Ok,
                           run_id=run_id, agent_id="preflight", timeout_s=20)
        except Exception as e:
            problems[m] = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
    return problems
