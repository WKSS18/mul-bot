# 接口契约

REST统一前缀 `/api/v1`，成功结构为 `data` + `request_id`，错误结构为 `error` + `request_id`。完整目标见 `docs/实施方案.md` 第8、9节。后续从 FastAPI OpenAPI 生成 TypeScript 类型，避免手工漂移。
