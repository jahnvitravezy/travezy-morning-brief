import os

# Tests never reach the network: exchange rates come from config/fx.yaml.
os.environ.setdefault("MB_FX", "fixed")
