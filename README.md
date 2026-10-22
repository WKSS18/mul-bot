# 同频 AI

按 [实施方案](docs/实施方案.md) 启动的新仓库。官网与 Bot 工作台是两个独立构建和部署单元；Electron 使用工作台界面，FastAPI 独立运行。视觉和交互参考前一个蓝白原型。

## 当前进度

| 部分 | 已完成 | 尚待完成 |
|---|---|---|
| 官网 | 独立 Vite 入口、响应式页面、独立 `dist/website` 构建 | 页面文案和真实注册跳转联调 |
| Bot 网页 | 独立 Vite 入口、原型工作台、独立 `dist/workbench` 构建 | 原型本地模拟状态替换为 API/WS 数据 |
| Electron | Main/Preload 安全隔离骨架，共享工作台构建 | 会话、原生文件、更新、安装包及签名 |
| 后端 | 注册、登录、工作空间、房间、消息持久化和基本成员权限；开发数据库自动建表 | 刷新会话、邀请、WebSocket/事件、并发序号、Bot Runner、迁移、生产部署 |
| DeepSeek | 服务端 Anthropic SDK 适配器，按配置发起真实请求 | 运行任务/流式结果接入和使用受控密钥实测 |
| OSS | 服务端 V4 限定文件键和大小的直传签名、HEAD 核验后落库 | 前端直传、文件解析、产物下载和 Bucket/CORS/RAM 实测 |

工作台沿用原型中的本地演示数据。它尚未调用刚搭建的后端；模拟的 Bot 回复、邀请和文件卡片不代表线上能力。前后端联调是下一阶段的首要任务。

## 安装与启动

前端要求 Node 22；后端要求 Python 3.12。

```powershell
cd D:\dz-ai\tongpin-ai
npm install
npm run website:dev
# 新开终端
npm run workbench:dev
```

官网为 `http://127.0.0.1:5173/`，工作台为 `http://127.0.0.1:5174/`。分别构建：`npm run website:build`、`npm run workbench:build`。

后端开发可先用 SQLite：在 `D:\dz-ai\tongpin-ai\.env` 中配置 `APP_ENV=development`、`DATABASE_URL=sqlite+aiosqlite:///./tongpin-dev.db` 和长度至少 40 的随机 `JWT_SECRET`。该文件已加入 `.gitignore`。生产目标为 MySQL，生产迁移尚未建立，此阶段不要部署。

```powershell
cd D:\dz-ai\tongpin-ai\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API 文档：`http://127.0.0.1:8000/docs`。本地自动测试：`cd backend; .\.venv\Scripts\python.exe -m pytest -q`。

桌面开发需先启动工作台 Vite，再运行 `npm run desktop:dev`。此阶段只验证桌面外壳，不提供正式安装包。

## DeepSeek 与 OSS 配置

服务端从根目录 `.env` 或进程环境读取 `ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 等字段，字段示例见 [.env.example](.env.example)。凭据只放受控后端环境，绝不使用 `VITE_` 前缀。当前没有写入任何真实密钥。

配置后，`GET /api/v1/health/ready` 会报告两项是否已配置，但不代表外部调用成功。要进行最小实测，可在后端目录执行 `python check_providers.py`；它会调用一次付费 DeepSeek 请求，OSS 使用不存在的对象做 HEAD 权限探测。生产联调仍需验证 Bucket/RAM/CORS、文件直传与内容核验。

## 仓库边界

`apps/website`、`apps/workbench`、`apps/desktop` 独立入口；`packages/workbench` 暂时保留原型 UI；`packages/contracts` 存放接口契约；`backend` 为独立 Python 服务。合同审查系统及其生产配置不在本仓库内。
