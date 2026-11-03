import shutil

import pytest
import yaml

from mb.config import CONFIG_DIR, load_config


@pytest.fixture
def cfg_dir(tmp_path):
    d = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, d)
    return d


def _edit(path, fn):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_real_config_loads():
    cfg = load_config()
    assert len(cfg.agents) == 4
    assert "rubric.morning_brief.v1" in cfg.rubrics


def test_model_typo_is_rejected(cfg_dir):
    _edit(cfg_dir / "agents.yaml", lambda a: a[0].update(model="openai/gpt-4oo"))
    with pytest.raises(ValueError, match="not in models.yaml"):
        load_config(cfg_dir)


def test_unknown_input_is_rejected(cfg_dir):
    _edit(cfg_dir / "agents.yaml", lambda a: a[0]["inputs"].append("src.vibes"))
    with pytest.raises(ValueError, match="not in sources.yaml"):
        load_config(cfg_dir)


def test_weights_must_sum_to_one(cfg_dir):
    _edit(cfg_dir / "rubrics" / "morning_brief.v1.yaml", lambda r: r["criteria"][0].update(weight=0.5))
    with pytest.raises(ValueError, match="sum to"):
        load_config(cfg_dir)


def test_unknown_key_is_rejected(cfg_dir):
    _edit(cfg_dir / "app.yaml", lambda a: a.update(budjet_inr=1))
    with pytest.raises(ValueError):
        load_config(cfg_dir)

def test_litellm_uses_its_local_price_list():
    """No network call for prices at startup; and every model we use has a price in it."""
    import json
    import os
    from pathlib import Path

    import litellm
    import mb  # noqa: F401  (sets the variable)
    from mb.config import load_config
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"].lower() == "true"
    backup = json.loads((Path(litellm.__file__).parent / "model_prices_and_context_window_backup.json").read_text())
    for name in load_config().models:
        key = name.split("/", 1)[1] if name.startswith(("openai/", "anthropic/")) else name
        assert key in backup, f"{name} has no price in LiteLLM's local list"


def test_models_nearing_end_of_life_are_flagged():
    import datetime as dt
    from mb.config import eol_warnings, load_config
    cfg = load_config()
    assert cfg.models["openai/gpt-4o"].end_of_life == dt.date(2027, 1, 1)
    soon = eol_warnings(cfg, today=dt.date(2026, 11, 3))
    assert any(w.startswith("openai/gpt-4o ") for w in soon)
    assert eol_warnings(cfg, today=dt.date(2026, 6, 1)) == []            # more than 90 days away
