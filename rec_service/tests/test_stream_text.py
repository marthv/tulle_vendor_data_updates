"""Streamed answer text (2026-10-31): the final model turn's `message` is published on /rec/progress while it is
being written. partial_message decodes a half-written JSON string; _drain_stream reads one turn's events."""
import types

import agent
import progress


def test_partial_message_decodes_as_far_as_written():
    assert agent.partial_message('{"mess') == ""
    assert agent.partial_message('{"message": "') == ""
    assert agent.partial_message('{"message": "Pecan Springs leads') == "Pecan Springs leads"
    # escapes: complete ones decode, a cut one waits
    assert agent.partial_message('{"message": "Line one\\nLine two \\"quoted\\" \\') == 'Line one\nLine two "quoted" '
    assert agent.partial_message('{"message": "Caf\\u00e9 at \\u00') == "Café at "
    # the string ends at the first unescaped quote; later keys are never shown
    buf = '{"message": "Done.", "vendor_ids": ["V1"], "reasons": ["secret reason"]}'
    assert agent.partial_message(buf) == "Done."
    # key order: message need not be first
    assert agent.partial_message('{"chips": ["a"], "message": "Hi') == "Hi"


def _ev(**kw):
    return types.SimpleNamespace(**kw)


class _Stream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


class _Prog:
    def __init__(self):
        self.texts = []

    def text(self, t):
        self.texts.append(t)

    def set(self, label):
        pass


def test_drain_stream_publishes_message_text_and_returns_final():
    final = object()
    events = [
        _ev(type="message_start"),
        _ev(type="content_block_start", content_block=_ev(type="text")),
        _ev(type="content_block_delta", delta=_ev(type="text_delta", text="Thinking out loud")),
        _ev(type="content_block_stop"),
        _ev(type="content_block_start", content_block=_ev(type="tool_use", name="search_venues")),
        _ev(type="content_block_delta", delta=_ev(type="input_json_delta", partial_json='{"states": ["Texas"], "message": "not this"}')),
        _ev(type="content_block_stop"),
        _ev(type="content_block_start", content_block=_ev(type="tool_use", name="present_recommendations")),
        _ev(type="content_block_delta", delta=_ev(type="input_json_delta", partial_json='{"message": "Pecan ')),
        _ev(type="content_block_delta", delta=_ev(type="input_json_delta", partial_json='Springs leads')),
        _ev(type="content_block_delta", delta=_ev(type="input_json_delta", partial_json='.", "vendor_ids": ["V1"]}')),
        _ev(type="message_stop"),
    ]
    prog = _Prog()
    assert agent._drain_stream(_Stream(events, final), prog) is final
    # plain text streams; another tool's "message" key is NOT published; the final answer streams in order
    assert prog.texts == ["Thinking out loud", "Pecan ", "Pecan Springs leads", "Pecan Springs leads."]


def test_progress_text_is_served_while_running_and_cleared_on_bounce():
    progress.reset()
    tr = progress.start("req-1", 7)
    assert progress.get("req-1", 7)["text"] == ""
    tr.text("Pecan Springs leads")
    assert progress.get("req-1", 7)["text"] == "Pecan Springs leads"
    tr.text("")   # a bounced present_recommendations withdraws the draft
    assert progress.get("req-1", 7)["text"] == ""
    tr.text("x" * 10000)
    assert len(progress.get("req-1", 7)["text"]) == progress.TEXT_MAX
    tr.finish("done")
    assert progress.get("req-1", 7)["text"] == ""   # the finished answer comes from the response itself
    assert progress.get("nope", 7) == progress.UNKNOWN
    progress.NULL.text("ignored")


def test_run_streams_only_with_tracker_and_plan(monkeypatch):
    """stream=True is passed to the tool runner only when REC_STREAM_TEXT is on, a tracker exists and the couple
    has a plan; the events are drained and the finished message is used."""
    import config
    import xano
    monkeypatch.setattr(config, "STREAM_TEXT", True)
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: [])
    monkeypatch.setattr(xano, "market_benchmarks", lambda *a, **k: {})
    monkeypatch.setattr(xano, "cached_market_benchmarks", lambda *a, **k: {})
    captured = {}

    class Msg:
        def __init__(self):
            self.content, self.model, self.stop_reason = [], "fake", "end_turn"
            self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)

    class Runner:
        def __init__(self, tools, **kw):
            captured.update(kw)
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}

        def __iter__(self):
            present = self.tools["present_recommendations"]
            (present.call if hasattr(present, "call") else present)(
                **{"message": "Plain answer.", "vendor_ids": [], "reasons": [], "chips": ["More"]})
            if captured.get("stream"):
                yield _Stream([_ev(type="content_block_start", content_block=_ev(type="tool_use", name="present_recommendations")),
                               _ev(type="content_block_delta", delta=_ev(type="input_json_delta", partial_json='{"message": "Plain answer."'))],
                              Msg())
            else:
                yield Msg()

        def generate_tool_call_response(self):
            return None

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    monkeypatch.setattr(agent.anthropic, "Anthropic", Client, raising=False)
    msgs = [{"role": "user", "content": "venues?"}]
    user = {"id": 1, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 100}

    progress.reset()
    tr = progress.start("r-stream", 1)
    res, _ = agent.run("tok", user, True, msgs, detail="full", progress=tr)
    assert captured.get("stream") is True and res["text"] == "Plain answer."
    captured.clear()
    res, _ = agent.run("tok", user, True, msgs, detail="full", progress=None)      # no tracker: not streamed
    assert "stream" not in captured and res["text"] == "Plain answer."
    captured.clear()
    tr2 = progress.start("r-free", 1)
    res, _ = agent.run("tok", user, False, msgs, detail="free", progress=tr2)     # no plan: not streamed
    assert "stream" not in captured
