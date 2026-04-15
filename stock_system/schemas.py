from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StockInfo(BaseModel):
    symbol: str
    name: str
    market: str = "CN"


class RecommendationItem(BaseModel):
    symbol: str
    name: str
    score: float
    price: float
    reason: str
    factors: dict[str, Any] = Field(default_factory=dict)
    strategy_scores: dict[str, float] = Field(default_factory=dict)
    related_contexts: list[dict[str, Any]] = Field(default_factory=list)


class ResearchQueryRequest(BaseModel):
    query: str
    symbol: str | None = None
    top_k: int = 3


class ResearchQueryResponse(BaseModel):
    answer: str
    sources: list[str]
    contexts: list[dict[str, Any]]
    retrieval: dict[str, Any] = Field(default_factory=dict)


class TradeOrderRequest(BaseModel):
    mode: str = "paper"
    symbol: str
    name: str
    side: str = "BUY"
    quantity: int
    price: float | None = None
    reason: str = ""


class AutoTradeRequest(BaseModel):
    mode: str = "paper"
    top_n: int = 3
    strategy: str | None = None


class FeishuWebhookRequest(BaseModel):
    text: str


class FeishuAppMessageRequest(BaseModel):
    receive_id: str
    text: str
    receive_id_type: str = "chat_id"


class PositionItem(BaseModel):
    mode: str
    symbol: str
    name: str
    quantity: int
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float


class AccountSnapshot(BaseModel):
    mode: str
    cash: float
    positions: list[PositionItem]
    total_market_value: float
    total_equity: float


class OrderResult(BaseModel):
    id: int
    mode: str
    symbol: str
    name: str
    side: str
    quantity: int
    price: float
    notional: float
    status: str
    reason: str
    created_at: str


class SystemSettingsResponse(BaseModel):
    paper_initial_cash: float
    auto_trade_budget_ratio: float
    default_auto_trade_strategy: str
    auto_trade_refresh_seconds: int
    enable_ollama_decision: bool
    ollama_timeout_seconds: int
    research_top_k: int
    market_page_size: int
    rag_retrieval_mode: str
    updated_at: str


class SystemSettingsUpdateRequest(BaseModel):
    paper_initial_cash: float
    auto_trade_budget_ratio: float
    default_auto_trade_strategy: str
    auto_trade_refresh_seconds: int
    enable_ollama_decision: bool
    ollama_timeout_seconds: int = 180
    research_top_k: int
    market_page_size: int
    rag_retrieval_mode: str = "hybrid"
    reset_paper_account: bool = False


class StockAnalysisResponse(BaseModel):
    symbol: str
    name: str
    price: float
    source: str
    total_score: float
    momentum_score: float
    mean_reversion_score: float
    quality_score: float
    news_score: float
    risk_score: float
    strategy_scores: dict[str, float] = Field(default_factory=dict)
    factors: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    related_contexts: list[dict[str, Any]] = Field(default_factory=list)
