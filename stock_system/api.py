from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .container import container
from .schemas import (
    AutoTradeRequest,
    FeishuAppMessageRequest,
    FeishuWebhookRequest,
    ResearchQueryRequest,
    SystemSettingsUpdateRequest,
    TradeOrderRequest,
)


app = FastAPI(title="Stock System MVP", version="0.2.0")
ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def frontend_index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/system/settings")
def get_system_settings() -> dict:
    return container.system_settings.get_settings().model_dump()


@app.post("/system/settings")
def update_system_settings(request: SystemSettingsUpdateRequest) -> dict:
    try:
        return container.system_settings.update_settings(request).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/system/ollama/check")
def check_ollama(timeout: int | None = Query(default=None, ge=5, le=600)) -> dict:
    profile = container.system_settings.get_settings()
    final_timeout = int(timeout or profile.ollama_timeout_seconds)
    started = time.perf_counter()
    result = container.research.generate_with_ollama_debug(
        prompt="请只输出 OK，不要输出任何其他内容。",
        timeout=final_timeout,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    response_text = str(result.get("response", "") or "").strip()
    response_preview = response_text[:160]
    return {
        "available": bool(result.get("ok")) and bool(response_text),
        "timeout_seconds": final_timeout,
        "latency_ms": latency_ms,
        "status_code": result.get("status_code"),
        "error": str(result.get("error", "") or "").strip(),
        "response_preview": response_preview,
    }


@app.get("/stocks/search")
def search_stocks(q: str = Query(default="", alias="q"), limit: int = 10) -> dict:
    items = container.market_data.search_stocks(query=q, limit=limit)
    return {"items": [item.model_dump() for item in items]}


@app.get("/market/overview")
def market_overview() -> dict:
    return container.market_data.get_market_overview()


@app.get("/market/realtime")
def market_realtime(
    q: str = Query(default="", alias="q"),
    page: int = 1,
    page_size: int | None = None,
) -> dict:
    profile = container.system_settings.get_settings()
    final_page_size = page_size or profile.market_page_size
    return container.market_data.get_realtime_market_page(query=q, page=page, page_size=final_page_size)


@app.get("/stocks/{symbol}/realtime")
def stock_realtime(symbol: str) -> dict:
    return container.market_data.get_realtime_snapshot(symbol)


@app.get("/stocks/{symbol}/history")
def stock_history(symbol: str, limit: int = 120) -> dict:
    df = container.market_data.get_history(symbol=symbol, limit=limit)
    return {"items": df.to_dict(orient="records")}


@app.get("/stocks/{symbol}/analysis")
def stock_analysis(symbol: str) -> dict:
    return container.ranking.analyze_stock(symbol).model_dump()


@app.get("/recommendations")
def recommendations(limit: int = 5, q: str = Query(default="", alias="q"), strategy: str = "ensemble") -> dict:
    items = container.ranking.recommend(limit=limit, query=q, strategy=strategy)
    return {"items": [item.model_dump() for item in items], "strategy": strategy}


@app.post("/research/query")
def research_query(request: ResearchQueryRequest) -> dict:
    return container.research.query(query=request.query, symbol=request.symbol, top_k=request.top_k)


@app.get("/research/chunking")
def research_chunking() -> dict:
    return container.research.describe_chunking()


@app.get("/research/status")
def research_status() -> dict:
    return container.research.index_status()


@app.post("/research/reindex")
def research_reindex() -> dict:
    return container.research.rebuild_index(force=True)


@app.get("/accounts/{mode}")
def account_snapshot(mode: str) -> dict:
    snapshot = container.trading.get_account_snapshot(mode=mode)
    return snapshot.model_dump()


@app.get("/orders/{mode}")
def list_orders(mode: str, limit: int = 50) -> dict:
    items = container.trading.list_orders(mode=mode, limit=limit)
    return {"items": [item.model_dump() for item in items]}


@app.post("/trade/order")
def place_order(request: TradeOrderRequest) -> dict:
    try:
        result = container.trading.place_order(
            mode=request.mode,
            symbol=request.symbol,
            name=request.name,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump()


@app.post("/trade/auto")
def auto_trade(request: AutoTradeRequest) -> dict:
    try:
        return container.trading.auto_trade(mode=request.mode, top_n=request.top_n, strategy=request.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/notify/feishu/webhook")
def feishu_webhook(request: FeishuWebhookRequest) -> dict:
    return container.feishu.send_webhook_text(request.text)


@app.post("/notify/feishu/app")
def feishu_app_message(request: FeishuAppMessageRequest) -> dict:
    return container.feishu.send_app_text(
        receive_id=request.receive_id,
        text=request.text,
        receive_id_type=request.receive_id_type,
    )


@app.get("/feishu/status")
def feishu_status() -> dict:
    return {
        "webhook_configured": bool(settings.feishu_webhook_url),
        "app_configured": bool(settings.feishu_app_id and settings.feishu_app_secret),
        "verify_token_configured": bool(settings.feishu_verify_token),
        "event_callback_path": "/feishu/events",
    }


def _normalize_feishu_text(raw_content: str) -> str:
    try:
        payload = json.loads(raw_content)
        if isinstance(payload, dict):
            return str(payload.get("text", "")).strip()
    except Exception:
        pass
    return str(raw_content or "").strip()


def _extract_receive_id(event: dict) -> tuple[str, str]:
    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", {})
    open_id = str(sender_id.get("open_id", "")).strip()
    chat_id = str(event.get("message", {}).get("chat_id", "")).strip()
    if open_id:
        return open_id, "open_id"
    if chat_id:
        return chat_id, "chat_id"
    return "", "chat_id"


def _build_feishu_help_text() -> str:
    return (
        "可用命令:\n"
        "1. 帮助\n"
        "2. 行情 600519\n"
        "3. 推荐\n"
        "4. 研究 请分析银行板块\n"
        "5. 买入 600519 100\n"
        "6. 卖出 600519 100\n"
        "7. 自动交易"
    )


def _handle_feishu_command(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        return _build_feishu_help_text()

    parts = normalized.split()
    head = parts[0]

    if head in {"帮助", "help", "HELP"}:
        return _build_feishu_help_text()

    if head == "推荐":
        items = container.ranking.recommend(limit=5, strategy="ensemble")
        if not items:
            return "当前没有可用推荐结果。"
        lines = ["当前推荐:"]
        for idx, item in enumerate(items, start=1):
            lines.append(f"{idx}. {item.name}({item.symbol}) 价格 {item.price:.2f} 分数 {item.score:.2f}")
        return "\n".join(lines)

    if head == "自动交易":
        result = container.trading.auto_trade(mode="paper", top_n=3, strategy="ensemble_rag")
        executed = result.get("executed", [])
        skipped = result.get("skipped", [])
        lines = [f"自动交易完成，成交 {len(executed)} 笔。"]
        if result.get("referee_reason"):
            lines.append(f"RAG 裁决：{result['referee_reason'][:120]}")
        if executed:
            for item in executed:
                lines.append(f"- BUY {item['symbol']} x {item['quantity']} @ {item['price']}")
        if skipped:
            lines.append("跳过:")
            lines.extend([f"- {item}" for item in skipped[:5]])
        return "\n".join(lines)

    if head == "行情" and len(parts) >= 2:
        symbol = parts[1]
        quote = container.market_data.get_realtime_snapshot(symbol)
        analysis = container.ranking.analyze_stock(symbol)
        return (
            f"{quote['name']}({quote['symbol']})\n"
            f"现价: {quote['price']:.2f}\n"
            f"涨跌幅: {quote['change_pct']:.2f}%\n"
            f"综合分: {analysis.total_score:.2f}\n"
            f"时间: {quote['timestamp'] or '--'}"
        )

    if head == "研究":
        profile = container.system_settings.get_settings()
        question = normalized[len(head) :].strip() or "请给出当前股票系统设计建议。"
        result = container.research.query(query=question, top_k=profile.research_top_k)
        sources = ", ".join(result.get("sources", [])[:3])
        return f"{result['answer']}\n\n来源: {sources}"

    if head in {"买入", "卖出"}:
        if len(parts) < 3:
            return "用法: 买入 600519 100"
        try:
            quantity = int(parts[2])
        except ValueError:
            return "交易数量必须是整数，例如: 买入 600519 100"
        symbol = parts[1]
        name = container.market_data.resolve_name(symbol)
        side = "BUY" if head == "买入" else "SELL"
        result = container.trading.place_order(
            mode="paper",
            symbol=symbol,
            name=name,
            side=side,
            quantity=quantity,
            reason=f"Feishu {head}",
        )
        return (
            f"{head}结果: {result.status}\n"
            f"{result.name}({result.symbol}) x {result.quantity}\n"
            f"价格: {result.price:.2f}\n"
            f"原因: {result.reason}"
        )

    return _build_feishu_help_text()


@app.post("/feishu/events")
async def feishu_events(request: Request) -> dict:
    payload = await request.json()

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    token = str(payload.get("token", "")).strip()
    if settings.feishu_verify_token and token and token != settings.feishu_verify_token:
        raise HTTPException(status_code=403, detail="invalid feishu token")

    event = payload.get("event", {})
    message = event.get("message", {})
    if message.get("message_type") != "text":
        return {"ok": True, "message": "ignored non-text event"}

    receive_id, receive_id_type = _extract_receive_id(event)
    if not receive_id:
        return {"ok": True, "message": "ignored event without receive id"}

    text = _normalize_feishu_text(message.get("content", ""))
    reply = _handle_feishu_command(text)
    send_result = container.feishu.send_app_text(receive_id=receive_id, text=reply, receive_id_type=receive_id_type)
    return {"ok": True, "reply_sent": send_result.get("ok", False), "send_message": send_result.get("message", "")}
