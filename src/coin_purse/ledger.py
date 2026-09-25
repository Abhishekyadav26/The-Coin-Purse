"""Append-only audit ledger: every payment decision (paid or refused, and why).

Arjun reads this the next morning. Backed by SQLite (suggested stack) with a
JSONL mirror for easy inspection / inclusion in the repo.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    stall TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    amount_base_units INTEGER,
    asset TEXT,
    network TEXT,
    scheme TEXT,
    decision TEXT NOT NULL,   -- 'paid' or 'refused'
    reason TEXT NOT NULL,
    tx_hash TEXT,
    spent_total_after INTEGER NOT NULL
);
"""


class DecisionLedger:
    def __init__(self, db_path: str = "decisions.db", jsonl_path: str | None = None):
        self.db_path = db_path
        self.jsonl_path = jsonl_path
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def record(
        self,
        *,
        stall: str,
        endpoint: str,
        decision: str,
        reason: str,
        amount_base_units: int | None = None,
        asset: str | None = None,
        network: str | None = None,
        scheme: str | None = None,
        tx_hash: str | None = None,
        spent_total_after: int = 0,
    ) -> dict:
        assert decision in ("paid", "refused"), decision
        ts = datetime.now(timezone.utc).isoformat()
        row = {
            "ts": ts,
            "stall": stall,
            "endpoint": endpoint,
            "amount_base_units": amount_base_units,
            "asset": asset,
            "network": network,
            "scheme": scheme,
            "decision": decision,
            "reason": reason,
            "tx_hash": tx_hash,
            "spent_total_after": spent_total_after,
        }
        self._conn.execute(
            """INSERT INTO decisions
               (ts, stall, endpoint, amount_base_units, asset, network, scheme,
                decision, reason, tx_hash, spent_total_after)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ts,
                stall,
                endpoint,
                amount_base_units,
                asset,
                network,
                scheme,
                decision,
                reason,
                tx_hash,
                spent_total_after,
            ),
        )
        self._conn.commit()
        if self.jsonl_path:
            directory = os.path.dirname(os.path.abspath(self.jsonl_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        # Small sleep keeps identical timestamps ordered in fast loops.
        time.sleep(0.001)
        return row

    def all(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM decisions ORDER BY id")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def total_paid_base_units(self) -> int:
        """Cumulative settled spend, integer base units, from durable storage.

        The buyer reads this back on startup so the run budget survives
        restarts — spend is never trusted to a fresh in-memory zero.
        """
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(amount_base_units), 0) FROM decisions "
            "WHERE decision = 'paid'"
        )
        return int(cur.fetchone()[0] or 0)

    def close(self) -> None:
        self._conn.close()
