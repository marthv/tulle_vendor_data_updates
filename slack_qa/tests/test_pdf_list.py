"""'@Tulle Bot PDFs for ...' (owner 2026-11-22): read-only list of a vendor's pricing PDFs from table 10."""
import pytest
import requests

import pdf_list as pl
from fakes import mention
from test_vendor_actions import ROSE, make_bot

DRIVE = "https://drive.google.com/file/d/1CmYk6h1AK8M2OkFNrP6ZGm9c90-MCPNW/view?usp=drivesdk"
PDFS = [
    {"PDF_ID": "P10", "Vendor_ID": "V5", "Current_Assignment": "Past Season PDF 1", "PDF_Link": DRIVE,
     "Show_PDF": True, "Year_of_Pricing": "2025", "extraction_status": "extracted", "confidentiality_flag": False},
    {"PDF_ID": "P11", "Vendor_ID": "V5", "Current_Assignment": "Current PDF", "PDF_Link": DRIVE + "2",
     "Show_PDF": False, "Year_of_Pricing": "2026", "extraction_status": None, "confidentiality_flag": True},
    {"PDF_ID": "P12", "Vendor_ID": "V6", "Current_Assignment": "Other venue", "PDF_Link": DRIVE + "3"},
]
GRAMERCY = [dict(ROSE), {"id": 6, "Vendor_ID": "V6", "Name": "Gramercy Mansion", "State": "MD"},
            {"id": 7, "Vendor_ID": "V7", "Name": "The Gramercy", "State": "NY"},
            {"id": 8, "Vendor_ID": "V8", "Name": "Gramercy at Lakeside Manor", "State": "NJ"}]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network write attempted in a test")
    monkeypatch.setattr(requests, "post", boom)


class TableXano:
    enabled = True

    def __init__(self, rows=PDFS):
        self.rows_, self.calls = rows, 0

    def rows(self, table_id):
        assert table_id == 10
        self.calls += 1
        return list(self.rows_), False


def bot_with_pdfs(rows=None, vendors=None):
    b, slack, store = make_bot(rows=vendors or GRAMERCY)
    b.xano = TableXano(rows if rows is not None else PDFS)
    return b, slack, store


@pytest.mark.parametrize("q,want", [
    ("give me all the pdfs for Gramercy Mansion", "Gramercy Mansion"),
    ("PDFs for V1234", "V1234"),
    ("what pdfs do we have for Rose Hall?", "Rose Hall"),
    ("can you list the pricing pdfs for the venue Rose Hall", "Rose Hall"),
    ("V1234 pdfs", "V1234"),
    ("show me the PDF for <https://x|Rose Hall>", "Rose Hall"),
])
def test_parse(q, want):
    assert pl.parse(q) == want


@pytest.mark.parametrize("q", ["why do free couples only see 3 pdfs for each venue? it seems low",
                               "how many pdfs were opened yesterday", "hide V12", "look up user 34819",
                               "the pdf for V12 is cropped, can someone fix it"])
def test_parse_leaves_other_questions_alone(q):
    assert pl.parse(q) is None


def test_pdfs_first_form():
    assert pl.parse("pdfs for Gramercy") == "Gramercy"


def test_lists_by_name_with_links_newest_first_and_flags():
    b, slack, store = bot_with_pdfs()
    assert b.handle(mention("give me all the pdfs for Rose Hall")) == "pdfs:2"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "Rose Hall (V5): 2 PDFs* (1 shown in the app)" in txt
    assert txt.index("Current PDF") < txt.index("Past Season PDF 1")          # 2026 before 2025
    assert DRIVE in txt and "[token]" not in txt                               # Drive links survive
    assert "hidden in the app" in txt and "confidentiality flag" in txt and "prices extracted" in txt
    assert "Other venue" not in txt                                            # another vendor's PDF


def test_exact_name_wins_and_ambiguous_names_ask_with_ids():
    b, slack, store = bot_with_pdfs()
    assert b.handle(mention("give me all the pdfs for Gramercy Mansion")) == "pdfs:1"
    assert "Gramercy Mansion (V6): 1 PDF*" in slack.of("chat.postMessage")[-1]["text"]
    assert b.handle(mention("pdfs for Gramercy")) == "pdfs:ambiguous"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "(V7)" in txt and "(V8)" in txt and "Which one?" in txt


def test_no_pdfs_and_unknown_vendor():
    b, slack, store = bot_with_pdfs(rows=[])
    assert b.handle(mention("pdfs for V5")) == "pdfs:0"
    assert "no PDFs on file" in slack.of("chat.postMessage")[-1]["text"]
    assert b.handle(mention("pdfs for Nowhere Barn")) == "pdfs:not-found"


def test_index_is_read_once_and_reused():
    b, slack, store = bot_with_pdfs()
    b.handle(mention("pdfs for V5"))
    b.handle(mention("pdfs for V6"))
    assert b.xano.calls == 1


def test_no_model_call_and_no_card(monkeypatch):
    b, slack, store = bot_with_pdfs()
    monkeypatch.setattr(b, "answer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    b.handle(mention("pdfs for V5"))
    assert store.all() == []


def test_markup_in_labels_is_escaped():
    rows = [dict(PDFS[0], Current_Assignment="<!channel> PDF", PDF_Link="javascript:alert(1)")]
    b, slack, store = bot_with_pdfs(rows=rows)
    b.handle(mention("pdfs for V5"))
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "<!channel>" not in txt and "javascript:" not in txt.split("&lt;!channel&gt;")[0] + "x"[:0]
    assert "<javascript" not in txt


def test_help_mentions_pdfs():
    b, slack, store = bot_with_pdfs()
    b.handle(mention("help"))
    assert "PDFs for V1234" in slack.of("chat.postMessage")[-1]["text"]
