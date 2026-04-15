from __future__ import annotations

from ..db import get_connection, utc_now
from ..schemas import SystemSettingsResponse, SystemSettingsUpdateRequest


SETTING_KEYS = (
    "paper_initial_cash",
    "auto_trade_budget_ratio",
    "default_auto_trade_strategy",
    "auto_trade_refresh_seconds",
    "enable_ollama_decision",
    "ollama_timeout_seconds",
    "research_top_k",
    "market_page_size",
    "rag_retrieval_mode",
)


def _to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class SystemSettingsService:
    def get_settings(self) -> SystemSettingsResponse:
        placeholders = ", ".join("?" for _ in SETTING_KEYS)
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT key, value, updated_at FROM app_settings WHERE key IN ({placeholders})",
                SETTING_KEYS,
            ).fetchall()

        values = {row["key"]: row["value"] for row in rows}
        updated_at = max((row["updated_at"] for row in rows), default=utc_now())
        return SystemSettingsResponse(
            paper_initial_cash=float(values.get("paper_initial_cash", "100000")),
            auto_trade_budget_ratio=float(values.get("auto_trade_budget_ratio", "0.2")),
            default_auto_trade_strategy=str(values.get("default_auto_trade_strategy", "ensemble_rag")),
            auto_trade_refresh_seconds=int(float(values.get("auto_trade_refresh_seconds", "30"))),
            enable_ollama_decision=_to_bool(values.get("enable_ollama_decision"), True),
            ollama_timeout_seconds=int(float(values.get("ollama_timeout_seconds", "180"))),
            research_top_k=int(float(values.get("research_top_k", "4"))),
            market_page_size=int(float(values.get("market_page_size", "30"))),
            rag_retrieval_mode=str(values.get("rag_retrieval_mode", "hybrid")).strip().lower() or "hybrid",
            updated_at=updated_at,
        )

    def update_settings(self, request: SystemSettingsUpdateRequest) -> SystemSettingsResponse:
        normalized_retrieval_mode = str(request.rag_retrieval_mode or "hybrid").strip().lower()
        if request.paper_initial_cash <= 0:
            raise ValueError("paper_initial_cash must be > 0")
        if not 0 < request.auto_trade_budget_ratio <= 1:
            raise ValueError("auto_trade_budget_ratio must be within (0, 1]")
        if request.research_top_k <= 0 or request.research_top_k > 20:
            raise ValueError("research_top_k must be within [1, 20]")
        if request.market_page_size < 10 or request.market_page_size > 100:
            raise ValueError("market_page_size must be within [10, 100]")
        if request.auto_trade_refresh_seconds < 5 or request.auto_trade_refresh_seconds > 3600:
            raise ValueError("auto_trade_refresh_seconds must be within [5, 3600]")
        if request.ollama_timeout_seconds < 20 or request.ollama_timeout_seconds > 600:
            raise ValueError("ollama_timeout_seconds must be within [20, 600]")
        if normalized_retrieval_mode not in {"hybrid", "lexical"}:
            raise ValueError("rag_retrieval_mode must be 'hybrid' or 'lexical'")

        now = utc_now()
        updates = {
            "paper_initial_cash": request.paper_initial_cash,
            "auto_trade_budget_ratio": request.auto_trade_budget_ratio,
            "default_auto_trade_strategy": request.default_auto_trade_strategy,
            "auto_trade_refresh_seconds": request.auto_trade_refresh_seconds,
            "enable_ollama_decision": int(bool(request.enable_ollama_decision)),
            "ollama_timeout_seconds": request.ollama_timeout_seconds,
            "research_top_k": request.research_top_k,
            "market_page_size": request.market_page_size,
            "rag_retrieval_mode": normalized_retrieval_mode,
        }

        with get_connection() as conn:
            for key, value in updates.items():
                conn.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, str(value), now),
                )

            if request.reset_paper_account:
                conn.execute("DELETE FROM positions WHERE mode = ?", ("paper",))
                conn.execute("DELETE FROM orders WHERE mode = ?", ("paper",))
                conn.execute(
                    "UPDATE accounts SET cash = ?, updated_at = ? WHERE mode = ?",
                    (round(request.paper_initial_cash, 2), now, "paper"),
                )

        return self.get_settings()
