import sqlite3, os, threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_BASE_DIR, 'output_data.db')

# One persistent SQLite connection per thread, created lazily on first use and
# reused thereafter. Opening a fresh connection (and re-asserting PRAGMAs) on
# every operation was wasteful: WAL/synchronous/busy_timeout are durable or
# per-connection settings that only need to be applied once, and history writes
# happen at least ~1-2x/sec at idle (far more during a quake). Connections are
# left open between `with` blocks; we still commit/rollback per block so no read
# transaction lingers to block WAL checkpointing.
_local = threading.local()


def _get_conn(db_name):
    conn = getattr(_local, 'conn', None)
    if conn is not None and getattr(_local, 'db_name', None) == db_name:
        return conn
    if conn is not None:
        # Different db requested on this thread; drop the old handle.
        try:
            conn.close()
        except Exception:
            pass
    conn = sqlite3.connect(db_name, timeout=30, check_same_thread=False)
    # WAL lets readers and the writer proceed concurrently instead of
    # exclusively locking the whole file; busy_timeout makes any residual
    # lock contention retry briefly instead of raising "database is locked".
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=5000')
    _local.conn = conn
    _local.db_name = db_name
    return conn


class Database:
    def __init__(self, db_name=_DEFAULT_DB):
        self.db_name = db_name

    def __enter__(self):
        self.conn = _get_conn(self.db_name)
        self.cursor = self.conn.cursor()
        return self.cursor

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.cursor.close()
        # Connection is intentionally left open for reuse by this thread.
