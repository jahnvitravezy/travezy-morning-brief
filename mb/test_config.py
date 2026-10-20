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