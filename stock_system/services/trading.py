from __future__ import annotations

import re

from ..config import settings
from ..db import get_connection, init_db, utc_now
from ..schemas import AccountSnapshot, OrderResult, PositionItem
from .market_data import MarketDataService
from .ranking import RankingService
from .research import ResearchService
from .system_settings import SystemSettingsService


class TradingService:
    def __init__(
        self,
        market_data_service: MarketDataService,
        ranking_service: RankingService,
        research_service: ResearchService,
        settings_service: SystemSettingsService,
    ) -> None:
        self.market_data_service = market_data_service
        self.ranking_service = ranking_service
        self.research_service = research_service
        self.settings_service = settings_service
        init_db()

    def get_account_snapshot(self, mode: str = "paper") -> AccountSnapshot:
        with get_connection() as conn:
            account_row = conn.execute("SELECT mode, cash FROM accounts WHERE mode = ?", (mode,)).fetchone()
            if account_row is None:
                raise ValueError(f"Unknown account mode: {mode}")

            position_rows = conn.execute(
                """
                SELECT mode, symbol, name, quantity, avg_cost
                FROM positions
                WHERE mode = ?
                ORDER BY symbol
                """,
                (mode,),
            ).fetchall()

        positions: list[PositionItem] = []
        total_market_value = 0.0
        for row in position_rows:
            market_price = self.market_data_service.get_latest_price(row["symbol"])
            market_value = round(market_price * int(row["quantity"]), 2)
            unrealized_pnl = round((market_price - float(row["avg_cost"])) * int(row["quantity"]), 2)
            total_market_value += market_value
            positions.append(
                PositionItem(
                    mode=row["mode"],
                    symbol=row["symbol"],
                    name=row["name"],
                    quantity=int(row["quantity"]),
                    avg_cost=round(float(row["avg_cost"]), 2),
                    market_price=round(market_price, 2),
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                )
            )

        cash = round(float(account_row["cash"]), 2)
        total_market_value = round(total_market_value, 2)
        total_equity = round(cash + total_market_value, 2)
        return AccountSnapshot(
            mode=mode,
            cash=cash,
            positions=positions,
            total_market_value=total_market_value,
            total_equity=total_equity,
        )

    def place_order(
        self,
        mode: str,
        symbol: str,
        name: str,
        side: str,
        quantity: int,
        price: float | None = None,
        reason: str = "",
    ) -> OrderResult:
        normalized_mode = mode.lower().strip()
        normalized_side = side.upper().strip()
        if normalized_mode not in {"paper", "live"}:
            raise ValueError("mode must be 'paper' or 'live'")
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        if normalized_mode == "live" and not settings.enable_live_trading:
            return self._record_rejected_order(
                mode=normalized_mode,
                symbol=symbol,
                name=name,
                side=normalized_side,
                quantity=quantity,
                price=price or 0.0,
                reason="实盘模式已禁用，当前版本仅允许 paper trading。",
            )

        resolved_price = price
        if resolved_price is None:
            realtime = self.market_data_service.get_realtime_snapshot(symbol)
            resolved_price = float(realtime.get("price", 0.0) or 0.0)
        if not resolved_price:
            resolved_price = self.market_data_service.get_latest_price(symbol)

        trade_price = round(float(resolved_price), 2)
        if trade_price <= 0:
            return self._record_rejected_order(
                mode=normalized_mode,
                symbol=symbol,
                name=name,
                side=normalized_side,
                quantity=quantity,
                price=trade_price,
                reason="无法获取有效价格。",
            )

        with get_connection() as conn:
            account_row = conn.execute("SELECT cash FROM accounts WHERE mode = ?", (normalized_mode,)).fetchone()
            if account_row is None:
                raise ValueError(f"Unknown account mode: {normalized_mode}")

            cash = float(account_row["cash"])
            notional = round(trade_price * quantity, 2)
            now = utc_now()

            if normalized_side == "BUY":
                if cash < notional:
                    return self._insert_order(
                        conn=conn,
                        mode=normalized_mode,
                        symbol=symbol,
                        name=name,
                        side=normalized_side,
                        quantity=quantity,
                        price=trade_price,
                        notional=notional,
                        status="rejected",
                        reason=f"资金不足，当前现金 {cash:.2f}。",
                        created_at=now,
                    )
                self._apply_buy(conn, normalized_mode, symbol, name, quantity, trade_price, cash, now)
                return self._insert_order(
                    conn=conn,
                    mode=normalized_mode,
                    symbol=symbol,
                    name=name,
                    side=normalized_side,
                    quantity=quantity,
                    price=trade_price,
                    notional=notional,
                    status="filled",
                    reason=reason or "手动买入",
                    created_at=now,
                )

            position_row = conn.execute(
                "SELECT quantity, avg_cost FROM positions WHERE mode = ? AND symbol = ?",
                (normalized_mode, symbol),
            ).fetchone()
            if position_row is None or int(position_row["quantity"]) < quantity:
                return self._insert_order(
                    conn=conn,
                    mode=normalized_mode,
                    symbol=symbol,
                    name=name,
                    side=normalized_side,
                    quantity=quantity,
                    price=trade_price,
                    notional=notional,
                    status="rejected",
                    reason="持仓不足，无法卖出。",
                    created_at=now,
                )

            self._apply_sell(conn, normalized_mode, symbol, quantity, trade_price, cash, now)
            return self._insert_order(
                conn=conn,
                mode=normalized_mode,
                symbol=symbol,
                name=name,
                side=normalized_side,
                quantity=quantity,
                price=trade_price,
                notional=notional,
                status="filled",
                reason=reason or "手动卖出",
                created_at=now,
            )

    def auto_trade(self, mode: str = "paper", top_n: int = 3, strategy: str | None = None) -> dict:
        profile = self.settings_service.get_settings()
        requested_strategy = (strategy or profile.default_auto_trade_strategy or "ensemble_rag").strip()

        snapshot = self.get_account_snapshot(mode=mode)
        if requested_strategy in {"ensemble_rag", "multi_rag", "auto_rag"}:
            strategy_recommendations = self._build_multi_strategy_recommendations(top_n=top_n)
            final_strategy, conflict_detected, referee_reason, no_trade, ollama_raw_output = self._resolve_strategy_with_rag(
                strategy_recommendations=strategy_recommendations,
                top_n=top_n,
                snapshot=snapshot,
                use_ollama_decision=bool(profile.enable_ollama_decision),
                ollama_timeout_seconds=int(profile.ollama_timeout_seconds),
            )
            recommendations = strategy_recommendations.get(final_strategy, [])
        else:
            final_strategy = requested_strategy
            conflict_detected = False
            referee_reason = ""
            no_trade = False
            ollama_raw_output = ""
            recommendations = self.ranking_service.recommend(limit=top_n, strategy=final_strategy)

        if not recommendations:
            return {
                "mode": mode,
                "strategy": final_strategy,
                "requested_strategy": requested_strategy,
                "final_strategy": final_strategy,
                "conflict_detected": conflict_detected,
                "final_decision": "NO_TRADE" if no_trade else "EXECUTE",
                "executed": [],
                "skipped": ["没有可用推荐结果。"],
                "referee_reason": referee_reason,
                "ollama_raw_output": ollama_raw_output,
            }

        if no_trade:
            return {
                "mode": mode,
                "strategy": final_strategy,
                "requested_strategy": requested_strategy,
                "final_strategy": final_strategy,
                "conflict_detected": conflict_detected,
                "final_decision": "NO_TRADE",
                "budget": round(snapshot.cash * profile.auto_trade_budget_ratio, 2),
                "executed": [],
                "skipped": ["RAG裁决为不买入（NO_TRADE）。"],
                "referee_reason": referee_reason,
                "ollama_raw_output": ollama_raw_output,
            }

        budget = snapshot.cash * profile.auto_trade_budget_ratio
        if budget <= 0:
            return {
                "mode": mode,
                "strategy": final_strategy,
                "requested_strategy": requested_strategy,
                "final_strategy": final_strategy,
                "conflict_detected": conflict_detected,
                "final_decision": "NO_TRADE",
                "executed": [],
                "skipped": ["账户现金不足，无法自动交易。"],
                "referee_reason": referee_reason,
                "ollama_raw_output": ollama_raw_output,
            }

        per_stock_budget = budget / max(len(recommendations), 1)
        executed = []
        skipped = []

        for item in recommendations:
            quantity = int(per_stock_budget // item.price // 100) * 100
            if quantity < 100:
                skipped.append(f"{item.symbol} 预算不足 100 股。")
                continue
            result = self.place_order(
                mode=mode,
                symbol=item.symbol,
                name=item.name,
                side="BUY",
                quantity=quantity,
                price=item.price,
                reason=f"自动交易[{final_strategy}] 买入: {item.reason}",
            )
            if result.status == "filled":
                executed.append(result.model_dump())
            else:
                skipped.append(f"{item.symbol} 下单失败: {result.reason}")

        return {
            "mode": mode,
            "strategy": final_strategy,
            "requested_strategy": requested_strategy,
            "final_strategy": final_strategy,
            "conflict_detected": conflict_detected,
            "final_decision": "EXECUTE",
            "budget": round(budget, 2),
            "executed": executed,
            "skipped": skipped,
            "referee_reason": referee_reason,
            "ollama_raw_output": ollama_raw_output,
        }

    def _build_multi_strategy_recommendations(self, top_n: int) -> dict[str, list]:
        return {
            "momentum": self.ranking_service.recommend(limit=top_n, strategy="momentum"),
            "mean_reversion": self.ranking_service.recommend(limit=top_n, strategy="mean_reversion"),
            "quality_news": self.ranking_service.recommend(limit=top_n, strategy="quality_news"),
        }

    def _resolve_strategy_with_rag(
        self,
        strategy_recommendations: dict[str, list],
        top_n: int,
        snapshot: AccountSnapshot,
        use_ollama_decision: bool,
        ollama_timeout_seconds: int,
    ) -> tuple[str, bool, str, bool, str]:
        available = {name: items for name, items in strategy_recommendations.items() if items}
        if not available:
            return "ensemble", False, "没有可用策略候选，回退到 ensemble。", True, ""

        top_symbols = {items[0].symbol for items in available.values() if items}
        conflict_detected = len(top_symbols) > 1
        if not conflict_detected:
            chosen = self._pick_best_by_avg_score(available)
            return chosen, False, "多策略结果一致，无需RAG裁决。", False, ""

        if not use_ollama_decision:
            chosen = self._pick_best_by_avg_score(available)
            return chosen, True, f"已禁用Ollama裁决，按平均分选择 {chosen}。", False, ""

        prompt = self._build_strategy_decision_prompt(available, top_n=top_n, snapshot=snapshot)
        ollama_result = self.research_service.generate_with_ollama_debug(
            prompt=prompt,
            timeout=ollama_timeout_seconds,
        )
        rag_answer = str(ollama_result.get("response", "")).strip()
        ollama_debug = self._format_ollama_debug(ollama_result)
        if not rag_answer and self._is_ollama_timeout(ollama_result):
            retry_prompt = self._build_strategy_decision_prompt(
                available,
                top_n=top_n,
                snapshot=snapshot,
                compact=True,
            )
            retry_result = self.research_service.generate_with_ollama_debug(
                prompt=retry_prompt,
                timeout=ollama_timeout_seconds,
            )
            rag_answer = str(retry_result.get("response", "")).strip()
            ollama_debug = self._merge_ollama_debug(first=ollama_result, retry=retry_result)
        if not rag_answer:
            chosen = self._pick_best_by_avg_score(available)
            reason = f"Ollama未返回有效结果，已按平均分回退到 {chosen}。"
            if ollama_debug:
                reason = f"{reason}\n{ollama_debug}"
            return chosen, True, reason, False, ollama_debug

        decision = self._extract_trade_decision(rag_answer)
        if decision == "NO_TRADE":
            chosen = self._pick_best_by_avg_score(available)
            return chosen, True, rag_answer, True, ollama_debug

        chosen = self._extract_strategy_name(rag_answer)
        if chosen not in available:
            chosen = self._pick_best_by_avg_score(available)
            rag_answer = f"{rag_answer}\n[系统回退] 未识别明确策略，已按平均分选择 {chosen}。".strip()
            if ollama_debug:
                rag_answer = f"{rag_answer}\n{ollama_debug}"
            return chosen, True, rag_answer, False, ollama_debug
        return chosen, True, rag_answer, False, ollama_debug

    def _build_strategy_decision_prompt(
        self,
        strategy_recommendations: dict[str, list],
        top_n: int,
        snapshot: AccountSnapshot,
        compact: bool = False,
    ) -> str:
        def _safe_float(value: object, default: float = 0.0) -> float:
            try:
                if value is None:
                    return default
                return float(value)
            except Exception:
                return default

        history_limit = 10 if compact else 30
        reason_limit = 2 if compact else 5
        lines = [
            "你是自动交易策略裁决器。",
            "任务：基于账户状态和候选股票数据，决定本轮是买入还是不买。",
            "若买入，再在 momentum、mean_reversion、quality_news 三个策略中选一个最终策略。",
            "必须基于给定候选股票的量化指标、历史数据、当前持仓与可用资金做判断，不要输出格式外内容。",
            f"候选数量 top_n={top_n}",
            "",
            "账户状态：",
            f"- cash={snapshot.cash:.2f}",
            f"- total_market_value={snapshot.total_market_value:.2f}",
            f"- total_equity={snapshot.total_equity:.2f}",
        ]
        if compact:
            lines.append("- 说明：当前为超时重试的精简模式，请优先输出最终结论。")
        if snapshot.positions:
            lines.append("- 当前持仓：")
            for pos in snapshot.positions:
                exposure = (pos.market_value / snapshot.total_equity * 100) if snapshot.total_equity else 0.0
                lines.append(
                    f"  · {pos.symbol} {pos.name} qty={pos.quantity} avg_cost={pos.avg_cost:.2f} "
                    f"market_price={pos.market_price:.2f} market_value={pos.market_value:.2f} "
                    f"unrealized_pnl={pos.unrealized_pnl:.2f} exposure={exposure:.2f}%"
                )
        else:
            lines.append("- 当前持仓：空仓")
        lines.extend([
            "",
            "策略候选摘要：",
        ])

        symbol_name_map: dict[str, str] = {}
        for strategy_name, items in strategy_recommendations.items():
            symbols = [item.symbol for item in items]
            avg_score = sum(float(item.score) for item in items) / max(len(items), 1)
            lines.append(f"- {strategy_name}: symbols={symbols}, avg_score={avg_score:.4f}")
            for item in items:
                symbol_name_map[item.symbol] = item.name
                score_map = item.strategy_scores or {}
                lines.append(
                    f"  · {item.symbol} {item.name} price={item.price:.2f} score={item.score:.4f} "
                    f"momentum={float(score_map.get('momentum', 0)):.4f} "
                    f"mean_reversion={float(score_map.get('mean_reversion', 0)):.4f} "
                    f"quality_news={float(score_map.get('quality_news', 0)):.4f}"
                )
                factor = item.factors or {}
                lines.append(
                    "    factors: "
                    f"ret_5d={factor.get('ret_5d')} ret_20d={factor.get('ret_20d')} "
                    f"rsi_14={factor.get('rsi_14')} volatility_20d={factor.get('volatility_20d')} "
                    f"drawdown_60d={factor.get('drawdown_60d')} volume_ratio={factor.get('volume_ratio')}"
                )
                if not compact:
                    lines.append(f"    reason: {item.reason}")

        lines.append("")
        lines.append("候选股票详细资料（指标 + 历史K线）：")
        for symbol, name in symbol_name_map.items():
            analysis = self.ranking_service.analyze_stock(symbol=symbol, name=name)
            quote = self.market_data_service.get_realtime_snapshot(symbol)
            history = self.market_data_service.get_history(symbol=symbol, limit=60).tail(history_limit)
            lines.append(
                f"[{symbol} {name}] latest price={_safe_float(quote.get('price')):.2f} "
                f"change_pct={_safe_float(quote.get('change_pct')):.2f}% "
                f"open={_safe_float(quote.get('open')):.2f} high={_safe_float(quote.get('high')):.2f} "
                f"low={_safe_float(quote.get('low')):.2f} prev_close={_safe_float(quote.get('prev_close')):.2f}"
            )
            lines.append(
                "  analysis_scores: "
                f"total={analysis.total_score:.4f} momentum={analysis.momentum_score:.4f} "
                f"mean_reversion={analysis.mean_reversion_score:.4f} quality={analysis.quality_score:.4f} "
                f"news={analysis.news_score:.4f} risk={analysis.risk_score:.4f}"
            )
            lines.append(f"  analysis_reasons: {' | '.join(analysis.reasons[:reason_limit])}")
            lines.append(f"  history_last_{history_limit}:")
            for row in history.itertuples(index=False):
                day = str(row.date)[:10]
                lines.append(
                    f"    {day} O={float(row.open):.2f} H={float(row.high):.2f} "
                    f"L={float(row.low):.2f} C={float(row.close):.2f} V={float(row.volume):.0f}"
                )
            lines.append("")

        lines.append("请严格按以下格式输出，不要输出其他内容：")
        lines.append("FINAL_DECISION=<EXECUTE|NO_TRADE>")
        lines.append("FINAL_STRATEGY=<momentum|mean_reversion|quality_news>")
        lines.append("REASON=<用2-5句说明为何选择该决策，并指出主要风险>")
        lines.append("如果 FINAL_DECISION=NO_TRADE，也必须给出 FINAL_STRATEGY（可作为下一轮观察策略）。")
        return "\n".join(lines)

    def _pick_best_by_avg_score(self, strategy_recommendations: dict[str, list]) -> str:
        best_name = "ensemble"
        best_score = float("-inf")
        for name, items in strategy_recommendations.items():
            if not items:
                continue
            avg_score = sum(float(item.score) for item in items) / max(len(items), 1)
            if avg_score > best_score:
                best_score = avg_score
                best_name = name
        return best_name

    def _extract_strategy_name(self, text: str) -> str:
        normalized = (text or "").lower()
        explicit = re.search(r"final_strategy\s*=\s*(momentum|mean_reversion|quality_news)", normalized)
        if explicit:
            return explicit.group(1)
        if "均值回归" in (text or ""):
            return "mean_reversion"
        if "质量新闻" in (text or "") or "新闻质量" in (text or ""):
            return "quality_news"
        if "动量" in (text or ""):
            return "momentum"
        for name in ("mean_reversion", "quality_news", "momentum"):
            if name in normalized:
                return name
        return ""

    def _extract_trade_decision(self, text: str) -> str:
        normalized = (text or "").upper()
        explicit = re.search(r"FINAL_DECISION\s*=\s*(EXECUTE|NO_TRADE)", normalized)
        if explicit:
            return explicit.group(1)
        if "NO_TRADE" in normalized or "不买" in (text or ""):
            return "NO_TRADE"
        return "EXECUTE"

    def _format_ollama_debug(self, result: dict) -> str:
        raw = str(result.get("response", "") or "").strip()
        error = str(result.get("error", "") or "").strip()
        response_text = str(result.get("response_text", "") or "").strip()
        status = result.get("status_code")
        parts = []
        if status is not None:
            parts.append(f"Ollama状态码: {status}")
        if error:
            parts.append(f"Ollama错误: {error}")
        if raw:
            parts.append(f"Ollama原始输出:\n{raw}")
        if response_text and response_text != raw:
            parts.append(f"Ollama响应体:\n{response_text}")
        if not parts:
            parts.append("Ollama原始输出为空。")
        return "\n".join(parts)

    def _is_ollama_timeout(self, result: dict) -> bool:
        error = str(result.get("error", "") or "").lower()
        status = result.get("status_code")
        if status in {408, 504}:
            return True
        return any(token in error for token in ("timed out", "timeout", "read timeout"))

    def _merge_ollama_debug(self, first: dict, retry: dict) -> str:
        return "\n\n".join([
            "[首次调用]",
            self._format_ollama_debug(first),
            "[超时后重试: 精简提示词]",
            self._format_ollama_debug(retry),
        ])

    def list_orders(self, mode: str = "paper", limit: int = 50) -> list[OrderResult]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, mode, symbol, name, side, quantity, price, notional, status, reason, created_at
                FROM orders
                WHERE mode = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (mode, limit),
            ).fetchall()
        return [
            OrderResult(
                id=int(row["id"]),
                mode=row["mode"],
                symbol=row["symbol"],
                name=row["name"],
                side=row["side"],
                quantity=int(row["quantity"]),
                price=round(float(row["price"]), 2),
                notional=round(float(row["notional"]), 2),
                status=row["status"],
                reason=row["reason"] or "",
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def _apply_buy(self, conn, mode: str, symbol: str, name: str, quantity: int, price: float, cash: float, now: str) -> None:
        position_row = conn.execute(
            "SELECT quantity, avg_cost FROM positions WHERE mode = ? AND symbol = ?",
            (mode, symbol),
        ).fetchone()
        if position_row is None:
            conn.execute(
                """
                INSERT INTO positions(mode, symbol, name, quantity, avg_cost, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (mode, symbol, name, quantity, price, now),
            )
        else:
            old_qty = int(position_row["quantity"])
            old_cost = float(position_row["avg_cost"])
            new_qty = old_qty + quantity
            new_cost = ((old_qty * old_cost) + (quantity * price)) / new_qty
            conn.execute(
                """
                UPDATE positions
                SET quantity = ?, avg_cost = ?, updated_at = ?, name = ?
                WHERE mode = ? AND symbol = ?
                """,
                (new_qty, round(new_cost, 4), now, name, mode, symbol),
            )

        conn.execute(
            "UPDATE accounts SET cash = ?, updated_at = ? WHERE mode = ?",
            (round(cash - (quantity * price), 2), now, mode),
        )

    def _apply_sell(self, conn, mode: str, symbol: str, quantity: int, price: float, cash: float, now: str) -> None:
        row = conn.execute(
            "SELECT quantity FROM positions WHERE mode = ? AND symbol = ?",
            (mode, symbol),
        ).fetchone()
        old_qty = int(row["quantity"])
        new_qty = old_qty - quantity
        if new_qty <= 0:
            conn.execute("DELETE FROM positions WHERE mode = ? AND symbol = ?", (mode, symbol))
        else:
            conn.execute(
                "UPDATE positions SET quantity = ?, updated_at = ? WHERE mode = ? AND symbol = ?",
                (new_qty, now, mode, symbol),
            )
        conn.execute(
            "UPDATE accounts SET cash = ?, updated_at = ? WHERE mode = ?",
            (round(cash + (quantity * price), 2), now, mode),
        )

    def _record_rejected_order(
        self,
        mode: str,
        symbol: str,
        name: str,
        side: str,
        quantity: int,
        price: float,
        reason: str,
    ) -> OrderResult:
        with get_connection() as conn:
            return self._insert_order(
                conn=conn,
                mode=mode,
                symbol=symbol,
                name=name,
                side=side,
                quantity=quantity,
                price=price,
                notional=round(price * quantity, 2),
                status="rejected",
                reason=reason,
                created_at=utc_now(),
            )

    def _insert_order(
        self,
        conn,
        mode: str,
        symbol: str,
        name: str,
        side: str,
        quantity: int,
        price: float,
        notional: float,
        status: str,
        reason: str,
        created_at: str,
    ) -> OrderResult:
        cursor = conn.execute(
            """
            INSERT INTO orders(mode, symbol, name, side, quantity, price, notional, status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mode, symbol, name, side, quantity, price, notional, status, reason, created_at),
        )
        return OrderResult(
            id=int(cursor.lastrowid),
            mode=mode,
            symbol=symbol,
            name=name,
            side=side,
            quantity=quantity,
            price=round(price, 2),
            notional=round(notional, 2),
            status=status,
            reason=reason,
            created_at=created_at,
        )
