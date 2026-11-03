from mb.config import load_config
from mb.limits import api, api_client, limits


def test_every_agent_and_api_has_a_timeout_and_retries_in_one_file():
    lim = limits()
    cfg = load_config()
    assert set(lim["agents"]) == set(cfg.agents)
    for a in cfg.agents.values():                       # agents.yaml no longer sets timeouts
        assert (a.timeout_s, a.retries) == (lim["agents"][a.id]["timeout_s"], lim["agents"][a.id]["retries"])
    for name in ("weather", "places", "routes", "web_search", "fx"):
        assert {"timeout_s", "retries"} <= set(api(name))
    assert lim["brief_budget_s"] > 0 and lim["llm"]["max_retries"] >= 0


def test_api_client_uses_the_configured_timeout():
    assert api_client("places").timeout.read == api("places")["timeout_s"]
