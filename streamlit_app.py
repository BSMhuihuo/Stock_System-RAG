from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from stock_system.container import container


st.set_page_config(page_title="智能股票交易系统", page_icon="📈", layout="wide")


def init_state() -> None:
    st.session_state.setdefault("selected_symbol", "600519")
    st.session_state.setdefault("selected_name", container.market_data.resolve_name("600519"))


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --bg: #09111f;
          --panel: #101b2e;
          --panel-2: #13233c;
          --border: rgba(148, 163, 184, 0.18);
          --text: #e5edf8;
          --muted: #93a4bd;
          --green: #16a34a;
          --red: #ef4444;
          --gold: #f59e0b;
          --blue: #38bdf8;
        }
        .stApp {
          background:
            radial-gradient(circle at top left, rgba(56, 189, 248, 0.08), transparent 26%),
            radial-gradient(circle at top right, rgba(245, 158, 11, 0.08), transparent 24%),
            linear-gradient(180deg, #07101c 0%, #09111f 100%);
          color: var(--text);
        }
        .block-container {padding-top: 1.2rem; padding-bottom: 1.5rem;}
        [data-testid="stSidebar"] {
          background: rgba(11, 19, 34, 0.96);
          border-right: 1px solid var(--border);
        }
        div[data-testid="stMetric"] {
          background: linear-gradient(180deg, rgba(16, 27, 46, 0.96), rgba(10, 18, 33, 0.96));
          border: 1px solid var(--border);
          border-radius: 18px;
          padding: 14px 16px;
        }
        .panel {
          background: linear-gradient(180deg, rgba(16, 27, 46, 0.96), rgba(12, 20, 36, 0.96));
          border: 1px solid var(--border);
          border-radius: 20px;
          padding: 18px 18px 14px 18px;
          box-shadow: 0 18px 50px rgba(0, 0, 0, 0.22);
        }
        .hero {
          background: linear-gradient(135deg, rgba(14, 27, 50, 0.95), rgba(17, 40, 71, 0.85));
          border: 1px solid rgba(56, 189, 248, 0.24);
          border-radius: 24px;
          padding: 22px 24px;
          margin-bottom: 14px;
        }
        .hero-title {font-size: 30px; font-weight: 800; color: #f8fbff;}
        .hero-sub {font-size: 13px; color: var(--muted); margin-top: 6px;}
        .tag {
          display: inline-block;
          padding: 6px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 700;
          margin-right: 8px;
        }
        .tag-blue {background: rgba(56, 189, 248, 0.12); color: #7dd3fc;}
        .tag-green {background: rgba(34, 197, 94, 0.12); color: #86efac;}
        .tag-gold {background: rgba(245, 158, 11, 0.12); color: #fcd34d;}
        .section-title {font-size: 18px; font-weight: 800; margin-bottom: 10px;}
        .small-muted {font-size: 12px; color: var(--muted);}
        .quote-card {
          background: rgba(19, 35, 60, 0.9);
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 14px;
          margin-bottom: 10px;
        }
        .quote-name {font-size: 15px; font-weight: 800;}
        .quote-meta {font-size: 12px; color: var(--muted);}
        .rise {color: var(--red);}
        .fall {color: var(--green);}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## 交易面板")
        if st.button("刷新实时行情", use_container_width=True):
            container.market_data.clear_cache()
            st.rerun()

        query = st.text_input("搜索股票代码/名称", value=st.session_state.selected_symbol)
        results = container.market_data.search_stocks(query=query, limit=12)
        for item in results:
            if st.button(f"{item.symbol}  {item.name}", key=f"pick_{item.symbol}", use_container_width=True):
                st.session_state.selected_symbol = item.symbol
                st.session_state.selected_name = item.name
                st.rerun()

        st.markdown("---")
        st.markdown("### 热门股票")
        for item in container.market_data.get_hot_stocks(limit=8):
            change_cls = "rise" if item.get("change_pct", 0) >= 0 else "fall"
            st.markdown(
                f"""
                <div class="quote-card">
                  <div class="quote-name">{item['name']} <span class="small-muted">{item['symbol']}</span></div>
                  <div class="quote-meta">现价 {item['price']:.2f} · <span class="{change_cls}">{item['change_pct']:.2f}%</span></div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def build_candlestick_chart(history: pd.DataFrame) -> alt.Chart:
    data = history.copy()
    data["date_str"] = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d")
    data["color"] = data.apply(lambda row: "上涨" if row["close"] >= row["open"] else "下跌", axis=1)

    rule = (
        alt.Chart(data)
        .mark_rule()
        .encode(
            x=alt.X("date_str:N", sort=None, axis=alt.Axis(title="日期", labels=False, ticks=False)),
            y=alt.Y("low:Q", title="价格"),
            y2="high:Q",
            color=alt.Color(
                "color:N",
                scale=alt.Scale(domain=["上涨", "下跌"], range=["#ef4444", "#22c55e"]),
                legend=None,
            ),
        )
    )

    bar = (
        alt.Chart(data)
        .mark_bar(size=7)
        .encode(
            x=alt.X("date_str:N", sort=None, axis=alt.Axis(title="日期", labelAngle=-40)),
            y=alt.Y("open:Q", title="价格"),
            y2="close:Q",
            color=alt.Color(
                "color:N",
                scale=alt.Scale(domain=["上涨", "下跌"], range=["#ef4444", "#22c55e"]),
                legend=None,
            ),
            tooltip=["date_str:N", "open:Q", "close:Q", "high:Q", "low:Q", "volume:Q"],
        )
    )

    return (rule + bar).properties(height=420)


def render_header(selected_quote: dict, overview: dict) -> None:
    change_cls = "rise" if selected_quote.get("change_pct", 0) >= 0 else "fall"
    st.markdown(
        f"""
        <div class="hero">
          <div>
            <span class="tag tag-blue">A股全市场检索</span>
            <span class="tag tag-green">实时快照</span>
            <span class="tag tag-gold">模拟交易</span>
          </div>
          <div class="hero-title">{selected_quote['name']} <span style="font-size:16px;color:#9fb0c7;">{selected_quote['symbol']}</span></div>
          <div class="hero-sub">
            最新价 <span class="{change_cls}" style="font-weight:800;">{selected_quote['price']:.2f}</span>
            · 涨跌幅 <span class="{change_cls}" style="font-weight:800;">{selected_quote['change_pct']:.2f}%</span>
            · 市场覆盖 {overview['universe_size']} 只股票
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_market_metrics(selected_quote: dict, context: dict, snapshot) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最新价", f"{selected_quote['price']:.2f}", f"{selected_quote['change_pct']:.2f}%")
    c2.metric("MA5 / MA20", f"{context['ma5']:.2f} / {context['ma20']:.2f}", f"{context['return_20d']:.2f}%")
    c3.metric("账户现金", f"{snapshot.cash:,.2f}", f"总资产 {snapshot.total_equity:,.2f}")
    c4.metric("持仓数", f"{len(snapshot.positions)}", f"持仓市值 {snapshot.total_market_value:,.2f}")


def render_main_panels() -> None:
    symbol = st.session_state.selected_symbol
    name = st.session_state.selected_name
    overview = container.market_data.get_market_overview()
    selected_quote = container.market_data.get_realtime_snapshot(symbol)
    context = container.market_data.build_symbol_context(symbol, name)
    snapshot = container.trading.get_account_snapshot("paper")
    history = container.market_data.get_history(symbol, limit=90)

    render_header(selected_quote, overview)
    render_market_metrics(selected_quote, context, snapshot)

    left, right = st.columns([2.2, 1.1], gap="large")

    with left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">K 线图</div>', unsafe_allow_html=True)
        st.altair_chart(build_candlestick_chart(history), use_container_width=True)
        st.dataframe(history.tail(20), width="stretch", height=260)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">市场热度与推荐</div>', unsafe_allow_html=True)
        rec_left, rec_right = st.columns([1.1, 1.1], gap="large")
        with rec_left:
            st.markdown("**热门实时股票**")
            st.dataframe(pd.DataFrame(overview["hot_stocks"]), width="stretch", height=280)
        with rec_right:
            st.markdown("**系统推荐**")
            recommendations = container.ranking.recommend(limit=6)
            st.dataframe(pd.DataFrame([item.model_dump() for item in recommendations]), width="stretch", height=280)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">全市场实时看盘</div>', unsafe_allow_html=True)
        market_filter_col1, market_filter_col2 = st.columns([2, 1])
        with market_filter_col1:
            market_query = st.text_input("市场过滤", value="", key="market_query")
        with market_filter_col2:
            market_page = st.number_input("页码", min_value=1, value=1, step=1, key="market_page")
        market_page_data = container.market_data.get_realtime_market_page(
            query=market_query,
            page=int(market_page),
            page_size=30,
        )
        st.caption(f"总数 {market_page_data['total']} · 当前页 {market_page_data['page']}")
        st.dataframe(pd.DataFrame(market_page_data["items"]), width="stretch", height=320)
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">实时行情</div>', unsafe_allow_html=True)
        st.write(
            {
                "symbol": selected_quote["symbol"],
                "name": selected_quote["name"],
                "price": selected_quote["price"],
                "change_pct": selected_quote["change_pct"],
                "open": selected_quote["open"],
                "high": selected_quote["high"],
                "low": selected_quote["low"],
                "prev_close": selected_quote["prev_close"],
                "timestamp": selected_quote["timestamp"],
                "source": selected_quote["source"],
            }
        )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">研究解释 / RAG</div>', unsafe_allow_html=True)
        question = st.text_area(
            "研究问题",
            value=f"请结合当前市场环境，解释为什么需要关注 {selected_quote['name']} 这只股票。",
            height=120,
        )
        if st.button("生成研究解释", use_container_width=True):
            result = container.research.query(query=question, symbol=symbol, top_k=3)
            st.write(result["answer"])
            st.caption("来源: " + ", ".join(result["sources"]))
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">交易执行</div>', unsafe_allow_html=True)
        qty = st.number_input("买卖数量", min_value=100, step=100, value=100)
        trade_col1, trade_col2 = st.columns(2)
        with trade_col1:
            if st.button("买入", use_container_width=True):
                result = container.trading.place_order(
                    mode="paper",
                    symbol=symbol,
                    name=selected_quote["name"],
                    side="BUY",
                    quantity=int(qty),
                    price=selected_quote["price"],
                    reason="UI 买入",
                )
                st.json(result.model_dump())
        with trade_col2:
            if st.button("卖出", use_container_width=True):
                result = container.trading.place_order(
                    mode="paper",
                    symbol=symbol,
                    name=selected_quote["name"],
                    side="SELL",
                    quantity=int(qty),
                    price=selected_quote["price"],
                    reason="UI 卖出",
                )
                st.json(result.model_dump())

        if st.button("自动交易", use_container_width=True):
            result = container.trading.auto_trade(mode="paper", top_n=3)
            st.json(result)
        st.markdown("</div>", unsafe_allow_html=True)


def render_bottom_panels() -> None:
    bottom_left, bottom_right = st.columns([1.4, 1.0], gap="large")
    with bottom_left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">持仓</div>', unsafe_allow_html=True)
        snapshot = container.trading.get_account_snapshot("paper")
        if snapshot.positions:
            st.dataframe(pd.DataFrame([item.model_dump() for item in snapshot.positions]), width="stretch")
        else:
            st.info("当前没有持仓。")
        st.markdown("</div>", unsafe_allow_html=True)
    with bottom_right:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">最近订单</div>', unsafe_allow_html=True)
        orders = container.trading.list_orders(mode="paper", limit=12)
        if orders:
            st.dataframe(pd.DataFrame([item.model_dump() for item in orders]), width="stretch")
        else:
            st.info("当前没有订单记录。")
        st.markdown("</div>", unsafe_allow_html=True)


init_state()
apply_styles()
render_sidebar()
render_main_panels()
st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
render_bottom_panels()
