# 接口契约

REST统一前缀 `/api/v1`，成功结构为 `data` + `request_id`，错误结构为 `error` + `request_id`。`src/index.ts` 定义请求、消息和事件结构；`events.schema.json` 可校验持久化房间事件。HTTP契约以FastAPI生成的 `/openapi.json` 为准。

WebSocket先发送一次性 `auth` ticket，再 `subscribe` 房间和 `after_seq`；服务端返回按房间递增的 `seq`。客户端按 `room_id + seq` 去重，断线重连后从最后处理的序号回放。完整目标见 `docs/实施方案.md` 第8、9节。
