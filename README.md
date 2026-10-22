# 同频 AI

本仓库实现 [《实施方案》](docs/实施方案.md) 中的响应式官网、Bot 网页工作台、Electron Windows 桌面端和 FastAPI 服务。官网与工作台分别构建；网页和桌面共用工作台业务界面。`packages/workbench/src/App.jsx` 保留交互原型用于官网演示，实际工作台入口是 `apps/workbench/src/ConnectedApp.jsx`，数据来自服务端。

## 已实现的链路

- 注册、登录、刷新/退出会话；工作空间、房间、邮箱邀请、成员访问；消息持久化和幂等键。
- 单连接 WebSocket 房间事件订阅与断线按序回放；两 Bot 按明确提及顺序运行，Bot 版本快照，流式结果入库与事件更新。
- Bot 创建、编辑新版本、启停、加入/移出房间；可选择读取附件、计算器、生成文件权限。
- 私有 OSS 限定键/大小的直传签名、HEAD 核验、PDF/DOCX/TXT/MD 文本提取；明确请求时生成 Markdown/DOCX/PDF 产物，存储确认后才显示文件卡片。
- Electron Main/Preload/Renderer 隔离、受限 IPC 请求、原生选文件和保存文件；Windows x64 NSIS 开发测试包。

## 本次验证

在 Windows / Python 3.12 / Node 22 环境：后端 `pytest` 通过 24 项、前端 Vitest 通过 4 项、两个前端构建通过、Alembic 已升级到 `0004_read_positions`、Electron 主进程和 Preload 语法检查通过。真实联调使用临时进程环境变量，验证了 DeepSeek 流式响应、原生 `tool_use` 计算器调用、两 Bot 顺序执行、OSS 直传/HEAD/解析/签名访问、Markdown/DOCX/PDF 产物写入和清理。测试数据库与文件留在忽略目录，未将凭据写入源码或 Git。

尚未完成的方案项在 [验收差距](docs/验收差距.md) 中逐项列出。**当前不能视为《实施方案》全部功能完成或正式生产验收通过。** Docker 部署配置已提供，但本机缺少 Docker，未执行容器集成测试；Windows 安装包未签名，也未进行图形界面安装验收。

## 本地启动

前端安装并分别启动：

```powershell
cd D:\dz-ai\tongpin-ai
npm ci
npm run website:dev
# 另一个终端：npm run workbench:dev
```

官网 `http://127.0.0.1:5173/`，工作台 `http://127.0.0.1:5174/`。生产构建 `npm run build` 输出 `dist/website`、`dist/workbench`。本地 UI 单测 `npm run test:ui`。

后端（先复制 `.env.example` 为根目录 `.env`，填写至少 40 字符的 `JWT_SECRET`；开发可用 SQLite）：

```powershell
cd D:\dz-ai\tongpin-ai\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# 另一个终端：.\.venv\Scripts\python.exe -m app.worker
```

API 文档在 `http://127.0.0.1:8000/docs`。开发模式会自动建表；生产必须先运行 Alembic。`DATABASE_URL` 指向 MySQL 时需要 `asyncmy` 驱动。

## 外部服务和密钥

模型和 OSS 凭据仅通过后端进程环境或受控 `.env` 注入，绝不能使用 `VITE_` 前缀或打包到桌面端。参考根目录 `.env.example`。真实联调脚本 `backend/check_stream.py` 与 `backend/live_e2e.py` 会产生付费模型请求和 OSS 临时对象；运行前在当前进程配置所需环境变量，脚本不打印密钥。OSS Bucket 对网页来源需配置限定来源的 POST/GET/HEAD CORS；开发来源已在测试 Bucket 上验证，生产域名需要另外加入。原始上传文件不由后端代理下载，访问由成员权限校验后返回 60 秒签名 URL。

## 桌面打包与部署

桌面开发先启动工作台 Vite，然后 `npm run desktop:dev`。`npm run desktop:pack:dev` 生成本机 API 地址的未签名 NSIS 开发测试包到 `dist/desktop`。正式打包需设置 `DESKTOP_API_BASE_URL`、`DESKTOP_WS_URL`、`DESKTOP_RELEASE=true` 并在 CI 配置签名证书；仓库不包含证书、更新源凭据或正式下载域名。官网桌面下载按钮默认指向 `/downloads/Tongpin-AI-0.1.0-x64.exe`，部署时必须发布对应安装包，或设置 `VITE_DESKTOP_DOWNLOAD_URL`。

`deploy/compose.yml`、两个 Dockerfile 和 Nginx 配置提供同域网站/工作台/API/WS + MySQL + 单 worker 的起点。它们需要受 TLS 保护的反向代理、生产域名 OSS CORS、私密环境变量和数据库备份流程。不要在未执行完整验收前直接投入生产。
