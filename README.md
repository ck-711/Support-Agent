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
| 知识资料 | 人工编写的知识条目，以及 TXT、MD、PDF、DOCX 和图片上传；草稿审核后发布 |
| RAG | 文档解析、本地 Tesseract OCR、切块、关键词候选、1024 维向量、统一 Milvus 检索与发布状态校验 |
| 图片 | 员工上传到私有 MinIO；审核图片描述后，玩家聊天可显示被引用的图片 |

目前员工工作台中“知识库”和“文档知识库”仍是两个管理区；文档解析和索引在上传请求中执行。统一管理界面及 Celery 后台任务属于下一步迭代，尚未包含在本次发布中。

## 技术结构

```text
玩家 /support ── FastAPI ── LangGraph 客服 Agent ── 已发布知识检索
                         │                         └─ Milvus：条目、文档切块、图片描述向量
员工 /support/admin ──────┤
                         ├─ PostgreSQL：用户、会话、消息、工单
                         └─ MinIO：员工上传的知识图片
```

AI 只使用已发布资料回答游戏事实。文档或条目修改后会回到草稿；员工复核来源、正文、切块和图片描述后再发布。高风险操作或资料不足时转人工，工单保留对话上下文。

## 快速启动（Windows PowerShell）

需要 Docker Desktop、Python 3.13 和 `uv`。复制配置模板，填入自己的数据库密码、JWT 密钥、MinIO 密钥及模型 API Key；不要把 `.env.development` 提交到仓库。

```powershell
Copy-Item .env.example .env.development
# 编辑 .env.development
docker compose --env-file .env.development up -d --build app
docker compose --env-file .env.development exec app /app/.venv/bin/alembic upgrade head
```

打开 `http://127.0.0.1:8000/support`。Docker Compose 会启动 PostgreSQL、MinIO、Milvus 和 etcd。首次启动或切换 embedding 模型后，可回填已发布资料：

```powershell
docker compose --env-file .env.development exec app /app/.venv/bin/python -m scripts.rebuild_support_milvus
```

PowerShell 默认没有 GNU Make，直接运行上面的 `docker compose` 命令即可。纯本机 Python 启动、OCR 安装和常见问题见[中文运行指南](docs/game-support.zh-CN.md)。

## 配置员工账号

1. 在 `/support` 注册玩家账号，或在 `/docs` 调用 `POST /api/v1/auth/register`。
2. 将注册结果中的数字用户 ID 写入 `.env.development` 的 `SUPPORT_ADMIN_USER_IDS`；多个 ID 用逗号分隔。
3. 重启应用，在 `/support/admin` 登录。员工权限由服务端 ID 白名单控制。

```powershell
docker compose --env-file .env.development up -d --force-recreate app
```

## 知识库使用流程

1. 员工新建有来源的知识条目，或上传不超过 50 MiB 的 TXT、MD、PDF、DOCX、PNG、JPG、WEBP 文件。
2. 系统提取文字与图片中的 OCR 文本，按段切分，生成关键词候选和向量。员工可查看正文、切块和上传进度。
3. 员工核对来源、游戏版本、关键词及图片描述后发布。只有已发布内容会进入玩家问答。
4. 更改已发布资料时，需重新审核并发布；切换 embedding 模型后运行回填脚本。

知识条目、文档切块和图片描述使用同一个 Milvus collection；PostgreSQL 保存可审核的原文与状态。Milvus 不可用时，检索仍有数据库关键词路径作为兜底。详细限制、图片处理和模型配置见[中文运行指南](docs/game-support.zh-CN.md)。

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
