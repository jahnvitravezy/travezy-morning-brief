"""Travezy Morning Brief.

LiteLLM downloads its model price list from GitHub when it first loads. On a slow or blocked
connection that took about 50 seconds (three timed-out attempts) and pushed a brief to the 90 s
budget (clean-clone test, 30 Oct). The price list shipped inside LiteLLM covers every model we use,
so use it and never wait on that download. Set here, before anything imports litellm.
"""
import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
