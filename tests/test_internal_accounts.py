"""Every internal-account exclusion list carries Des's and Kate's test accounts (owner 2026-11-20) and never the
two real couples the owner named. Team email prefixes come from INTERNAL_EMAIL_PREFIXES (this repo is public), never
from code. Source-level check so no module (some need live env) has to be imported."""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TEST_IDS = {21414, 21797, 21800, 21801, 21998, 22002, 22007, 22118, 22548, 22549, 22581, 22694, 24403, 26339, 26647,
            26649, 26726, 27701, 29112, 29564, 29747, 31971, 32114, 33154, 33155, 33602, 34096, 35020, 35212, 36553,
            35527}
REAL_COUPLES = {30959, 34873}
LISTS = {"daily_brief.py": "INTERNAL_IDS", "experiments_brief.py": "INTERNAL_IDS",
         "experiments_engine/xano.py": "INTERNAL_IDS", "rec_service/plan.py": "ALWAYS_PLAN",
         "rec_service/sheet_log.py": "INTERNAL_IDS", "rec_watch.py": "INTERNAL_USER_IDS",
         "slack_qa/reports.py": "DEFAULT_INTERNAL_IDS", "venue_digest.py": "DEFAULT_EXCLUDE"}
# modules whose internal check reads an email (rec_service: list constant only, by design)
EMAIL_RULES = ("daily_brief.py", "experiments_brief.py", "experiments_engine/xano.py", "slack_qa/reports.py")


def _ids(path, name):
    tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return {int(x) for x in re.findall(r"\d+", ast.unparse(node.value))}
    raise AssertionError(f"{name} not found in {path}")


class InternalAccounts(unittest.TestCase):
    def test_every_list_has_test_accounts_and_no_real_couples(self):
        for path, name in LISTS.items():
            ids = _ids(path, name)
            self.assertFalse(TEST_IDS - ids, f"{path}: missing {sorted(TEST_IDS - ids)}")
            self.assertFalse(REAL_COUPLES & ids, f"{path}: real couple excluded")

    def test_email_prefixes_come_from_env_not_code(self):
        for path in EMAIL_RULES:
            src = open(os.path.join(ROOT, path), encoding="utf-8").read()
            self.assertIn("INTERNAL_EMAIL_PREFIXES", src, path)
            self.assertIsNone(re.search(r"[\w.+-]+@(gmail|yahoo|hotmail|icloud|aol)\.com", src, re.I), path)

    def test_experiments_engine_is_internal(self):
        from unittest import mock
        from experiments_engine import xano
        self.assertTrue(xano.is_internal(21414))
        with mock.patch.dict(os.environ, {"INTERNAL_EMAIL_PREFIXES": ""}):
            self.assertFalse(xano.is_internal(None, "teamtest4@example.com"))      # empty default: no prefix rule
            self.assertTrue(xano.is_internal(None, "vivek.marthi+x@example.com"))  # unchanged from main
            self.assertTrue(xano.is_internal(None, "someone@tulletogether.com"))
        with mock.patch.dict(os.environ, {"INTERNAL_EMAIL_PREFIXES": "teamtest, qa.alias+"}):
            self.assertTrue(xano.is_internal(None, "TeamTest4@example.com"))
            self.assertTrue(xano.is_internal(None, "qa.alias+planner@example.com"))
            self.assertFalse(xano.is_internal(None, "qa.alias@example.com"))       # the + alias only
            self.assertFalse(xano.is_internal(30959, "couple@example.com"))
            self.assertFalse(xano.is_internal(34873))


if __name__ == "__main__":
    unittest.main()
