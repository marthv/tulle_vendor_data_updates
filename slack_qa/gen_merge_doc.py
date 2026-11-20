"""Regenerate the generated block of XANO_MERGE_PROPOSAL.md from dedupe.py, so the proposal the owner approves always
matches the code. Run: cd slack_qa && python gen_merge_doc.py   (tests/test_v2_owner.py fails if they drift)."""
from pathlib import Path

import dedupe

START, END = "<!-- generated:start (python gen_merge_doc.py) -->", "<!-- generated:end -->"


def render(text: str) -> str:
    a, b = text.index(START), text.index(END)
    return text[: a + len(START)] + "\n" + dedupe.merge_doc_tables() + "\n" + text[b:]


if __name__ == "__main__":
    p = Path(__file__).with_name("XANO_MERGE_PROPOSAL.md")
    p.write_text(render(p.read_text(encoding="utf-8")), encoding="utf-8")
    print("updated", p)
