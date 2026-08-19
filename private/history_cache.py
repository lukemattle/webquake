import bisect
import threading

_INF = float('inf')


class HistoryCache:
    """In-memory mirror of the history_log SQLite table, covering the most
    recent retention window. Replay reads (markers/gaps/window/state) are
    served from here instead of SQLite so many concurrent replay viewers
    don't contend with the live write path or each other.

    SQLite remains the durable store: rows are still inserted there first,
    this cache is just a read-side accelerator rebuilt from it on startup
    (load_from_db) and kept in sync afterwards (add/prune).
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._seq = 0
        self._all = []      # (ts, seq, msg_type, payload, marker_kind, marker_label, event_ts), sorted by ts then seq
        self._by_type = {}  # msg_type -> same-shaped list, sorted
        self._markers = []  # subset of entries where marker_kind is not None, sorted
        self._size_bytes = 0  # running total mirroring stats()'s size_bytes, kept in sync by add()/prune()
        self.ready = False
        # Startup-load metadata, set by the caller (main.py) for admin/diagnostic reporting.
        self.load_seconds: float | None = None
        self.load_error: str | None = None
        self.loaded_at: float | None = None

    def clear(self):
        with self._lock:
            self._seq = 0
            self._all = []
            self._by_type = {}
            self._markers = []
            self._size_bytes = 0
            self.ready = False

    @staticmethod
    def _entry_size(entry):
        _ts, _seq, msg_type, payload, marker_kind, marker_label, _ets = entry
        return (len(msg_type) + (len(payload) if payload is not None else 0) +
                (len(marker_kind) if marker_kind is not None else 0) +
                (len(marker_label) if marker_label is not None else 0))

    def add(self, ts, msg_type, payload, marker_kind=None, marker_label=None, event_ts=None):
        with self._lock:
            self._seq += 1
            entry = (ts, self._seq, msg_type, payload, marker_kind, marker_label, event_ts)
            bisect.insort(self._all, entry)
            bisect.insort(self._by_type.setdefault(msg_type, []), entry)
            if marker_kind is not None:
                bisect.insort(self._markers, entry)
            self._size_bytes += self._entry_size(entry)

    def load_from_db(self, rows):
        """rows: iterable of (ts, msg_type, payload, marker_kind, marker_label, event_ts)."""
        with self._lock:
            self.clear()
            for ts, msg_type, payload, marker_kind, marker_label, event_ts in rows:
                self.add(ts, msg_type, payload, marker_kind, marker_label, event_ts)
            self.ready = True

    def prune(self, cutoff_ts):
        with self._lock:
            idx = bisect.bisect_left(self._all, (cutoff_ts,))
            for entry in self._all[:idx]:
                self._size_bytes -= self._entry_size(entry)
            self._all = self._all[idx:]
            self._markers = self._trim(self._markers, cutoff_ts)
            for msg_type in list(self._by_type.keys()):
                trimmed = self._trim(self._by_type[msg_type], cutoff_ts)
                if trimmed:
                    self._by_type[msg_type] = trimmed
                else:
                    del self._by_type[msg_type]

    @staticmethod
    def _trim(lst, cutoff_ts):
        idx = bisect.bisect_left(lst, (cutoff_ts,))
        return lst[idx:]

    # --- query helpers, mirroring the SQL shapes they replace in main.py ---

    def latest_before(self, msg_type, at):
        """(ts, payload) for the latest row of msg_type with ts <= at, or None."""
        with self._lock:
            lst = self._by_type.get(msg_type)
            if not lst:
                return None
            idx = bisect.bisect_right(lst, (at, _INF))
            if idx == 0:
                return None
            ts, _seq, _t, payload, _mk, _ml, _ets = lst[idx - 1]
            return ts, payload

    def max_ts_before(self, msg_type, at):
        with self._lock:
            lst = self._by_type.get(msg_type)
            if not lst:
                return None
            idx = bisect.bisect_right(lst, (at, _INF))
            return lst[idx - 1][0] if idx else None

    def range(self, msg_type, ts_from, ts_to, from_exclusive=True):
        """[(ts, payload), ...] ascending for msg_type within the range.
        from_exclusive=True matches SQL "ts > ts_from AND ts <= ts_to"."""
        with self._lock:
            lst = self._by_type.get(msg_type)
            if not lst:
                return []
            lo = bisect.bisect_right(lst, (ts_from, _INF)) if from_exclusive else bisect.bisect_left(lst, (ts_from,))
            hi = bisect.bisect_right(lst, (ts_to, _INF))
            return [(e[0], e[3]) for e in lst[lo:hi]]

    def rows_before(self, msg_type, at, ascending=True):
        """All [(ts, payload), ...] for msg_type with ts <= at."""
        with self._lock:
            lst = self._by_type.get(msg_type)
            if not lst:
                return []
            idx = bisect.bisect_right(lst, (at, _INF))
            rows = [(e[0], e[3]) for e in lst[:idx]]
            return rows if ascending else list(reversed(rows))

    def markers_in_range(self, ts_from, ts_to):
        with self._lock:
            lo = bisect.bisect_left(self._markers, (ts_from,))
            hi = bisect.bisect_right(self._markers, (ts_to, _INF))
            return [(e[0], e[2], e[4], e[5], e[6]) for e in self._markers[lo:hi]]

    def stats(self):
        """Snapshot of cache size/coverage for admin/diagnostic reporting.
        size_bytes mirrors the SUM(LENGTH(...)) estimate main.py already
        computes for the SQLite history_log table, so the two are comparable."""
        with self._lock:
            rows = len(self._all)
            size_bytes = self._size_bytes
            oldest_ts = self._all[0][0] if rows else None
            newest_ts = self._all[-1][0] if rows else None
        return {
            'ready': self.ready,
            'row_count': rows,
            'size_bytes': size_bytes,
            'oldest_ts': oldest_ts,
            'newest_ts': newest_ts,
            'load_seconds': self.load_seconds,
            'load_error': self.load_error,
            'loaded_at': self.loaded_at,
        }

    def window(self, ts_from, ts_to, limit, exclude_type='heartbeat'):
        """[(ts, payload), ...] ascending across all types within the range,
        skipping `exclude_type` and null payloads, capped at `limit`."""
        with self._lock:
            lo = bisect.bisect_left(self._all, (ts_from,))
            hi = bisect.bisect_right(self._all, (ts_to, _INF))
            out = []
            for e in self._all[lo:hi]:
                if e[2] == exclude_type or e[3] is None:
                    continue
                out.append((e[0], e[3]))
                if len(out) >= limit:
                    break
            return out
