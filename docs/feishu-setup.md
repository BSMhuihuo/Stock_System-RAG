# 飞书接入与测试

## 1. 当前支持的两条链路

- `Webhook` 单向通知
- `App ID / Secret` 双向消息

当前项目中的回调入口是：

```text
POST /feishu/events
```

## 2. 先测最简单的 Webhook

这一步不需要公网回调，也不需要 `receive_id`。

先启动服务：

```bash
conda activate stock-system
cd E:\Desktop\stock
uvicorn api_app:app --reload
```

再执行：

```bash
python scripts/test_feishu.py --webhook --text "股票系统 webhook 测试"
```

如果返回 `ok: true`，去飞书群里看机器人消息是否到了。

也可以直接调接口：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/notify/feishu/webhook `
  -ContentType "application/json" `
  -Body '{"text":"股票系统 webhook 测试"}'
```

## 3. 再测 App Token 是否正常

这一步只验证 `App ID / Secret` 能否换到租户访问令牌，不会发消息。

```bash
python scripts/test_feishu.py --tenant-token
```

如果输出里 `ok` 为 `true`，说明应用凭证有效。

## 4. 测 App 主动发消息

这一步需要你已经知道一个 `receive_id`。

常见方式：

- 给个人发：使用 `open_id`
- 给群发：使用 `chat_id`

命令示例：

```bash
python scripts/test_feishu.py --app-send --receive-id <你的chat_id> --receive-id-type chat_id --text "股票系统 App 主动消息测试"
```

如果成功，飞书里会收到一条应用消息。

## 5. 测双向对话回调

这一步才会验证“你给机器人发消息，系统自动回复”。

因为飞书服务端需要回调你的本地程序，`http://127.0.0.1:8000` 不能直接被飞书访问，所以必须给本地服务挂一个公网 `HTTPS` 地址。

常见做法：

- `cpolar`
- `frp`
- `ngrok`

假设公网地址是：

```text
https://xxx.example.com
```

那飞书事件回调地址要填：

```text
https://xxx.example.com/feishu/events
```

## 6. 飞书开放平台里要做的配置

在你的自建应用里完成下面几项：

1. 启用机器人能力
2. 开启事件订阅
3. 事件请求网址填写 `https://你的公网地址/feishu/events`
4. `Verification Token` 与本地 `.env` 中的 `FEISHU_VERIFY_TOKEN` 保持一致
5. 先不要开启消息加密
6. 订阅事件 `im.message.receive_v1`
7. 给应用开通发送消息权限

## 7. 回调联调时如何验证

服务启动后，给应用机器人发下面任一命令：

```text
帮助
行情 600519
推荐
研究 请分析银行板块
买入 600519 100
卖出 600519 100
自动交易
```

如果回调配置正确，系统会自动回复。

## 8. 我建议你按这个顺序测

1. 先测 `Webhook`
2. 再测 `tenant token`
3. 再测 `App 主动发消息`
4. 最后测 `事件回调`

这样定位问题最快。
