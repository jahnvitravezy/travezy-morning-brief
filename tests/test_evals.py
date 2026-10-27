"""Runner and blinded pack, on stub agents: no network, no spend."""
import json

import pytest
from openpyxl import load_workbook

import mb.evals.blinded_pack as pack
import mb.evals.runner as runner
from mb.agents.composer import safe_version
from mb.config import load_config
from mb.context import build_context
from mb.evals.testset import REQUIRED_COVERAGE, load_testset
from mb.pipeline import BriefRun
from mb.sources.itinerary import FileLoader


class FakeServices:
    def __init__(self):
        self.cfg, self.loader = load_config(), FileLoader()
        self.llm = self.meteo = self.places = self.routes = None
        self.providers = []


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CONTEXTS", tmp_path / "contexts")
    monkeypatch.setattr(runner, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(pack, "CONTEXTS", tmp_path / "contexts")
    monkeypatch.setattr(pack, "RUNS", tmp_path / "runs")
    built = []

    async def fake_context(trip, index, meteo, weather, **kw):
        built.append((trip.trip_id, index))
        return build_context(trip, index)

    async def fake_run(ctx, cfg, llm, run_id, budget_s=90):
        return BriefRun(brief=safe_version(ctx, run_id), events=["stub"])

    monkeypatch.setattr(runner, "build_full_context", fake_context)
    monkeypatch.setattr(runner, "run_brief", fake_run)
    return tmp_path, built


async def test_runner_stores_briefs_records_and_reuses_contexts(run_dir):
    tmp, built = run_dir
    ts = load_testset()
    recs = await runner.run_testset("t1", ts, FakeServices())
    assert len(recs) == len(ts.days) == len(built)
    lines = (tmp / "runs" / "t1" / "records.jsonl").read_text().splitlines()
    assert len(lines) == len(ts.days) and json.loads(lines[0])["ctx_id"]
    assert len(list((tmp / "runs" / "t1" / "briefs").glob("*.json"))) == len(ts.days)
    await runner.run_testset("t2", ts, FakeServices())
    assert len(built) == len(ts.days)                   # second run: contexts loaded, not rebuilt
    assert runner.summary(recs)["days"] == len(ts.days)


async def test_blinded_pack_hides_scores_and_keeps_coverage(run_dir):
    tmp, _ = run_dir
    await runner.run_testset("t1", load_testset(), FakeServices())
    pack.main("t1")
    out = tmp / "runs" / "t1"
    page = (out / "pack" / "briefs.html").read_text()
    assert page.count("class=card") == 20
    for hidden in ("openai", "gpt", "badge", "confidence", "td."):
        assert hidden not in page.lower()
    key = json.loads((out / "pack_key.json").read_text())["codes"]
    assert list(key) == [f"B{i:02d}" for i in range(1, 21)]
    days = {r["day_id"]: r for r in map(json.loads, (out / "records.jsonl").read_text().splitlines())}
    assert REQUIRED_COVERAGE <= {c for v in key.values() for c in days[v["day_id"]]["covers"]}
    ws = load_workbook(out / "pack" / "scoring_sheet.xlsx")["Scores"]
    assert [c.value for c in ws[1]][:6] == ["code", "accuracy", "relevance", "feasibility", "format", "tone"]
    assert ws.max_row == 21


def test_choose_is_repeatable():
    recs = [{"day_id": d.id, "covers": d.covers} for d in load_testset().days]
    assert pack.choose(recs) == pack.choose(recs)


async def test_judge_run_scores_stored_briefs_repeatedly(run_dir, monkeypatch):
    import mb.evals.judge_run as jr
    from mb.contracts import Verdict
    tmp, _ = run_dir
    monkeypatch.setattr(jr, "CONTEXTS", tmp / "contexts")
    monkeypatch.setattr(jr, "RUNS", tmp / "runs")
    await runner.run_testset("t1", load_testset(), FakeServices())

    async def fake_judge(ctx, brief, rubric, spec, llm, run_id, attempts_left):
        return Verdict(agent_id=spec.id, rubric_id=rubric.id, model=spec.model, decision="pass", score=80,
                       badge="amber"), None

    monkeypatch.setattr(jr, "judge_brief", fake_judge)
    s = FakeServices()
    s.budget = type("B", (), {"status": lambda self: {}})()
    recs = await jr.judge_run("t1", s, model="anthropic/claude-haiku-5-5", repeat=2, check=False)
    assert len(recs) == 2 * len(load_testset().days)
    assert (tmp / "runs" / "t1" / "judged" / "anthropic__claude-haiku-5-5.jsonl").exists()
    assert {r["judge_model"] for r in recs} == {"anthropic/claude-haiku-5-5"}


def test_one_role_can_be_swapped_and_unknown_ones_are_rejected():
    from mb.config import load_config
    cfg = runner.with_models(load_config(), {"summariser": "anthropic/claude-sonnet-5-5"})
    assert cfg.agents["mb.summariser.v1"].model == "anthropic/claude-sonnet-5-5"
    assert cfg.agents["mb.composer.v1"].model == "openai/gpt-4o"
    j = runner.with_models(load_config(), {"judge": "gemini/gemini-3.5-flash-lite"})
    assert j.rubrics["rubric.morning_brief.v1"].judge_model == "gemini/gemini-3.5-flash-lite"
    with pytest.raises(ValueError):
        runner.with_models(load_config(), {"writer": "openai/gpt-4o"})
    with pytest.raises(ValueError):
        runner.with_models(load_config(), {"summariser": "openai/gpt-5-unlisted"})


async def test_scorecard_reads_runs_and_judges(run_dir, monkeypatch):
    import mb.evals.scorecard as sc
    tmp, _ = run_dir
    await runner.run_testset("t1", load_testset(), FakeServices())
    recs = await runner.run_testset("t1", load_testset(), FakeServices())
    (tmp / "runs" / "t1" / "summary.json").write_text(json.dumps(runner.summary(recs) | {"models": {}}))
    j = tmp / "runs" / "t1" / "judged"
    j.mkdir()
    (j / "x.jsonl").write_text("\n".join(json.dumps({"day_id": d, "repeat": k, "judge_model": "m", "score": s,
                                                      "badge": "green", "cost_usd": 0.001})
                                         for d, k, s in [("td.01", 1, 90), ("td.01", 2, 80), ("td.02", 1, 70),
                                                         ("td.02", 2, 70)]))
    assert sc.agent_rows(tmp / "runs")[0]["run"] == "t1"
    jr = sc.judge_rows(tmp / "runs")[0]
    assert jr["avg_spread"] == 5 and jr["max_spread"] == 10 and jr["same_badge"] == "100%"


async def test_preflight_reports_models_that_cannot_answer():
    from mb.llm import preflight

    class Half:
        async def call(self, *, model, **kw):
            if model == "bad":
                raise RuntimeError("quota exceeded\nlong details")
    assert await preflight(Half(), {"good", "bad"}) == {"bad": "RuntimeError: quota exceeded"}


def test_models_that_reject_temperature_are_marked():
    from mb.config import load_config
    m = load_config().models
    assert m["anthropic/claude-sonnet-5-5"].temperature is False and m["openai/gpt-4o"].temperature is True


async def test_llm_leaves_out_temperature_for_models_that_reject_it():
    from types import SimpleNamespace
    from mb.config import load_config
    from mb.llm import LLM, _Ok
    seen = {}

    class Budget:
        def check(self, est): pass
        def record(self, **kw): pass

    async def fake_create(**kw):
        seen[kw["model"]] = "temperature" in kw
        raw = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        return _Ok(ok=True), raw

    llm = LLM(Budget(), load_config().models)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create_with_completion=fake_create)))
    for m in ("anthropic/claude-haiku-5-5", "openai/gpt-4o"):
        await llm.call(model=m, system="s", user="u", response_model=_Ok, run_id="t", agent_id="a")
    assert seen == {"anthropic/claude-haiku-5-5": False, "openai/gpt-4o": True}


async def test_sonnet_uses_the_json_client_not_forced_tools():
    from types import SimpleNamespace
    from mb.config import load_config
    from mb.llm import LLM, _Ok
    used = []

    class Budget:
        def check(self, est): pass
        def record(self, **kw): pass

    def client(name):
        async def create(**kw):
            used.append(name)
            return _Ok(ok=True), SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create_with_completion=create)))

    llm = LLM(Budget(), load_config().models)
    llm.client, llm.json_client = client("tools"), client("json")
    for m in ("anthropic/claude-sonnet-5-5", "anthropic/claude-haiku-5-5"):
        await llm.call(model=m, system="s", user="u", response_model=_Ok, run_id="t", agent_id="a")
    assert used == ["json", "tools"]
