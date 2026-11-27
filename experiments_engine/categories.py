"""Experiment categories (owner 2026-11-27): one per registry entry, used by the Experiments tab's category filter.

This tuple is the only list. validate.py requires every entry's ``category`` to be one of these, the dash hands the
list to the page (experiments_ui/render.py), and the page builds its filter chips from it. ``category`` sits beside,
and never replaces, the older ``themes`` / ``bucket`` fields.

To add a category: add it here, assign it in experiments/experiment_log.json, run experiments/validate.py --all.
"""
from __future__ import annotations

CATEGORIES = (
    "Planner",
    "Onboarding",
    "Pricing & payments",
    "Paywall & PDF limit",
    "Price Report & PI card",
    "Search & Discovery",
    "Email & feedback",
    "Data & extraction",
    "Growth & ads",
    "Other",
)


def problem(entry):
    """None when the entry's category is valid, else a one-line reason."""
    c = (entry or {}).get("category") if isinstance(entry, dict) else None
    if c is None or c == "":
        return "No category: set category to one of " + ", ".join(CATEGORIES)
    if c not in CATEGORIES:
        return f"Unknown category {c!r}: use one of " + ", ".join(CATEGORIES)
    return None
