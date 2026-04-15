from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..schemas import RecommendationItem, StockAnalysisResponse
from .market_data import MarketDataService
from .research import ResearchService


POSITIVE_TERMS = ["增长", "回暖", "创新高", "超预期", "景气", "扩张", "改善", "利好", "订单", "突破", "上调"]
NEGATIVE_TERMS = ["下滑", "承压", "风险", "减值", "亏损", "回撤", "利空", "诉讼", "波动", "下降", "放缓"]


def clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


@dataclass(slots=True)
class TimedCacheItem:
    value: Any
    expires_at: float


class RankingService:
    def __init__(self, market_data_service: MarketDataService, research_service: ResearchService) -> None:
        self.market_data_service = market_data_service
        self.research_service = research_service
        self._analysis_cache: dict[str, TimedCacheItem] = {}
        self._news_cache: dict[str, TimedCacheItem] = {}

    def _get_cached(self, cache: dict[str, TimedCacheItem], key: str) -> Any | None:
        item = cache.get(key)
        if item is None:
            return None
        if item.expires_at < time.time():
            cache.pop(key, None)
            return None
        return item.value

    def _set_cached(self, cache: dict[str, TimedCacheItem], key: str, value: Any, ttl_seconds: int) -> Any:
        cache[key] = TimedCacheItem(value=value, expires_at=time.time() + ttl_seconds)
        return value

    def recommend(self, limit: int = 5, query: str = "", strategy: str = "ensemble") -> list[RecommendationItem]:
        universe = self._build_candidate_universe(limit=limit, query=query)
        ranked: list[RecommendationItem] = []
        for stock in universe:
            analysis = self.analyze_stock(stock.symbol, stock.name)
            score = analysis.strategy_scores.get(strategy, analysis.total_score)
            reason = "；".join(analysis.reasons[:3])
            ranked.append(
                RecommendationItem(
                    symbol=analysis.symbol,
                    name=analysis.name,
                    score=round(score, 4),
                    price=analysis.price,
                    reason=reason,
                    factors=analysis.factors,
                    strategy_scores=analysis.strategy_scores,
                    related_contexts=analysis.related_contexts,
                )
            )
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:limit]

    def analyze_stock(self, symbol: str, name: str | None = None) -> StockAnalysisResponse:
        cache_key = str(symbol or "").strip()
        cached = self._get_cached(self._analysis_cache, cache_key)
        if cached is not None:
            if name and name != cached.name:
                return cached.model_copy(update={"name": name}, deep=True)
            return cached.model_copy(deep=True)

        snapshot = self.market_data_service.get_realtime_snapshot(symbol)
        history = self.market_data_service.get_history(symbol=symbol, limit=180)
        close = history["close"].astype(float)
        volume = history["volume"].astype(float)

        ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0.0
        ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0.0
        ma_5 = float(close.tail(5).mean()) if len(close) >= 5 else float(close.iloc[-1])
        ma_20 = float(close.tail(20).mean()) if len(close) >= 20 else float(close.iloc[-1])
        ma_60 = float(close.tail(60).mean()) if len(close) >= 60 else ma_20
        volatility_20d = float(close.pct_change().dropna().tail(20).std()) if len(close) >= 21 else 0.0
        drawdown_60d = self._calc_drawdown(close.tail(60))
        rsi_14 = self._calc_rsi(close, period=14)
        volume_ratio = float(volume.iloc[-1] / volume.tail(20).mean()) if len(volume) >= 20 and volume.tail(20).mean() else 1.0
        price_vs_ma20 = float(close.iloc[-1] / ma_20 - 1) if ma_20 else 0.0
        trend_gap = float(ma_20 / ma_60 - 1) if ma_60 else 0.0

        momentum_score = clamp(50 + ret_20d * 450 + ret_5d * 180 + (12 if ma_5 >= ma_20 else -10) + trend_gap * 300)
        mean_reversion_score = clamp(55 + max(-price_vs_ma20, 0) * 500 + max(40 - rsi_14, 0) * 1.2 - ret_5d * 160)
        quality_score = clamp(52 + trend_gap * 380 - volatility_20d * 420 - abs(drawdown_60d) * 160 + volume_ratio * 8)
        risk_score = clamp(100 - volatility_20d * 700 - abs(drawdown_60d) * 220)

        news = self._score_news(symbol=symbol, name=name or snapshot["name"])
        news_score = news["score"]

        strategy_scores = {
            "momentum": round(clamp(momentum_score * 0.55 + news_score * 0.15 + risk_score * 0.30), 4),
            "mean_reversion": round(clamp(mean_reversion_score * 0.55 + quality_score * 0.15 + risk_score * 0.30), 4),
            "quality_news": round(clamp(quality_score * 0.45 + news_score * 0.30 + risk_score * 0.25), 4),
        }
        strategy_scores["ensemble"] = round(float(np.mean(list(strategy_scores.values()))), 4)
        strategy_scores["ensemble_rag"] = strategy_scores["ensemble"]

        reasons = [
            f"20日收益率 {ret_20d:.2%}",
            f"5日收益率 {ret_5d:.2%}",
            f"RSI14 {rsi_14:.2f}",
            f"波动率 {volatility_20d:.2%}",
            f"新闻/文档情绪得分 {news_score:.2f}",
        ]

        factors = {
            "ret_5d": round(ret_5d, 4),
            "ret_20d": round(ret_20d, 4),
            "ma_5": round(ma_5, 2),
            "ma_20": round(ma_20, 2),
            "ma_60": round(ma_60, 2),
            "rsi_14": round(rsi_14, 2),
            "volatility_20d": round(volatility_20d, 4),
            "drawdown_60d": round(drawdown_60d, 4),
            "volume_ratio": round(volume_ratio, 4),
            "price_vs_ma20": round(price_vs_ma20, 4),
            "trend_gap": round(trend_gap, 4),
            "news_summary": news["summary"],
            "history_source": str(history.iloc[-1]["source"]),
        }

        result = StockAnalysisResponse(
            symbol=snapshot["symbol"],
            name=name or snapshot["name"],
            price=float(snapshot["price"]),
            source=str(snapshot.get("source", "unknown")),
            total_score=round(strategy_scores["ensemble"], 4),
            momentum_score=round(momentum_score, 4),
            mean_reversion_score=round(mean_reversion_score, 4),
            quality_score=round(quality_score, 4),
            news_score=round(news_score, 4),
            risk_score=round(risk_score, 4),
            strategy_scores=strategy_scores,
            factors=factors,
            reasons=reasons,
            related_contexts=news["contexts"],
        )
        self._set_cached(self._analysis_cache, cache_key, result, ttl_seconds=35)
        return result.model_copy(deep=True)

    def summarize_universe(self, query: str = "", limit: int = 5, strategy: str = "ensemble") -> dict[str, Any]:
        recommendations = self.recommend(limit=limit, query=query, strategy=strategy)
        if not recommendations:
            return {"count": 0, "top_symbols": [], "avg_score": 0.0, "items": []}

        avg_score = float(np.mean([item.score for item in recommendations]))
        return {
            "count": len(recommendations),
            "top_symbols": [item.symbol for item in recommendations],
            "avg_score": round(avg_score, 4),
            "items": [item.model_dump() for item in recommendations],
        }

    def _build_candidate_universe(self, limit: int, query: str) -> list:
        if query.strip():
            return self.market_data_service.search_stocks(query=query, limit=max(limit * 4, 20))

        candidates = []
        seen = set()
        for stock in self.market_data_service.default_universe(limit=12):
            if stock.symbol not in seen:
                candidates.append(stock)
                seen.add(stock.symbol)
        for item in self.market_data_service.get_hot_stocks(limit=12):
            if item["symbol"] not in seen:
                candidates.append(self.market_data_service.default_universe(limit=1)[0].model_copy(update={"symbol": item["symbol"], "name": item["name"]}))
                seen.add(item["symbol"])
        return candidates[: max(limit * 4, 16)]

    def _calc_rsi(self, close, period: int = 14) -> float:
        if len(close) <= period:
            return 50.0
        delta = close.diff().dropna()
        gains = delta.clip(lower=0).tail(period)
        losses = -delta.clip(upper=0).tail(period)
        avg_gain = gains.mean()
        avg_loss = losses.mean()
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calc_drawdown(self, close) -> float:
        if len(close) <= 1:
            return 0.0
        rolling_max = close.cummax()
        drawdown = close / rolling_max - 1
        return float(drawdown.min())

    def _score_news(self, symbol: str, name: str) -> dict[str, Any]:
        cache_key = str(symbol or "").strip()
        cached = self._get_cached(self._news_cache, cache_key)
        if cached is not None:
            return dict(cached)

        query = f"{symbol} {name} 新闻 业绩 公告 研报"
        contexts = self.research_service.retrieve(query=query, symbol=symbol, top_k=4)
        if not contexts:
            empty_result = {"score": 50.0, "summary": "未检索到相关文档上下文", "contexts": []}
            self._set_cached(self._news_cache, cache_key, empty_result, ttl_seconds=60)
            return dict(empty_result)

        polarity = 0
        snippets = []
        for item in contexts:
            text = item["preview"]
            pos_hits = sum(1 for term in POSITIVE_TERMS if term in text)
            neg_hits = sum(1 for term in NEGATIVE_TERMS if term in text)
            polarity += pos_hits - neg_hits
            snippets.append(f"{item['source']} / {item['heading'] or item['title']}")

        score = clamp(50 + polarity * 8)
        summary = f"命中 {len(contexts)} 条上下文，相关材料：{'；'.join(snippets[:3])}"
        result = {"score": round(score, 4), "summary": summary, "contexts": contexts}
        self._set_cached(self._news_cache, cache_key, result, ttl_seconds=60 * 5)
        return dict(result)
