"""Tulle experiment engine (spec: experiments/FRAMEWORK.md).

Framework-free and import-safe: no network or filesystem access at import time.

Modules:
  stats     Bayesian maths (section 4)
  spec      registry normalisation + the seven-question validation (sections 1-4)
  health    one health state + recommended action per live experiment (section 6)
  snapshot  the JSON the dash and alerts read (sections 5, 7)
  history   daily readout JSONL + alert de-dup on the volume (sections 5.7, 9)
"""
from . import health, history, snapshot, spec, stats  # noqa: F401

__all__ = ["health", "history", "snapshot", "spec", "stats"]
