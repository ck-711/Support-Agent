# DOTS Survivors 游戏客服 Agent

面向 [DOTS Survivors](https://store.steampowered.com/app/3326810/DOTS_Survivors/) 的玩家客服系统。玩家可登录后咨询游戏问题、查看自己的历史对话、提交问题并接收人工客服回复；员工可审核知识资料、处理工单和查看完整对话上下文。

**玩家入口：** `/support` · **员工入口：** `/support/admin` · **API 文档：** `/docs`

> English overview: A FastAPI and LangGraph player support application with account-bound conversations, reviewed knowledge, Milvus retrieval, tool calling, and staff handoff. See the [English operations guide](docs/game-support.md).

## 已实现的能力

| 模块 | 功能 |
| --- | --- |
| 玩家会话 | 账号注册与登录、按玩家隔离的历史记录、中文/英文/西班牙文界面、回答反馈与数据删除 |
| AI 客服 | LangGraph 对话流程、已发布资料检索、来源引用、无法核实时转交人工 |
| 工具调用 | 查询已发布知识、查询本人工单、创建人工转接工单；会话身份由服务端注入 |
| 员工工作台 | 工单领取、回复与状态管理；在原对话中继续服务玩家 |
| 统一文档知识库 | 手工编写正文，以及 TXT、MD、PDF、DOCX 和图片上传；草稿审核后发布 |
| RAG | 文档解析、本地 Tesseract OCR、切块、关键词候选、1024 维向量、统一 Milvus 检索与发布状态校验 |
| 图片 | 员工上传到私有 MinIO；审核图片描述后，玩家聊天可显示被引用的图片 |

员工工作台只有一个文档知识库入口。文件上传和手工正文都进入 Celery 队列；页面持续显示解析、切块、向量化与索引进度。

## 技术结构

```mermaid
flowchart LR
    玩家[玩家 /support] --> API[FastAPI]
    员工[员工 /support/admin] --> API
    API --> Agent[LangGraph 客服 Agent]
    Agent --> Milvus[(Milvus 知识原文、审核状态、切块与向量)]
    API --> PG[(PostgreSQL 账号、会话、工单与进度)]
    API --> MinIO[(MinIO 图片与临时文件)]
    API --> Redis[(Valkey / Redis 协议队列)]
    Redis --> Celery[Celery 解析与索引 worker]
    Celery --> MinIO
    Celery --> Milvus
    Celery --> PG
```

| 组件 | 用途 |
| --- | --- |
| FastAPI + LangGraph | 鉴权、客服 API、对话编排与工具调用 |
| Milvus | 知识原文、元数据、审核状态、关键词、切块和向量检索 |
| Celery + Valkey | 异步解析、OCR、向量化和重建索引 |
| PostgreSQL | 账号、会话、消息、工单、反馈和上传进度 |
| MinIO | 私有知识图片与临时待处理文件 |

AI 只使用已发布资料回答游戏事实。文档或条目修改后会回到草稿；员工复核来源、正文、切块和图片描述后再发布。高风险操作或资料不足时转人工，工单保留对话上下文。

## 快速启动（Windows PowerShell）

需要 Docker Desktop、Python 3.13 和 `uv`。复制配置模板，填入自己的数据库密码、JWT 密钥、MinIO 密钥及模型 API Key；不要把 `.env.development` 提交到仓库。

```powershell
Copy-Item .env.example .env.development
# 编辑 .env.development
docker compose --env-file .env.development up -d --build app support-worker
docker compose --env-file .env.development exec app /app/.venv/bin/alembic upgrade head
docker compose --env-file .env.development exec app /app/.venv/bin/python -m scripts.migrate_support_knowledge
```

打开 `http://127.0.0.1:8000/support`。Docker Compose 会启动 PostgreSQL、Valkey（Redis 协议）、MinIO、Milvus、etcd 和 Celery worker。最后一条命令会把初始化或旧版 PostgreSQL 中的知识资料写入 Milvus 并核对；可重复运行。

PowerShell 默认没有 GNU Make，直接运行上面的 `docker compose` 命令即可。纯本机 Python 启动、OCR 安装和常见问题见[中文运行指南](docs/game-support.zh-CN.md)。

## 配置员工账号

1. 在 `/support` 注册玩家账号，或在 `/docs` 调用 `POST /api/v1/auth/register`。
2. 将注册结果中的数字用户 ID 写入 `.env.development` 的 `SUPPORT_ADMIN_USER_IDS`；多个 ID 用逗号分隔。
3. 重启应用，在 `/support/admin` 登录。员工权限由服务端 ID 白名单控制。

```powershell
docker compose --env-file .env.development up -d --force-recreate app
```

## 知识库使用流程

1. 员工在同一个文档知识库中建立正文，或上传不超过 50 MiB 的 TXT、MD、PDF、DOCX、PNG、JPG、WEBP 文件。
2. Celery worker 提取文字与图片中的 OCR 文本，按段切分，生成关键词候选和向量。员工可查看正文、切块和实时处理进度。
3. 员工核对来源、游戏版本、关键词及图片描述后发布。只有已发布内容会进入玩家问答。
4. 更改已发布资料或重建索引后，需重新审核并发布。切换 embedding 模型后，可在工作台逐份重建索引。

Milvus 的 `support_catalog_v1` collection 保存文档元数据、原文、审核状态、关键词、切块和图片描述向量；图片二进制保存在 MinIO。PostgreSQL 不再参与知识检索或审核，旧表仅供迁移。迁移核对成功且新工作台可用后，可运行 `python -m scripts.migrate_support_knowledge --purge-legacy` 清理旧表数据。Milvus 不可用时，知识检索不可用。详细限制、图片处理和模型配置见[中文运行指南](docs/game-support.zh-CN.md)。

## 开发与验证

```powershell
uv sync
uv run ruff check .
uv run pyright
uv run pytest tests
```

需要数据库的集成测试须先启动依赖并完成迁移，再设置 `SUPPORT_INTEGRATION=1`。API 位于 `app/api/v1/`，客服业务逻辑位于 `app/services/`，前端静态文件位于 `app/static/support/`，迁移位于 `alembic/versions/`。

## 文档与许可

- [中文运行指南](docs/game-support.zh-CN.md)
- [English operations guide](docs/game-support.md)
- [数据库说明](docs/database.md)
- [MIT 许可证](LICENSE)

游戏客服功能由本仓库维护者开发，项目沿用 FastAPI LangGraph Agent 模板作为基础。原模板及其贡献者的提交历史和 MIT 版权声明保留在仓库中。
