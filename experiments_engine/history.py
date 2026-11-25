"""Readout history and alert de-dup state on the service volume (FRAMEWORK.md sections 5.7, 9).

Files (in ``EXP_DATA_DIR``, default ``/data/experiments``):

* ``readouts.jsonl`` - one row per experiment per UTC day. Re-running the same day replaces
  that day's row (idempotent), so the cron can run every 6 h. Rows are append-only across
  days; the file is rewritten atomically (temp file + ``os.replace``) on every write.
* ``alerts_sent.json`` - {key: iso timestamp} for urgent-alert de-duplication
  ("sent once per breach per experiment", section 9).

Nothing touches the filesystem at import time; the directory is created on first write.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile

DEFAULT_DIR = "/data/experiments"
READOUTS = "readouts.jsonl"
ALERTS = "alerts_sent.json"
LOCKS = "settings_lock.json"      # EXP_TAB_V2: launch settings first seen live, per experiment
LOOKS = "harm_looks.json"         # EXP_TAB_V2: today's harm look per split (one a day); a fired stop stays
READS = "reads.json"              # EXP_TAB_V2: the one read of each split, kept as first taken

ROW_FIELDS = ("health", "stale", "days_running", "progress", "projected_days", "overdue_days")


def data_dir(path=None):
    return path or os.environ.get("EXP_DATA_DIR") or DEFAULT_DIR


def _atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _date(v):
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def row_from_experiment(exp, day, generated_at=None):
    """Compact daily row for one snapshot experiment (what health.py reads back)."""
    st = exp.get("stats") or {}
    ro = exp.get("readout") or {}
    arms = ro.get("arms") or {}
    c, t = arms.get("control") or {}, arms.get("treatment") or {}
    n_total = None
    if arms:
        n_total = sum((a.get("n") or 0) for a in arms.values() if isinstance(a, dict))
    row = {"date": day, "exp_id": exp.get("id"), "generated_at": generated_at,
           "p_beat": st.get("p_beat"), "lift_median_rel": st.get("lift_median_rel"),
           "lift_ci95_rel": st.get("lift_ci95_rel"), "expected_loss_abs": st.get("expected_loss_abs"),
           "control_rate": st.get("control_rate"), "treatment_rate": st.get("treatment_rate"),
           "n_control": c.get("n"), "conv_control": c.get("conv"),
           "n_treatment": t.get("n"), "conv_treatment": t.get("conv"), "n_total": n_total,
           "error": ro.get("error")}
    for k in ROW_FIELDS:
        row[k] = exp.get(k)
    if exp.get("result_hidden"):           # EXP_TAB_V2: couples only until the read date
        for k in ("p_beat", "lift_median_rel", "lift_ci95_rel", "expected_loss_abs", "control_rate",
                  "treatment_rate", "conv_control", "conv_treatment"):
            row[k] = None
    return row


class HistoryStore:
    """JSONL readout store + alert de-dup state in one directory."""

    def __init__(self, path=None):
        self.dir = data_dir(path)

    @property
    def readouts_path(self):
        return os.path.join(self.dir, READOUTS)

    @property
    def alerts_path(self):
        return os.path.join(self.dir, ALERTS)

    # -- readouts ---------------------------------------------------------
    def _read_all(self):
        rows = []
        try:
            with open(self.readouts_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue  # a torn/corrupt line never breaks the store
                    if isinstance(r, dict):
                        rows.append(r)
        except FileNotFoundError:
            pass
        return rows

    def append_day(self, snapshot):
        """Write one row per evaluated experiment for the snapshot's UTC day.

        Idempotent: rows for the same (date, exp_id) are replaced. Returns rows written.
        """
        day = (snapshot or {}).get("today") or str((snapshot or {}).get("generated_at") or "")[:10]
        if not _date(day):
            day = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
        new = [row_from_experiment(e, day, snapshot.get("generated_at"))
               for e in snapshot.get("experiments") or []
               if isinstance(e, dict) and e.get("id") and e.get("health")]
        keys = {(r["date"], r["exp_id"]) for r in new}
        kept = [r for r in self._read_all() if (str(r.get("date"))[:10], r.get("exp_id")) not in keys]
        rows = kept + new
        rows.sort(key=lambda r: (str(r.get("date")), str(r.get("exp_id"))))
        _atomic_write(self.readouts_path,
                      "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows))
        return len(new)

    def load(self, exp_id, days=90, today=None):
        """Rows for one experiment from the last ``days`` days (inclusive), oldest first."""
        ref = _date(today) if today is not None else _dt.datetime.now(_dt.timezone.utc).date()
        cutoff = ref - _dt.timedelta(days=int(days or 0))
        out = [r for r in self._read_all() if r.get("exp_id") == exp_id
               and (_date(r.get("date")) or _dt.date.min) >= cutoff]
        out.sort(key=lambda r: str(r.get("date")))
        return out

    # -- EXP_TAB_V2: launch settings and the one read ----------------------
    def _json(self, name):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _put_once(self, name, key, value):
        """Write-once: an existing value is never replaced."""
        d = self._json(name)
        if str(key) in d:
            return d[str(key)]
        d[str(key)] = value
        _atomic_write(os.path.join(self.dir, name), json.dumps(d, indent=1, sort_keys=True, default=str))
        return value

    def load_lock(self, exp_id):
        return self._json(LOCKS).get(str(exp_id))

    def save_lock(self, exp_id, lock):
        return self._put_once(LOCKS, exp_id, lock)

    def load_look(self, exp_id):
        return self._json(LOOKS).get(str(exp_id))

    def save_look(self, exp_id, look):
        d = self._json(LOOKS)
        if (d.get(str(exp_id)) or {}).get("fired"):
            return                                  # a stop that fired stays fired
        d[str(exp_id)] = look
        _atomic_write(os.path.join(self.dir, LOOKS), json.dumps(d, indent=1, sort_keys=True, default=str))

    def load_read(self, exp_id):
        return self._json(READS).get(str(exp_id))

    def save_read(self, exp_id, read):
        return self._put_once(READS, exp_id, read)

    # -- alert de-dup -----------------------------------------------------
    def _alerts(self):
        try:
            with open(self.alerts_path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def was_sent(self, key):
        return str(key) in self._alerts()

    def mark_sent(self, key, when=None):
        d = self._alerts()
        d[str(key)] = when or _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        _atomic_write(self.alerts_path, json.dumps(d, indent=1, sort_keys=True))


# Module-level convenience wrappers (use EXP_DATA_DIR unless ``path`` is given).
def append_day(snapshot, path=None):
    return HistoryStore(path).append_day(snapshot)


def load(exp_id, days=90, path=None, today=None):
    return HistoryStore(path).load(exp_id, days=days, today=today)


def was_sent(key, path=None):
    return HistoryStore(path).was_sent(key)


def mark_sent(key, path=None):
    return HistoryStore(path).mark_sent(key)
