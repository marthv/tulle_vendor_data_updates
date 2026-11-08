"""
Background-refreshed snapshots.

The Streamlit app cached in process memory and only filled the cache when someone opened
the tab, so the first visitor after every deploy/restart waited ~30-75 s. Here each
snapshot is built at startup and refreshed on a timer in a background thread; requests
always get the last good copy instantly. A failed refresh keeps the previous copy.
"""

import threading
import time
import traceback


class Snapshot:
    def __init__(self, name, builder, ttl):
        self.name, self.builder, self.ttl = name, builder, ttl
        self.value = None
        self.error = None
        self.built_at = 0.0
        self.building = False
        self._lock = threading.Lock()

    def _build(self):
        with self._lock:
            if self.building:
                return
            self.building = True
        try:
            v = self.builder()
            self.value, self.error, self.built_at = v, None, time.time()
        except Exception as e:  # noqa: BLE001 - keep serving the old copy
            self.error = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            self.building = False

    def refresh_async(self):
        threading.Thread(target=self._build, daemon=True, name=f"snap-{self.name}").start()

    def get(self, wait_seconds=0):
        """Return the current value; if none yet, optionally wait for the first build."""
        if self.value is None and not self.building:
            self.refresh_async()
        deadline = time.time() + wait_seconds
        while self.value is None and time.time() < deadline and (self.building or self.error is None):
            time.sleep(0.25)
        return self.value

    def status(self):
        return {"name": self.name, "ready": self.value is not None, "building": self.building,
                "built_at": self.built_at or None, "error": self.error}

    def start_timer(self):
        def loop():
            while True:
                self._build()
                time.sleep(self.ttl)
        threading.Thread(target=loop, daemon=True, name=f"timer-{self.name}").start()
