from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import requests

from ..config import settings
from ..db import get_connection

try:
    import faiss  # type: ignore
except Exception:
    faiss = None

try:
    from rank_bm25 import BM25Okapi  # type: ignore
except Exception:
    BM25Okapi = None

try:
    from sentence_transformers import CrossEncoder, SentenceTransformer  # type: ignore
except Exception:
    CrossEncoder = None
    SentenceTransformer = None


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{1,}|[A-Za-z0-9_]+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LEGAL_TERMS = (
    "法律",
    "法规",
    "法条",
    "民法",
    "刑法",
    "合同法",
    "劳动法",
    "行政法",
    "仲裁",
    "诉讼",
    "司法",
    "违法",
    "合规",
)


@dataclass(slots=True)
class DocumentChunk:
    source: str
    text: str
    chunk_id: str
    file_path: str
    title: str
    heading: str
    start_char: int
    end_char: int
    token_count: int


class ResearchService:
    def __init__(self) -> None:
        self.directories = [
            settings.research_dir,
            settings.root_dir / "knowledge-base",
            settings.docs_dir,
        ]
        overlap = min(settings.rag_chunk_overlap_chars, max(settings.rag_chunk_max_chars - 1, 0))
        self._chunk_config = {
            "mode": "section-window-overlap",
            "max_chars": settings.rag_chunk_max_chars,
            "overlap_chars": overlap,
            "tokenizer": "regex-zh-en+char-ngram",
        }
        self._retrieval_config = {
            "strategy": "hybrid_dense_sparse_rerank",
            "dense_top_k": settings.rag_dense_top_k,
            "sparse_top_k": settings.rag_sparse_top_k,
            "rerank_top_k": settings.rag_rerank_top_k,
            "enable_dense": bool(settings.rag_enable_dense),
            "enable_sparse": bool(settings.rag_enable_sparse),
            "enable_rerank": bool(settings.rag_enable_rerank),
            "rrf_k": 60.0,
        }
        self._chunks: list[DocumentChunk] | None = None
        self._index_lock = threading.Lock()
        self._index_built = False
        self._index_fingerprint = ""
        self._last_index_build = ""
        self._index_error = ""
        self._vector_store_runtime = "none"
        self._dense_embeddings: np.ndarray | None = None
        self._faiss_index: Any = None
        self._bm25: Any = None
        self._bm25_tokens: list[list[str]] = []
        self._embedder: Any = None
        self._reranker: Any = None
        self._retrieval_mode_cache = "hybrid"
        self._retrieval_mode_cache_expires_at = 0.0

    def describe_chunking(self) -> dict[str, Any]:
        status = self.index_status(lightweight=True)
        return {
            **self._chunk_config,
            "retrieval_mode": status["retrieval_mode"],
            "retrieval_strategy": status["effective_strategy"],
            "dense_top_k": self._retrieval_config["dense_top_k"],
            "sparse_top_k": self._retrieval_config["sparse_top_k"],
            "rerank_top_k": self._retrieval_config["rerank_top_k"],
            "enable_dense": self._retrieval_config["enable_dense"],
            "enable_sparse": self._retrieval_config["enable_sparse"],
            "enable_rerank": self._retrieval_config["enable_rerank"],
            "dense_ready": status["dense_ready"],
            "sparse_ready": status["sparse_ready"],
            "rerank_ready": status["rerank_ready"],
            "vector_store": status["vector_store"],
            "dense_backend": status["dense_backend"],
            "sparse_backend": status["sparse_backend"],
            "index_built": status["index_built"],
            "total_chunks": status["total_chunks"],
        }

    def index_status(self, lightweight: bool = False) -> dict[str, Any]:
        total_chunks = len(self._chunks) if self._chunks is not None else 0
        retrieval_mode = self._get_runtime_retrieval_mode()
        effective_strategy = self._effective_retrieval_strategy(retrieval_mode)
        sparse_backend = "bm25" if self._bm25 is not None else ("lexical_fallback" if self._retrieval_config["enable_sparse"] else "disabled")
        dense_backend = "faiss" if self._faiss_index is not None else ("numpy" if self._dense_embeddings is not None else "disabled")
        status = {
            "index_built": bool(self._index_built),
            "total_chunks": int(total_chunks),
            "retrieval_mode": retrieval_mode,
            "effective_strategy": effective_strategy,
            "embedding_model": settings.rag_embedding_model,
            "rerank_model": settings.rag_rerank_model,
            "vector_store": self._vector_store_runtime,
            "dense_ready": bool(self._dense_embeddings is not None),
            "sparse_ready": bool(self._retrieval_config["enable_sparse"]),
            "rerank_ready": bool(self._reranker is not None),
            "dense_backend": dense_backend,
            "sparse_backend": sparse_backend,
            "last_index_build": self._last_index_build,
            "index_error": self._index_error,
        }
        if not lightweight:
            status["config"] = {
                "chunk": dict(self._chunk_config),
                "retrieval": dict(self._retrieval_config),
            }
        return status

    def rebuild_index(self, force: bool = True) -> dict[str, Any]:
        self._ensure_indices(force=force)
        return self.index_status()

    def query(self, query: str, symbol: str | None = None, top_k: int = 3) -> dict[str, Any]:
        retrieval_mode = self._get_runtime_retrieval_mode()
        ranked = self.retrieve(query=query, symbol=symbol, top_k=top_k)
        answer = self._build_answer(query=query, symbol=symbol, ranked=ranked)
        return {
            "answer": answer,
            "sources": [item["source"] for item in ranked],
            "contexts": ranked,
            "retrieval": {
                "mode": retrieval_mode,
                "strategy": self._effective_retrieval_strategy(retrieval_mode),
                "chunking": self.describe_chunking(),
                "total_chunks": len(self._load_chunks()),
                "index_status": self.index_status(lightweight=True),
            },
        }

    def retrieve(self, query: str, symbol: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []
        retrieval_mode = self._get_runtime_retrieval_mode()
        if retrieval_mode == "lexical":
            return self._retrieve_lexical(query=normalized_query, symbol=symbol, top_k=top_k)

        chunks = self._load_chunks()
        if not chunks:
            return []
        self._ensure_indices(force=False)

        query_text = f"{normalized_query} {symbol or ''}".strip()
        legal_intent = self._is_legal_intent(normalized_query)
        top_k = max(1, min(int(top_k or 5), 30))

        candidates = self._collect_candidates(query_text=query_text, symbol=symbol, legal_intent=legal_intent)
        if not candidates:
            return []

        candidate_items = sorted(candidates.items(), key=lambda item: item[1]["base_score"], reverse=True)
        rerank_pool_size = min(max(self._retrieval_config["rerank_top_k"], top_k), len(candidate_items))
        rerank_pool = candidate_items[:rerank_pool_size]
        rerank_indices = [idx for idx, _ in rerank_pool]
        rerank_chunks = [chunks[idx] for idx in rerank_indices]
        rerank_scores = self._rerank(query_text, rerank_chunks)
        rerank_norm = self._normalize_array(rerank_scores)

        final_items: list[tuple[float, int, dict[str, float]]] = []
        for pos, (chunk_idx, signals) in enumerate(rerank_pool):
            final_score = (signals["base_score"] * 0.45) + (rerank_norm[pos] * 0.55)
            signals["rerank_raw"] = float(rerank_scores[pos])
            signals["rerank_norm"] = float(rerank_norm[pos])
            signals["final_score"] = float(final_score)
            final_items.append((final_score, chunk_idx, signals))

        final_items.sort(key=lambda item: item[0], reverse=True)
        selected = final_items[:top_k]
        return [
            self._chunk_to_context(
                chunk=chunks[idx],
                score=round(score, 4),
                rank=rank + 1,
                signals=signals,
            )
            for rank, (score, idx, signals) in enumerate(selected)
        ]

    def _retrieve_lexical(self, query: str, symbol: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        chunks = self._load_chunks()
        if not chunks:
            return []
        top_k = max(1, min(int(top_k or 5), 30))
        query_text = f"{query} {symbol or ''}".strip()
        query_tokens = self._tokenize(query_text)
        query_lower = query_text.lower()
        legal_intent = self._is_legal_intent(query)
        symbol_text = str(symbol or "").strip()

        scored: list[tuple[float, int, dict[str, float]]] = []
        for idx, chunk in enumerate(chunks):
            score = self._lexical_score(query_tokens, query_lower, chunk)
            heading_text = chunk.heading.lower()
            title_text = chunk.title.lower()
            path_text = chunk.file_path.lower()
            symbol_hit = 1.0 if symbol_text and symbol_text in chunk.text else 0.0
            heading_hit = 1.0 if any(token in heading_text for token in query_tokens if token) else 0.0
            title_hit = 1.0 if any(token in title_text for token in query_tokens if token) else 0.0
            path_hit = 1.0 if any(token in path_text for token in query_tokens if token) else 0.0
            legal_match = 1.0 if (legal_intent and self._contains_legal_term(chunk.text.lower(), heading_text, title_text, path_text)) else 0.0
            legal_penalty = -0.2 if (legal_intent and legal_match == 0.0) else 0.0
            final_score = score + (symbol_hit * 1.2) + (heading_hit * 0.5) + (title_hit * 0.4) + (path_hit * 0.2) + (legal_match * 0.5) + legal_penalty
            if final_score <= 0:
                continue
            signals = {
                "base_score": float(final_score),
                "dense_raw": 0.0,
                "sparse_raw": float(score),
                "dense_norm": 0.0,
                "sparse_norm": 0.0,
                "rerank_raw": 0.0,
                "rerank_norm": 0.0,
                "symbol_hit": symbol_hit,
                "legal_match": legal_match,
            }
            scored.append((float(final_score), idx, signals))

        if not scored:
            return []

        scored.sort(key=lambda item: item[0], reverse=True)
        sparse_norm = self._normalize_array([item[0] for item in scored[: max(top_k, 12)]])
        normalized_top = scored[: max(top_k, 12)]
        final_pack: list[tuple[float, int, dict[str, float]]] = []
        for pos, (score, idx, signals) in enumerate(normalized_top):
            signals["sparse_norm"] = float(sparse_norm[pos]) if pos < len(sparse_norm) else 0.0
            final_pack.append((score, idx, signals))

        return [
            self._chunk_to_context(
                chunk=chunks[idx],
                score=round(score, 4),
                rank=rank + 1,
                signals=signals,
            )
            for rank, (score, idx, signals) in enumerate(final_pack[:top_k])
        ]

    def _collect_candidates(self, query_text: str, symbol: str | None, legal_intent: bool) -> dict[int, dict[str, float]]:
        chunks = self._load_chunks()
        candidates: dict[int, dict[str, float]] = {}

        sparse_scores = np.zeros(len(chunks), dtype=np.float32)
        dense_scores = np.zeros(len(chunks), dtype=np.float32)
        has_sparse = False
        has_dense = False

        if self._retrieval_config["enable_sparse"]:
            sparse_scores = self._search_sparse(query_text)
            if np.any(np.isfinite(sparse_scores)) and np.max(sparse_scores) > 0:
                has_sparse = True
                for rank, idx in enumerate(self._top_indices(sparse_scores, self._retrieval_config["sparse_top_k"]), start=1):
                    score = float(sparse_scores[idx])
                    if score <= 0:
                        continue
                    signal = candidates.setdefault(int(idx), {})
                    signal["sparse_rank"] = float(rank)
                    signal["sparse_raw"] = score

        if self._retrieval_config["enable_dense"]:
            dense_scores = self._search_dense(query_text)
            if np.any(np.isfinite(dense_scores)):
                has_dense = True
                for rank, idx in enumerate(self._top_indices(dense_scores, self._retrieval_config["dense_top_k"]), start=1):
                    score = float(dense_scores[idx])
                    signal = candidates.setdefault(int(idx), {})
                    signal["dense_rank"] = float(rank)
                    signal["dense_raw"] = score

        if not candidates:
            return {}

        sparse_norm = self._normalize_array([candidates[idx].get("sparse_raw", 0.0) for idx in candidates])
        dense_norm = self._normalize_array([candidates[idx].get("dense_raw", 0.0) for idx in candidates])

        query_tokens = self._tokenize(query_text)
        symbol_text = str(symbol or "").strip()
        rrf_k = float(self._retrieval_config["rrf_k"])
        sparse_weight = 1.0 if has_sparse else 0.0
        dense_weight = 1.0 if has_dense else 0.0

        for pos, idx in enumerate(candidates):
            chunk = chunks[idx]
            signal = candidates[idx]
            sparse_rank = signal.get("sparse_rank")
            dense_rank = signal.get("dense_rank")
            rrf_sparse = (1.0 / (rrf_k + sparse_rank)) if sparse_rank else 0.0
            rrf_dense = (1.0 / (rrf_k + dense_rank)) if dense_rank else 0.0

            heading_text = chunk.heading.lower()
            title_text = chunk.title.lower()
            path_text = chunk.file_path.lower()
            symbol_hit = 1.0 if symbol_text and symbol_text in chunk.text else 0.0
            heading_hit = 1.0 if any(token in heading_text for token in query_tokens if token) else 0.0
            title_hit = 1.0 if any(token in title_text for token in query_tokens if token) else 0.0
            path_hit = 1.0 if any(token in path_text for token in query_tokens if token) else 0.0
            legal_match = 1.0 if (legal_intent and self._contains_legal_term(chunk.text.lower(), heading_text, title_text, path_text)) else 0.0
            legal_penalty = -0.12 if (legal_intent and legal_match == 0.0) else 0.0

            base_score = (
                (rrf_sparse * sparse_weight * 2.4)
                + (rrf_dense * dense_weight * 2.6)
                + (sparse_norm[pos] * 0.7)
                + (dense_norm[pos] * 0.9)
                + (symbol_hit * 0.24)
                + (heading_hit * 0.12)
                + (title_hit * 0.08)
                + (path_hit * 0.04)
                + (legal_match * 0.15)
                + legal_penalty
            )

            signal["sparse_norm"] = float(sparse_norm[pos])
            signal["dense_norm"] = float(dense_norm[pos])
            signal["symbol_hit"] = symbol_hit
            signal["heading_hit"] = heading_hit
            signal["title_hit"] = title_hit
            signal["path_hit"] = path_hit
            signal["legal_match"] = legal_match
            signal["base_score"] = float(base_score)

        return candidates

    def _search_sparse(self, query_text: str) -> np.ndarray:
        chunks = self._load_chunks()
        if not chunks:
            return np.array([], dtype=np.float32)

        if self._bm25 is not None:
            query_tokens = self._tokenize_for_bm25(query_text)
            if not query_tokens:
                return np.zeros(len(chunks), dtype=np.float32)
            scores = np.asarray(self._bm25.get_scores(query_tokens), dtype=np.float32)
            return scores

        fallback_scores = np.zeros(len(chunks), dtype=np.float32)
        query_tokens = self._tokenize(query_text)
        query_lower = query_text.lower()
        for idx, chunk in enumerate(chunks):
            fallback_scores[idx] = float(self._lexical_score(query_tokens, query_lower, chunk))
        return fallback_scores

    def _search_dense(self, query_text: str) -> np.ndarray:
        chunks = self._load_chunks()
        if not chunks or self._dense_embeddings is None:
            return np.zeros(len(chunks), dtype=np.float32)
        query_vector = self._encode_query(query_text)
        if query_vector is None:
            return np.zeros(len(chunks), dtype=np.float32)

        if self._faiss_index is not None and faiss is not None:
            search_k = min(self._retrieval_config["dense_top_k"], len(chunks))
            distances, indices = self._faiss_index.search(query_vector.reshape(1, -1).astype(np.float32), search_k)
            scores = np.full(len(chunks), -1.0, dtype=np.float32)
            for score, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue
                scores[int(idx)] = float(score)
            return scores

        return (self._dense_embeddings @ query_vector).astype(np.float32)

    def _rerank(self, query_text: str, chunks: list[DocumentChunk]) -> list[float]:
        if not chunks:
            return []

        if self._retrieval_config["enable_rerank"] and self._reranker is not None:
            pairs = [[query_text, chunk.text[:1200]] for chunk in chunks]
            try:
                scores = self._reranker.predict(pairs)
                if isinstance(scores, np.ndarray):
                    return [float(x) for x in scores.tolist()]
                return [float(x) for x in scores]
            except Exception:
                pass

        query_lower = query_text.lower()
        return [self._char_overlap_score(query_lower, chunk.text.lower()) for chunk in chunks]

    def _chunk_to_context(self, chunk: DocumentChunk, score: float, rank: int, signals: dict[str, float]) -> dict[str, Any]:
        return {
            "rank": rank,
            "score": score,
            "source": chunk.source,
            "chunk_id": chunk.chunk_id,
            "title": chunk.title,
            "heading": chunk.heading,
            "file_path": chunk.file_path,
            "start_char": chunk.start_char,
            "end_char": chunk.end_char,
            "token_count": chunk.token_count,
            "preview": chunk.text[:300],
            "text": chunk.text,
            "signals": {
                "base_score": round(float(signals.get("base_score", 0.0)), 6),
                "dense_raw": round(float(signals.get("dense_raw", 0.0)), 6),
                "sparse_raw": round(float(signals.get("sparse_raw", 0.0)), 6),
                "rerank_raw": round(float(signals.get("rerank_raw", 0.0)), 6),
                "dense_norm": round(float(signals.get("dense_norm", 0.0)), 6),
                "sparse_norm": round(float(signals.get("sparse_norm", 0.0)), 6),
                "rerank_norm": round(float(signals.get("rerank_norm", 0.0)), 6),
                "symbol_hit": round(float(signals.get("symbol_hit", 0.0)), 6),
                "legal_match": round(float(signals.get("legal_match", 0.0)), 6),
            },
        }

    def _load_chunks(self) -> list[DocumentChunk]:
        if self._chunks is not None:
            return self._chunks

        chunks: list[DocumentChunk] = []
        for directory in self.directories:
            if not directory.exists():
                continue
            for file_path in sorted(directory.rglob("*")):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in {".md", ".txt"}:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                chunks.extend(self._chunk_document(file_path=file_path, text=text))

        self._chunks = chunks
        return chunks

    def _chunk_document(self, file_path: Path, text: str) -> list[DocumentChunk]:
        sections = self._split_sections(text)
        chunks: list[DocumentChunk] = []
        title = file_path.stem
        for section_index, section in enumerate(sections, start=1):
            section_heading = section["heading"] or title
            windows = self._window_text(
                section["text"],
                max_chars=int(self._chunk_config["max_chars"]),
                overlap_chars=int(self._chunk_config["overlap_chars"]),
            )
            for window_index, window in enumerate(windows, start=1):
                chunk_text = window["text"].strip()
                if not chunk_text:
                    continue
                token_count = len(self._tokenize_for_bm25(chunk_text))
                chunks.append(
                    DocumentChunk(
                        source=file_path.name,
                        text=chunk_text,
                        chunk_id=f"{file_path.stem}-{section_index}-{window_index}",
                        file_path=str(file_path.relative_to(settings.root_dir)),
                        title=title,
                        heading=section_heading,
                        start_char=window["start"],
                        end_char=window["end"],
                        token_count=token_count,
                    )
                )
        return chunks

    def _split_sections(self, text: str) -> list[dict[str, str]]:
        lines = text.splitlines()
        sections: list[dict[str, str]] = []
        current_heading = ""
        current_lines: list[str] = []
        for line in lines:
            match = HEADING_RE.match(line.strip())
            if match:
                if current_lines:
                    sections.append({"heading": current_heading, "text": "\n".join(current_lines).strip()})
                    current_lines = []
                current_heading = match.group(2).strip()
                continue
            current_lines.append(line)
        if current_lines:
            sections.append({"heading": current_heading, "text": "\n".join(current_lines).strip()})
        if not sections:
            sections.append({"heading": "", "text": text})
        return sections

    def _window_text(self, text: str, max_chars: int, overlap_chars: int) -> list[dict[str, Any]]:
        normalized = text.strip()
        if len(normalized) <= max_chars:
            return [{"text": normalized, "start": 0, "end": len(normalized)}]

        step = max(max_chars - overlap_chars, 1)
        windows: list[dict[str, Any]] = []
        for start in range(0, len(normalized), step):
            piece = normalized[start : start + max_chars]
            if not piece:
                continue
            windows.append({"text": piece, "start": start, "end": start + len(piece)})
            if start + max_chars >= len(normalized):
                break
        return windows

    def _tokenize(self, text: str) -> list[str]:
        return [token.lower() for token in TOKEN_RE.findall(text or "")]

    def _tokenize_for_bm25(self, text: str) -> list[str]:
        normalized = (text or "").lower().strip()
        if not normalized:
            return []
        base_tokens = [token.lower() for token in TOKEN_RE.findall(normalized)]
        condensed = re.sub(r"\s+", "", normalized)
        char_ngrams = [condensed[idx : idx + 2] for idx in range(max(len(condensed) - 1, 0))]
        return base_tokens + char_ngrams

    def _lexical_score(self, query_tokens: list[str], query_lower: str, chunk: DocumentChunk) -> float:
        chunk_lower = chunk.text.lower()
        heading_lower = chunk.heading.lower()
        title_lower = chunk.title.lower()
        path_lower = chunk.file_path.lower()
        token_set = set(self._tokenize_for_bm25(chunk.text))
        overlap = sum(1 for token in query_tokens if token in token_set)
        exact_phrase = 1.0 if query_lower and query_lower in chunk_lower else 0.0
        heading_hit = 1.0 if any(token in heading_lower for token in query_tokens if token) else 0.0
        title_hit = 1.0 if any(token in title_lower for token in query_tokens if token) else 0.0
        path_hit = 1.0 if any(token in path_lower for token in query_tokens if token) else 0.0
        density = overlap / max(math.sqrt(max(chunk.token_count, 1)), 1.0)
        char_overlap = self._char_overlap_score(query_lower, chunk_lower)
        return (
            (overlap * 1.8)
            + (density * 4.2)
            + (exact_phrase * 2.4)
            + (heading_hit * 1.1)
            + (title_hit * 0.9)
            + (path_hit * 0.6)
            + (char_overlap * 2.0)
        )

    def _char_overlap_score(self, query: str, text: str) -> float:
        if not query or not text:
            return 0.0
        query_grams = {query[idx : idx + 2] for idx in range(max(len(query) - 1, 1))}
        text_grams = {text[idx : idx + 2] for idx in range(max(len(text) - 1, 1))}
        if not query_grams or not text_grams:
            return 0.0
        intersection = len(query_grams & text_grams)
        union = len(query_grams | text_grams)
        return intersection / max(union, 1)

    def _is_legal_intent(self, query: str) -> bool:
        normalized = (query or "").lower()
        return any(term in normalized for term in LEGAL_TERMS)

    def _contains_legal_term(self, text: str, heading: str, title: str, file_path: str) -> bool:
        merged = f"{text} {heading} {title} {file_path}".lower()
        return any(term in merged for term in LEGAL_TERMS)

    def _top_indices(self, scores: np.ndarray, top_k: int) -> list[int]:
        if scores.size == 0:
            return []
        top_k = max(1, min(int(top_k), int(scores.size)))
        indices = np.argpartition(scores, -top_k)[-top_k:]
        sorted_indices = indices[np.argsort(scores[indices])[::-1]]
        return [int(idx) for idx in sorted_indices]

    def _normalize_array(self, values: list[float] | np.ndarray) -> list[float]:
        arr = np.asarray(values, dtype=np.float32)
        if arr.size == 0:
            return []
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        low = float(np.min(arr))
        high = float(np.max(arr))
        if math.isclose(high, low):
            if math.isclose(high, 0.0):
                return [0.0 for _ in arr]
            return [1.0 for _ in arr]
        return [float((x - low) / (high - low)) for x in arr]

    def _get_runtime_retrieval_mode(self) -> str:
        now = time.time()
        if now < self._retrieval_mode_cache_expires_at:
            return self._retrieval_mode_cache
        mode = "hybrid"
        try:
            with get_connection() as conn:
                row = conn.execute("SELECT value FROM app_settings WHERE key = ?", ("rag_retrieval_mode",)).fetchone()
                if row is not None:
                    candidate = str(row["value"]).strip().lower()
                    if candidate in {"hybrid", "lexical"}:
                        mode = candidate
        except Exception:
            mode = "hybrid"
        self._retrieval_mode_cache = mode
        self._retrieval_mode_cache_expires_at = now + 2.0
        return mode

    def _effective_retrieval_strategy(self, retrieval_mode: str) -> str:
        return "lexical_only" if retrieval_mode == "lexical" else self._retrieval_config["strategy"]

    def _ensure_indices(self, force: bool) -> None:
        chunks = self._load_chunks()
        fingerprint = self._build_corpus_fingerprint(chunks)
        if self._index_built and not force and fingerprint == self._index_fingerprint:
            return

        with self._index_lock:
            if self._index_built and not force and fingerprint == self._index_fingerprint:
                return
            self._build_indices(chunks=chunks, fingerprint=fingerprint)

    def _build_indices(self, chunks: list[DocumentChunk], fingerprint: str) -> None:
        self._index_error = ""
        self._bm25 = None
        self._dense_embeddings = None
        self._faiss_index = None
        self._vector_store_runtime = "none"
        self._bm25_tokens = []

        if self._retrieval_config["enable_sparse"]:
            self._bm25_tokens = [self._tokenize_for_bm25(f"{chunk.heading}\n{chunk.text}") for chunk in chunks]
            if BM25Okapi is not None and self._bm25_tokens:
                try:
                    self._bm25 = BM25Okapi(self._bm25_tokens)
                except Exception as exc:
                    self._index_error = f"sparse index build failed: {exc}"

        if self._retrieval_config["enable_dense"]:
            embedder = self._load_embedder()
            if embedder is not None and chunks:
                try:
                    corpus = [self._to_embedding_text(chunk) for chunk in chunks]
                    embeddings = embedder.encode(
                        corpus,
                        batch_size=settings.rag_embedding_batch_size,
                        show_progress_bar=False,
                        normalize_embeddings=True,
                    )
                    dense = np.asarray(embeddings, dtype=np.float32)
                    if dense.ndim == 2 and dense.shape[0] == len(chunks):
                        self._dense_embeddings = dense
                        if settings.rag_vector_store == "faiss" and faiss is not None:
                            index = faiss.IndexFlatIP(dense.shape[1])
                            index.add(dense)
                            self._faiss_index = index
                            self._vector_store_runtime = "faiss"
                        else:
                            self._vector_store_runtime = "numpy"
                except Exception as exc:
                    self._index_error = (f"{self._index_error}; " if self._index_error else "") + f"dense index build failed: {exc}"

        if self._retrieval_config["enable_rerank"]:
            self._load_reranker()

        self._index_built = True
        self._index_fingerprint = fingerprint
        self._last_index_build = datetime.utcnow().isoformat(timespec="seconds")

    def _build_corpus_fingerprint(self, chunks: list[DocumentChunk]) -> str:
        if not chunks:
            return "empty"
        hasher = hashlib.sha1()
        hasher.update(str(self._chunk_config).encode("utf-8"))
        hasher.update(str(self.directories).encode("utf-8"))
        for chunk in chunks:
            hasher.update(chunk.chunk_id.encode("utf-8"))
            hasher.update(chunk.file_path.encode("utf-8"))
            hasher.update(str(chunk.start_char).encode("utf-8"))
            hasher.update(str(chunk.end_char).encode("utf-8"))
            hasher.update(str(chunk.token_count).encode("utf-8"))
        return hasher.hexdigest()

    def _to_embedding_text(self, chunk: DocumentChunk) -> str:
        return f"标题: {chunk.title}\n小节: {chunk.heading}\n正文: {chunk.text}"

    def _load_embedder(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        if SentenceTransformer is None:
            self._index_error = (f"{self._index_error}; " if self._index_error else "") + "sentence-transformers not installed"
            return None
        try:
            self._embedder = SentenceTransformer(settings.rag_embedding_model)
            return self._embedder
        except Exception as exc:
            self._index_error = (f"{self._index_error}; " if self._index_error else "") + f"embedding model load failed: {exc}"
            return None

    def _load_reranker(self) -> Any:
        if self._reranker is not None:
            return self._reranker
        if CrossEncoder is None:
            return None
        try:
            self._reranker = CrossEncoder(settings.rag_rerank_model)
            return self._reranker
        except Exception as exc:
            self._index_error = (f"{self._index_error}; " if self._index_error else "") + f"reranker load failed: {exc}"
            return None

    def _encode_query(self, query_text: str) -> np.ndarray | None:
        embedder = self._load_embedder()
        if embedder is None:
            return None
        try:
            query_embedding = embedder.encode(
                [query_text],
                batch_size=1,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            arr = np.asarray(query_embedding, dtype=np.float32)
            if arr.ndim == 2 and arr.shape[0] == 1:
                return arr[0]
            if arr.ndim == 1:
                return arr
            return None
        except Exception:
            return None

    def _build_answer(self, query: str, symbol: str | None, ranked: list[dict[str, Any]]) -> str:
        retrieval_mode = self._get_runtime_retrieval_mode()
        strategy = self._effective_retrieval_strategy(retrieval_mode)
        ollama_answer = self._query_ollama(query=query, symbol=symbol, ranked=ranked)
        if ollama_answer:
            return ollama_answer

        if not ranked:
            status = self.index_status(lightweight=True)
            return (
                "未检索到足够相关材料。"
                f" 当前索引状态：dense_ready={status['dense_ready']} / sparse_ready={status['sparse_ready']} / rerank_ready={status['rerank_ready']}。"
                "请补充文档或降低问题范围后重试。"
            )

        lines = [
            f"问题：{query}",
            (
                "检索管线：Chunk -> Dense Embedding 检索 -> Sparse(BM25) 检索 -> RRF 融合 -> Rerank -> TopK 输出。"
                if retrieval_mode == "hybrid"
                else "检索管线：纯文字检索（关键词/BM25/词法）-> TopK 输出。"
            ),
            f"当前模式：{retrieval_mode} / {strategy}",
        ]
        if symbol:
            lines.append(f"股票代码：{symbol}")
        for item in ranked:
            preview = item["preview"].replace("\n", " ")
            signals = item.get("signals", {})
            lines.append(
                f"- 证据 {item['rank']}：{item['source']} / {item['heading'] or item['title']} / "
                f"score={item['score']} / dense={signals.get('dense_norm', 0):.3f} / "
                f"sparse={signals.get('sparse_norm', 0):.3f} / rerank={signals.get('rerank_norm', 0):.3f} -> {preview}"
            )
        lines.append("当前未启用 Ollama 生成式回答，因此返回的是结构化检索摘要。")
        return "\n".join(lines)

    def generate_with_ollama_debug(self, prompt: str, timeout: int = 120) -> dict[str, Any]:
        def _safe_body_text(resp: Any) -> str:
            try:
                text = str(getattr(resp, "text", "") or "").strip()
            except Exception:
                text = ""
            if len(text) > 4000:
                return f"{text[:4000]}\n...[truncated]"
            return text

        if not settings.ollama_model:
            return {
                "ok": False,
                "response": "",
                "error": "OLLAMA_MODEL is empty",
                "status_code": None,
                "response_text": "",
            }
        payload = {"model": settings.ollama_model, "prompt": prompt, "stream": False}
        response = None
        try:
            response = requests.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            response_text = _safe_body_text(response)
            return {
                "ok": True,
                "response": str(data.get("response", "")).strip(),
                "error": "",
                "status_code": response.status_code,
                "response_text": response_text,
            }
        except Exception as exc:
            status_code = getattr(response, "status_code", None)
            response_text = _safe_body_text(response)
            return {
                "ok": False,
                "response": "",
                "error": str(exc),
                "status_code": status_code,
                "response_text": response_text,
            }

    def generate_with_ollama(self, prompt: str, timeout: int = 120) -> str:
        debug = self.generate_with_ollama_debug(prompt=prompt, timeout=timeout)
        return str(debug.get("response", "")).strip()

    def _query_ollama(self, query: str, symbol: str | None, ranked: list[dict[str, Any]]) -> str:
        if not settings.ollama_model:
            return ""
        retrieval_mode = self._get_runtime_retrieval_mode()
        strategy = self._effective_retrieval_strategy(retrieval_mode)
        prompt_parts = [
            "你是股票投研助手。请优先依据给定材料回答；如果材料不足，必须明确说明“材料不足”。",
            f"用户问题：{query}",
            (
                "检索管线："
                f"{strategy} "
                f"(chunk={self._chunk_config['mode']}, max_chars={self._chunk_config['max_chars']}, "
                f"overlap={self._chunk_config['overlap_chars']})"
            ),
            f"当前检索模式：{retrieval_mode}",
        ]
        if symbol:
            prompt_parts.append(f"股票代码：{symbol}")
        if ranked:
            prompt_parts.append("材料：")
            for idx, item in enumerate(ranked, start=1):
                prompt_parts.append(f"[材料{idx} | {item['source']} | {item['heading']}]\n{item['text'][:800]}")
        else:
            prompt_parts.append("材料：暂无命中。请先说明材料不足，再给出你能提供的最稳妥建议。")
        prompt_parts.append("请输出：1. 结论 2. 依据 3. 风险提醒")
        return self.generate_with_ollama(prompt="\n\n".join(prompt_parts), timeout=120)
