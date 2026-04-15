# System Design

## 目标

第一版先解决 5 个问题：

1. 股票搜索和历史行情查看
2. 推荐候选生成
3. 基于研究文档的 RAG 解释
4. 模拟交易下单和自动交易
5. 飞书通知

## 模块

- `config.py`: 环境变量和路径
- `db.py`: SQLite 初始化和基础访问
- `schemas.py`: API/UI 公共数据结构
- `services/`: 数据、推荐、RAG、交易、通知
- `api.py`: FastAPI 对外接口
- `streamlit_app.py`: 交互式 UI

## 当前实现

- `MarketDataService`
  - 股票搜索
  - 历史行情
  - 真实数据失败时回退到演示数据
- `RankingService`
  - 基于 5/20 日收益、均线、波动率做轻量打分
- `ResearchService`
  - 读取 `research-report/*.md`
  - 本地 chunking
  - Dense Embedding 检索（`sentence-transformers`）
  - Sparse 检索（`BM25`）
  - RRF 融合 + Cross-Encoder Rerank
  - 向量索引优先 `FAISS`（不可用时回退 `numpy`）
  - 可选 Ollama 总结
- `TradingService`
  - `paper` 模式账户
  - 订单、持仓、自动交易
  - `live` 模式默认禁用
- `FeishuService`
  - webhook 文本通知

## 启动方式

```bash
conda activate stock-system
uvicorn api_app:app --reload
streamlit run streamlit_app.py
```

## 设计原则

- 默认可运行
- 外部依赖失败时自动降级
- 交易层和数据层可替换
- 实盘接口先抽象，后适配券商
