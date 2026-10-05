import os

# Set before any test module imports config (test_budget sorts before test_guards): free-tier rules are
# tested with the Forever-only beta gate off.
os.environ.setdefault("REC_FOREVER_ONLY", "0")
