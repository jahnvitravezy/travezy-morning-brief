import os

# Tests never reach the network: exchange rates come from config/fx.yaml.
os.environ.setdefault("MB_FX", "fixed")

# The API refuses requests without a key; tests use this one.
os.environ.setdefault("MB_API_KEY", "test-key")
