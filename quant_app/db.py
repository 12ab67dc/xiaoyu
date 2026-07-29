from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


DEFAULT_WATCHLIST = (
    ("sh510300", "沪深300ETF", "ETF"),
    ("sz159915", "创业板ETF", "ETF"),
)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self, capital: float = 100000) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    asset_type TEXT NOT NULL DEFAULT '股票',
                    quantity REAL NOT NULL DEFAULT 0,
                    cost_price REAL NOT NULL DEFAULT 0,
                    loss_limit_pct REAL NOT NULL DEFAULT 8,
                    trailing_drawdown_pct REAL NOT NULL DEFAULT 8,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_cache (
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (market, symbol)
                );

                CREATE TABLE IF NOT EXISTS reports (
                    report_date TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                ("portfolio_capital", str(capital), now),
            )
            count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
            if count == 0:
                for symbol, name, asset_type in DEFAULT_WATCHLIST:
                    conn.execute(
                        """
                        INSERT INTO holdings(
                            symbol, name, asset_type, quantity, cost_price,
                            loss_limit_pct, trailing_drawdown_pct, enabled,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, 0, 0, 8, 8, 1, ?, ?)
                        """,
                        (symbol, name, asset_type, now, now),
                    )

    def list_holdings(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM holdings ORDER BY quantity > 0 DESC, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_holding(self, holding_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE id = ?", (holding_id,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_holding(self, values: dict[str, Any], holding_id: int | None = None) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        fields = (
            values["symbol"],
            values["name"],
            values.get("asset_type", "股票"),
            float(values.get("quantity", 0)),
            float(values.get("cost_price", 0)),
            float(values.get("loss_limit_pct", 8)),
            float(values.get("trailing_drawdown_pct", 8)),
            int(bool(values.get("enabled", True))),
        )
        with self.connect() as conn:
            if holding_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO holdings(
                        symbol, name, asset_type, quantity, cost_price,
                        loss_limit_pct, trailing_drawdown_pct, enabled,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*fields, now, now),
                )
                holding_id = int(cursor.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE holdings SET
                        symbol = ?, name = ?, asset_type = ?, quantity = ?,
                        cost_price = ?, loss_limit_pct = ?,
                        trailing_drawdown_pct = ?, enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (*fields, now, holding_id),
                )
        holding = self.get_holding(holding_id)
        if holding is None:
            raise RuntimeError("Holding was not saved")
        return holding

    def delete_holding(self, holding_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
        return cursor.rowcount > 0

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def load_cache(self, market: str, symbol: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM market_cache WHERE market = ? AND symbol = ?",
                (market, symbol),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload["cached_at"] = row["updated_at"]
        return payload

    def save_cache(self, market: str, symbol: str, payload: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO market_cache(market, symbol, payload, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(market, symbol) DO UPDATE SET
                    payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (market, symbol, json.dumps(payload, ensure_ascii=False), now),
            )

    def latest_report(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM reports ORDER BY report_date DESC LIMIT 1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_report(self, report_date: str, payload: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reports(report_date, payload, created_at) VALUES (?, ?, ?)
                ON CONFLICT(report_date) DO UPDATE SET
                    payload = excluded.payload, created_at = excluded.created_at
                """,
                (report_date, json.dumps(payload, ensure_ascii=False), now),
            )
