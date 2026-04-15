from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from ..config import settings
from ..schemas import StockInfo


SAMPLE_UNIVERSE: list[dict[str, str]] = [
    {"symbol": "000001", "name": "平安银行"},
    {"symbol": "000333", "name": "美的集团"},
    {"symbol": "000651", "name": "格力电器"},
    {"symbol": "300059", "name": "东方财富"},
    {"symbol": "300750", "name": "宁德时代"},
    {"symbol": "600030", "name": "中信证券"},
    {"symbol": "600036", "name": "招商银行"},
    {"symbol": "600276", "name": "恒瑞医药"},
    {"symbol": "600519", "name": "贵州茅台"},
    {"symbol": "601318", "name": "中国平安"},
    {"symbol": "603259", "name": "药明康德"},
    {"symbol": "688981", "name": "中芯国际"},
]

FEATURED_UNIVERSE = [
    "000001",
    "000333",
    "000651",
    "002594",
    "300059",
    "300750",
    "600030",
    "600036",
    "600276",
    "600519",
    "601318",
    "601899",
    "603259",
    "688981",
]


@dataclass(slots=True)
class TimedCacheItem:
    value: Any
    expires_at: float


def _digits_only(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-6:] if digits else value.strip()


class MarketDataService:
    def __init__(self) -> None:
        self._akshare = None
        self._cache: dict[str, TimedCacheItem] = {}
        self._directory_cache_path = settings.runtime_dir / "stock_directory.csv"

    def clear_cache(self) -> None:
        self._cache.clear()

    def _load_akshare(self):
        if self._akshare is not None:
            return self._akshare
        try:
            import akshare as ak  # type: ignore
        except Exception:
            ak = None
        self._akshare = ak
        return self._akshare

    def _get_cached(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if item is None:
            return None
        if item.expires_at < time.time():
            self._cache.pop(key, None)
            return None
        return item.value

    def _set_cached(self, key: str, value: Any, ttl_seconds: int) -> Any:
        self._cache[key] = TimedCacheItem(value=value, expires_at=time.time() + ttl_seconds)
        return value

    def default_universe(self, limit: int = 10) -> list[StockInfo]:
        return [StockInfo(**item) for item in SAMPLE_UNIVERSE[:limit]]

    def get_stock_directory(self) -> pd.DataFrame:
        cached = self._get_cached("stock_directory")
        if cached is not None:
            return cached

        df = self._fetch_stock_directory_via_akshare()
        if df.empty:
            df = self._load_stock_directory_from_disk()
        else:
            self._save_stock_directory_to_disk(df)

        if df.empty:
            df = pd.DataFrame(SAMPLE_UNIVERSE)

        df = df.copy()
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["name"] = df["name"].astype(str).str.strip()
        df = df[df["symbol"].str.fullmatch(r"\d{6}", na=False)].drop_duplicates(subset=["symbol"])
        df = df.sort_values("symbol").reset_index(drop=True)
        return self._set_cached("stock_directory", df, ttl_seconds=60 * 60 * 12)

    def _fetch_stock_directory_via_akshare(self) -> pd.DataFrame:
        ak = self._load_akshare()
        if ak is None:
            return pd.DataFrame()
        try:
            df = ak.stock_info_a_code_name()
        except Exception:
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()

        code_col = self._pick_first_existing_column(df, ["code", "代码"])
        name_col = self._pick_first_existing_column(df, ["name", "名称"])
        if code_col is None or name_col is None:
            return pd.DataFrame()

        out = df[[code_col, name_col]].copy()
        out.columns = ["symbol", "name"]
        return out

    def _load_stock_directory_from_disk(self) -> pd.DataFrame:
        path = self._directory_cache_path
        if not path.exists():
            return pd.DataFrame()
        try:
            return pd.read_csv(path, dtype=str)
        except Exception:
            return pd.DataFrame()

    def _save_stock_directory_to_disk(self, df: pd.DataFrame) -> None:
        try:
            df.to_csv(self._directory_cache_path, index=False, encoding="utf-8-sig")
        except Exception:
            pass

    def _pick_first_existing_column(self, df: pd.DataFrame, candidates: list[str]) -> str | None:
        return next((col for col in candidates if col in df.columns), None)

    def search_stocks(self, query: str = "", limit: int = 10) -> list[StockInfo]:
        directory = self.get_stock_directory()
        normalized = (query or "").strip()
        if not normalized:
            working = directory.head(limit)
        else:
            mask = directory["symbol"].str.contains(normalized, regex=False, na=False) | directory["name"].str.contains(
                normalized,
                regex=False,
                na=False,
            )
            candidates = directory[mask].copy()
            if not candidates.empty:
                candidates["score"] = (
                    (candidates["symbol"] == normalized).astype(int) * 1000
                    + candidates["symbol"].str.startswith(normalized, na=False).astype(int) * 300
                    + candidates["name"].str.startswith(normalized, na=False).astype(int) * 220
                    + candidates["name"].str.contains(normalized, regex=False, na=False).astype(int) * 100
                    + candidates["symbol"].str.contains(normalized, regex=False, na=False).astype(int) * 80
                    - candidates["symbol"].str.len().fillna(6)
                )
                working = candidates.sort_values(["score", "symbol"], ascending=[False, True]).head(limit)
            else:
                working = candidates

        if working.empty:
            fallback = []
            for item in SAMPLE_UNIVERSE:
                if not normalized or normalized in item["symbol"] or normalized in item["name"]:
                    fallback.append(StockInfo(**item))
            return fallback[:limit]

        return [StockInfo(symbol=row.symbol, name=row.name, market="CN") for row in working.itertuples(index=False)]

    def get_hot_stocks(self, limit: int = 10) -> list[dict[str, Any]]:
        cached = self._get_cached("hot_stocks")
        if cached is not None:
            return cached[:limit]

        quotes = self.get_realtime_quotes(FEATURED_UNIVERSE[: max(limit, 10)])
        results: list[dict[str, Any]] = []
        for item in quotes:
            results.append(
                {
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "price": item["price"],
                    "change_pct": item["change_pct"],
                    "turnover": item.get("turnover", 0.0),
                    "source": item.get("source", "tencent"),
                }
            )

        if not results:
            for item in SAMPLE_UNIVERSE[:limit]:
                price = self.get_latest_price(item["symbol"])
                results.append(
                    {
                        "symbol": item["symbol"],
                        "name": item["name"],
                        "price": round(price, 2),
                        "change_pct": 0.0,
                        "turnover": 0.0,
                        "source": "fallback",
                    }
                )

        results.sort(key=lambda item: (abs(float(item.get("change_pct", 0.0))), float(item.get("turnover", 0.0))), reverse=True)
        for idx, item in enumerate(results, start=1):
            item["rank"] = idx
        return self._set_cached("hot_stocks", results, ttl_seconds=30)[:limit]

    def get_realtime_snapshot(self, symbol: str) -> dict[str, Any]:
        normalized = _digits_only(symbol)
        cache_key = f"realtime:{normalized}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        realtime = self._fetch_tencent_realtime([normalized]).get(normalized)
        if realtime is None:
            context = self.build_symbol_context(normalized)
            realtime = {
                "symbol": normalized,
                "name": context["name"],
                "price": context["latest_price"],
                "change_pct": context["change_pct"],
                "open": context["latest_price"],
                "high": context["latest_price"],
                "low": context["latest_price"],
                "prev_close": context["latest_price"],
                "turnover": 0.0,
                "volume": 0.0,
                "source": "fallback",
                "timestamp": "",
            }
        return self._set_cached(cache_key, realtime, ttl_seconds=5)

    def get_realtime_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        normalized_symbols: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            normalized = _digits_only(symbol)
            if normalized and normalized not in seen:
                normalized_symbols.append(normalized)
                seen.add(normalized)

        if not normalized_symbols:
            return []

        mapping: dict[str, dict[str, Any]] = {}
        for start in range(0, len(normalized_symbols), 60):
            batch = normalized_symbols[start : start + 60]
            mapping.update(self._fetch_tencent_realtime(batch))

        results = []
        for symbol in normalized_symbols:
            quote = mapping.get(symbol)
            if quote is None:
                quote = self.get_realtime_snapshot(symbol)
            results.append(quote)
        return results

    def get_realtime_market_page(self, query: str = "", page: int = 1, page_size: int = 50) -> dict[str, Any]:
        directory = self.get_stock_directory()
        normalized = (query or "").strip()
        if normalized:
            mask = directory["symbol"].str.contains(normalized, regex=False, na=False) | directory["name"].str.contains(
                normalized,
                regex=False,
                na=False,
            )
            working = directory[mask].copy()
        else:
            working = directory.copy()

        total = int(len(working))
        page = max(page, 1)
        page_size = max(min(page_size, 100), 10)
        start = (page - 1) * page_size
        end = start + page_size
        page_df = working.iloc[start:end].copy()

        symbols = page_df["symbol"].astype(str).tolist()
        quote_map = {item["symbol"]: item for item in self.get_realtime_quotes(symbols)}
        items = []
        for row in page_df.itertuples(index=False):
            quote = quote_map.get(row.symbol, {})
            items.append(
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "price": quote.get("price", 0.0),
                    "change_pct": quote.get("change_pct", 0.0),
                    "open": quote.get("open", 0.0),
                    "high": quote.get("high", 0.0),
                    "low": quote.get("low", 0.0),
                    "prev_close": quote.get("prev_close", 0.0),
                    "turnover": quote.get("turnover", 0.0),
                    "volume": quote.get("volume", 0.0),
                    "source": quote.get("source", "fallback"),
                    "timestamp": quote.get("timestamp", ""),
                }
            )

        return {
            "query": normalized,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
        }

    def _to_tencent_symbol(self, symbol: str) -> str:
        normalized = _digits_only(symbol)
        if normalized.startswith(("5", "6", "9")):
            return f"sh{normalized}"
        return f"sz{normalized}"

    def _fetch_tencent_realtime(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}

        codes = ",".join(self._to_tencent_symbol(symbol) for symbol in symbols)
        try:
            response = requests.get(f"http://qt.gtimg.cn/q={codes}", timeout=15)
            response.raise_for_status()
            response.encoding = "gbk"
        except Exception:
            return {}

        mapping: dict[str, dict[str, Any]] = {}
        for raw_line in response.text.strip().split(";"):
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            _, right = line.split("=", 1)
            payload = right.strip().strip('"')
            parts = payload.split("~")
            if len(parts) < 38:
                continue

            symbol = str(parts[2]).zfill(6)
            try:
                price = float(parts[3] or 0.0)
                prev_close = float(parts[4] or 0.0)
                open_price = float(parts[5] or 0.0)
                volume = float(parts[6] or 0.0)
                high = float(parts[33] or 0.0)
                low = float(parts[34] or 0.0)
                turnover = float(parts[37] or 0.0)
                change_pct = float(parts[32] or 0.0)
            except Exception:
                continue

            mapping[symbol] = {
                "symbol": symbol,
                "name": parts[1] or self.resolve_name(symbol),
                "price": round(price, 2),
                "change_pct": round(change_pct, 2),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "prev_close": round(prev_close, 2),
                "turnover": round(turnover, 2),
                "volume": volume,
                "source": "tencent",
                "timestamp": parts[30] if len(parts) > 30 else "",
            }
        return mapping

    def get_market_overview(self) -> dict[str, Any]:
        hot_stocks = self.get_hot_stocks(limit=8)
        gainers = sorted(hot_stocks, key=lambda item: item.get("change_pct", 0.0), reverse=True)[:3]
        losers = sorted(hot_stocks, key=lambda item: item.get("change_pct", 0.0))[:3]
        indexes = self.get_realtime_quotes(["000001", "399001", "399006"])
        return {
            "universe_size": int(len(self.get_stock_directory())),
            "hot_count": len(hot_stocks),
            "gainers": gainers,
            "losers": losers,
            "hot_stocks": hot_stocks,
            "indexes": indexes,
        }

    def get_history(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        limit: int = 180,
    ) -> pd.DataFrame:
        normalized_symbol = _digits_only(symbol)
        start = start or (date.today() - timedelta(days=365))
        end = end or date.today()
        cache_key = f"history:{normalized_symbol}:{start:%Y%m%d}:{end:%Y%m%d}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached.tail(limit).reset_index(drop=True)

        live_df = self._history_via_akshare(symbol=normalized_symbol, start=start, end=end)
        if live_df.empty:
            live_df = self._build_demo_history(normalized_symbol, start=start, end=end)

        self._set_cached(cache_key, live_df, ttl_seconds=60 * 10)
        return live_df.tail(limit).reset_index(drop=True)

    def _history_via_akshare(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        ak = self._load_akshare()
        if ak is None:
            return pd.DataFrame()
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception:
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        date_col = self._pick_first_existing_column(df, ["日期"])
        open_col = self._pick_first_existing_column(df, ["开盘"])
        close_col = self._pick_first_existing_column(df, ["收盘"])
        high_col = self._pick_first_existing_column(df, ["最高"])
        low_col = self._pick_first_existing_column(df, ["最低"])
        volume_col = self._pick_first_existing_column(df, ["成交量", "成交量(手)"])
        required = [date_col, open_col, close_col, high_col, low_col, volume_col]
        if any(col is None for col in required):
            return pd.DataFrame()

        normalized = df[[date_col, open_col, close_col, high_col, low_col, volume_col]].copy()
        normalized.columns = ["date", "open", "close", "high", "low", "volume"]
        normalized["date"] = pd.to_datetime(normalized["date"])
        for col in ["open", "close", "high", "low", "volume"]:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
        normalized = normalized.dropna().sort_values("date").reset_index(drop=True)
        normalized["source"] = "akshare"
        return normalized

    def _build_demo_history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        dates = pd.date_range(start=start, end=end, freq="B")
        if len(dates) == 0:
            dates = pd.date_range(end=end, periods=120, freq="B")

        seed = int(_digits_only(symbol) or "1")
        rng = np.random.default_rng(seed)
        base_price = 10 + (seed % 200) / 10
        drift = rng.normal(loc=0.0008, scale=0.002, size=len(dates))
        noise = rng.normal(loc=0.0, scale=0.015, size=len(dates))
        close = base_price * np.cumprod(1 + drift + noise)
        open_ = close * (1 + rng.normal(0, 0.005, size=len(dates)))
        high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.02, size=len(dates)))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.02, size=len(dates)))
        volume = rng.integers(1_000_000, 15_000_000, size=len(dates))

        return pd.DataFrame(
            {
                "date": dates,
                "open": np.round(open_, 2),
                "close": np.round(close, 2),
                "high": np.round(high, 2),
                "low": np.round(low, 2),
                "volume": volume,
                "source": "demo",
            }
        )

    def get_latest_price(self, symbol: str) -> float:
        realtime = self.get_realtime_snapshot(symbol)
        price = float(realtime.get("price", 0.0) or 0.0)
        if price > 0:
            return price
        history = self.get_history(symbol=symbol, limit=5)
        if history.empty:
            return 0.0
        return float(history.iloc[-1]["close"])

    def build_symbol_context(self, symbol: str, name: str | None = None) -> dict[str, Any]:
        history = self.get_history(symbol=symbol, limit=120)
        latest_price = float(history.iloc[-1]["close"]) if not history.empty else 0.0
        prev_close = float(history.iloc[-2]["close"]) if len(history) >= 2 else latest_price
        change_pct = (latest_price / prev_close - 1) * 100 if prev_close else 0.0
        ma5 = float(history["close"].tail(5).mean()) if len(history) >= 5 else latest_price
        ma20 = float(history["close"].tail(20).mean()) if len(history) >= 20 else latest_price
        ret20 = float(history["close"].iloc[-1] / history["close"].iloc[-20] - 1) * 100 if len(history) >= 20 else 0.0
        return {
            "symbol": _digits_only(symbol),
            "name": name or self.resolve_name(symbol),
            "latest_price": round(latest_price, 2),
            "change_pct": round(change_pct, 2),
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "return_20d": round(ret20, 2),
            "history_source": history.iloc[-1]["source"] if not history.empty else "unknown",
        }

    def resolve_name(self, symbol: str) -> str:
        normalized = _digits_only(symbol)
        directory = self.get_stock_directory()
        matched = directory[directory["symbol"] == normalized]
        if not matched.empty:
            return str(matched.iloc[0]["name"])
        for item in SAMPLE_UNIVERSE:
            if item["symbol"] == normalized:
                return item["name"]
        return normalized
