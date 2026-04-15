from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    app_env: str
    root_dir: Path
    docs_dir: Path
    research_dir: Path
    runtime_dir: Path
    database_path: Path
    paper_initial_cash: float
    live_initial_cash: float
    auto_trade_budget_ratio: float
    ollama_base_url: str
    ollama_model: str
    rag_embedding_model: str
    rag_rerank_model: str
    rag_vector_store: str
    rag_enable_dense: bool
    rag_enable_sparse: bool
    rag_enable_rerank: bool
    rag_dense_top_k: int
    rag_sparse_top_k: int
    rag_rerank_top_k: int
    rag_chunk_max_chars: int
    rag_chunk_overlap_chars: int
    rag_embedding_batch_size: int
    feishu_webhook_url: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verify_token: str
    enable_live_trading: bool

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        root_dir = Path(__file__).resolve().parent.parent
        runtime_dir = root_dir / "runtime"
        docs_dir = root_dir / "docs"
        research_dir = root_dir / "research-report"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        docs_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            app_env=os.getenv("APP_ENV", "dev"),
            root_dir=root_dir,
            docs_dir=docs_dir,
            research_dir=research_dir,
            runtime_dir=runtime_dir,
            database_path=runtime_dir / "stock_system.db",
            paper_initial_cash=float(os.getenv("PAPER_INITIAL_CASH", "100000.0")),
            live_initial_cash=float(os.getenv("LIVE_INITIAL_CASH", "0.0")),
            auto_trade_budget_ratio=float(os.getenv("AUTO_TRADE_BUDGET_RATIO", "0.2")),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "").strip(),
            rag_embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-m3").strip(),
            rag_rerank_model=os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3").strip(),
            rag_vector_store=os.getenv("RAG_VECTOR_STORE", "faiss").strip().lower() or "faiss",
            rag_enable_dense=_get_bool("RAG_ENABLE_DENSE", True),
            rag_enable_sparse=_get_bool("RAG_ENABLE_SPARSE", True),
            rag_enable_rerank=_get_bool("RAG_ENABLE_RERANK", True),
            rag_dense_top_k=max(5, int(float(os.getenv("RAG_DENSE_TOP_K", "40")))),
            rag_sparse_top_k=max(5, int(float(os.getenv("RAG_SPARSE_TOP_K", "40")))),
            rag_rerank_top_k=max(5, int(float(os.getenv("RAG_RERANK_TOP_K", "20")))),
            rag_chunk_max_chars=max(200, int(float(os.getenv("RAG_CHUNK_MAX_CHARS", "700")))),
            rag_chunk_overlap_chars=max(0, int(float(os.getenv("RAG_CHUNK_OVERLAP_CHARS", "120")))),
            rag_embedding_batch_size=max(1, int(float(os.getenv("RAG_EMBEDDING_BATCH_SIZE", "32")))),
            feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
            feishu_app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            feishu_verify_token=os.getenv("FEISHU_VERIFY_TOKEN", "").strip(),
            enable_live_trading=_get_bool("ENABLE_LIVE_TRADING", False),
        )


settings = Settings.load()
