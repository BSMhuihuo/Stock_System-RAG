from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from .config import Settings, settings


DEFAULT_APP_SETTINGS = {
    "paper_initial_cash": lambda current: current.paper_initial_cash,
    "auto_trade_budget_ratio": lambda current: current.auto_trade_budget_ratio,
    "default_auto_trade_strategy": lambda current: "ensemble_rag",
    "auto_trade_refresh_seconds": lambda current: 30,
    "enable_ollama_decision": lambda current: 1,
    "ollama_timeout_seconds": lambda current: 180,
    "research_top_k": lambda current: 4,
    "market_page_size": lambda current: 30,
    "rag_retrieval_mode": lambda current: "hybrid",
}


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


@contextmanager
def get_connection(app_settings: Settings | None = None) -> Iterator[sqlite3.Connection]:
    current = app_settings or settings
    conn = sqlite3.connect(current.database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(app_settings: Settings | None = None) -> None:
    current = app_settings or settings
    with get_connection(current) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                mode TEXT PRIMARY KEY,
                cash REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                avg_cost REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (mode, symbol)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                notional REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        now = utc_now()
        conn.execute(
            """
            INSERT INTO accounts(mode, cash, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(mode) DO NOTHING
            """,
            ("paper", current.paper_initial_cash, now),
        )
        conn.execute(
            """
            INSERT INTO accounts(mode, cash, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(mode) DO NOTHING
            """,
            ("live", current.live_initial_cash, now),
        )

        for key, resolver in DEFAULT_APP_SETTINGS.items():
            conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, str(resolver(current)), now),
            )
