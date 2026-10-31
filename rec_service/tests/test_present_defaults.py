"""present_recommendations must accept a call without reasons / chips (2026-10-31 live log: the model left `chips` out,
pydantic rejected the call, the runner fed the error back and the couple paid for one more model turn)."""
import types

import agent
import progress


def test_present_recommendations_tolerates_missing_reasons_and_chips(monkeypatch):
    import xano
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: [])
    monkeypatch.setattr(xano, "market_benchmarks", lambda *a, **k: {})
    monkeypatch.setattr(xano, "cached_market_benchmarks", lambda *a, **k: {})

    class Msg:
        def __init__(self):
            self.content, self.model, self.stop_reason = [], "fake", "end_turn"
            self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)

    class Runner:
        def __init__(self, tools, **kw):
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}

        def __iter__(self):
            present = self.tools["present_recommendations"]
            fn = present.call if hasattr(present, "call") else present
            fn(**{"message": "Two picks for you. Want me to keep to Austin?", "vendor_ids": []})   # no reasons, no chips
            yield Msg()

        def generate_tool_call_response(self):
            return None

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    monkeypatch.setattr(agent.anthropic, "Anthropic", Client, raising=False)
    progress.reset()
    res, _ = agent.run("tok", {"id": 1, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 100}, True,
                       [{"role": "user", "content": "venues?"}], detail="full", progress=progress.start("r-nochips", 1))
    assert res["text"].startswith("Two picks") and res["chips"] == [] or isinstance(res["chips"], list)
