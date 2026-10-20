# DOTS Survivors 官方客服项目

玩家页面：`/support`。管理员页面：`/support/admin`。项目在现有 FastAPI 服务中提供账号绑定的玩家会话、受控工具调用、文档知识库、带来源的游戏问答、自动转人工、工单回复与数据删除。客服使用独立的 LangGraph「检索已发布资料 → 受控工具调用 → 生成回答」流程。

## 本地启动

1. 准备 Python 3.13、PostgreSQL 与 pgvector，运行 `uv sync`（支持 Windows PowerShell）；在 Bash 环境中也可运行 `make install`。
2. 将 `.env.example` 复制为 `.env.development`。设置强随机 `JWT_SECRET_KEY`、数据库连接、`DEFAULT_LLM_MODEL` 及对应模型的 API Key。Qwen 默认模型使用 `DASHSCOPE_API_KEY`；OpenAI 模型使用 `OPENAI_API_KEY`。按需设置 Langfuse 凭据及 `LANGFUSE_TRACING_ENABLED=true`。
3. 运行数据库迁移。新增迁移会建立客服表、文档表、1024 维 pgvector HNSW 索引，并从 [Steam 商店页面](https://store.steampowered.com/app/3326810/DOTS_Survivors/) 初始化五条有来源的公开资料。
4. 启动服务，打开 `http://localhost:8000/support`。

在 Windows PowerShell 中，从仓库根目录运行：

```powershell
uv sync
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

本机运行迁移和服务时，`.env.development` 中的 `POSTGRES_HOST` 应指向本机数据库（通常是 `localhost`），不能使用只在 Docker 网络中解析的 `db`。本机页面使用 `http://127.0.0.1:8000/support` 和 `http://127.0.0.1:8000/support/admin`；若 Docker 或 WSL 同时占用 8000 端口的 IPv6 地址，`localhost` 可能访问到另一个服务实例。若已安装 GNU Make 与 Bash，也可使用 `make migrate` 和 `make dev`。在 Windows 上直接编写异步 psycopg 启动程序时，应使用 selector 事件循环；上述 Uvicorn reload 模式已处理这一点。

使用 Docker Compose 时，PowerShell 可运行 `docker compose --env-file .env.development up -d --build db app`，然后运行 `docker compose --env-file .env.development exec app /app/.venv/bin/alembic upgrade head`。

如果 Docker Hub 的基础镜像鉴权连接失败，可先从 AWS Public ECR 获取相同标签的公开镜像，再使用本地缓存构建。Debian 默认软件源返回 502 时，可传入备用镜像源：

```powershell
docker pull public.ecr.aws/docker/library/python:3.13.2-slim
docker tag public.ecr.aws/docker/library/python:3.13.2-slim python:3.13.2-slim
docker compose --env-file .env.development build --pull=false --build-arg DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn app
docker compose --env-file .env.development up -d --no-deps app
```

## 配置管理员

先通过 `/docs` 中的 `POST /api/v1/auth/register` 注册现有系统用户。密码须为 8–64 个字符，且至少包含一个大写字母、一个小写字母、一个数字及一个支持的特殊字符（例如 `!`、`@` 或 `#`）。记下返回的数字用户 ID，将该 ID 写入 `SUPPORT_ADMIN_USER_IDS`；多个管理员用逗号分隔。重启服务后，即可在 `/support/admin` 使用该账号登录。默认值为空，所有管理员接口均拒绝访问。管理员权限依赖 ID 白名单，不依赖可由公开注册抢占的邮箱地址。

管理员可查看工单及完整对话、修改状态、在原会话向玩家回复，以及创建、修改、发布有来源的知识条目。修改已发布条目会退回草稿，需再次审核发布。当前没有邮件通知。玩家在 `/support` 注册或登录后可跨设备查看自己的会话和回复；浏览器里已有的旧匿名会话可在首次登录时认领一次，旧令牌随即失效。

## 文档知识库与向量模型

员工工作台可上传不超过 50 MiB 的 UTF-8 TXT、Markdown、PDF、DOCX、PNG、JPG 或 WEBP。PDF 最多 200 页，解析后的正文最多 60 万字符，最多建立 800 个切块。系统读取正文，并用本地 Tesseract OCR 识别 PDF 页面和 DOCX 内嵌图片中的文字；直接上传的图片也会识别。识别文字会进入原文、切块和关键词候选。系统按 1200 字符与 180 字符重叠切分、批量生成 1024 维向量。发布时，手工知识条目、文档切块和审核过的图片描述都写入同一个 Milvus collection。PostgreSQL 保存原文、审核状态和旧向量，检索结果仍须经过实时发布状态与版本校验。PDF 切块保留页码。上传时可记录资料来源类型、语言、适用游戏版本和术语别名；员工可在“审核切块”中查看正文、修改关键词并保存。新文档和替换文件先成为草稿；员工核对来源、版本、OCR 正文和切块后发布，AI 才会检索到它。可重新索引或删除；重新索引也会退回草稿。

Docker 镜像内安装 Tesseract 与简体中文、英语、西班牙语语言包。Windows 本机运行服务时，需另行安装 Tesseract 和对应语言包，确保 `tesseract` 在 `PATH` 中，或在 `.env.development` 设置 `SUPPORT_OCR_COMMAND` 为可执行文件绝对路径；`SUPPORT_OCR_LANGUAGES` 默认为 `chi_sim+eng+spa`。OCR 只在上传或替换文件时运行，重建向量索引使用已保存的识别结果。已有文档需要重新上传原文件，才能提取此前遗漏的图片文字。每份文档最多处理 400 张图片，单张图片最多 2000 万像素，单次 OCR 最长 20 秒，整份文档的图片识别最长约五分钟。大文件需要更长的上传、识别和向量化时间。

默认优先使用已有 `DASHSCOPE_API_KEY` 与 `text-embedding-v3`；没有该 Key 时使用 `OPENAI_API_KEY` 与 `text-embedding-3-small`。也可通过 `SUPPORT_EMBEDDING_MODEL`、`SUPPORT_EMBEDDING_API_KEY` 和 `SUPPORT_EMBEDDING_BASE_URL` 接入输出 **1024 维** 的 OpenAI 兼容 BGE 服务。`SUPPORT_EMBEDDING_DIMENSIONS` 应保持 1024；更换维度时需要新 collection。更换模型后运行下述回填命令，以当前模型重新建立已发布资料的 Milvus 索引。未配置可用的 embedding Key 时，上传和新知识条目的发布会失败；旧资料仍有关键词检索兜底。

### Milvus 启动与旧资料回填

在 `.env.development` 配置 `SUPPORT_MINIO_ACCESS_KEY`、`SUPPORT_MINIO_SECRET_KEY` 和可用的 embedding Key。Docker Compose 会启动 Milvus、etcd，并复用现有 MinIO 的独立 `support-milvus` 桶；向量端口只绑定本机 `19530`，WebUI 位于 `http://127.0.0.1:19091/webui/`。PowerShell 运行：

```powershell
docker compose --env-file .env.development up -d --build app
docker compose --env-file .env.development exec app /app/.venv/bin/python -m scripts.rebuild_support_milvus
```

若构建时 Debian 官方源返回 502，可先在当前 PowerShell 会话执行 `$env:DEBIAN_MIRROR="mirrors.tuna.tsinghua.edu.cn"`，再重跑第一条命令。

首次部署或切换 embedding 模型时执行第二条命令。它将所有已发布知识条目、文档切块和图片描述写入同一个 Milvus collection，不改变审核状态；重复运行是安全的。直接在 Windows 上运行 API 时，先用 Compose 启动 `db minio milvus`，将 `SUPPORT_MILVUS_URI` 设置为 `http://127.0.0.1:19530`，再运行 `uv run python -m scripts.rebuild_support_milvus`。新发布的资料会自动写入 Milvus。MinIO 用不同桶存储图片和 Milvus 数据。

MinerU 目前是可选的解析增强，适合现有 OCR 在多栏、复杂表格、公式或扫描版 PDF 上无法保留结构的资料；普通游戏公告、FAQ 和图文说明先使用现有解析流程。

### 知识库图片

员工打开文档的“审核切块”，可附加 PNG、JPG 或 WEBP 图片（每张最多 10 MiB，每份文档最多 20 张），填写对应页码和描述。图片经解码、缩放、去元数据后保存到私有 MinIO 桶；数据库只存对象键、审核描述和 1024 维描述向量。点击“视觉模型生成描述草稿”会把图片发送到配置的视觉模型，结果只填入编辑框；员工须核对游戏 UI、版本及文字后手动保存。已有 `DASHSCOPE_API_KEY` 时默认使用 `qwen3-vl-plus`，也可设置 `SUPPORT_VISION_MODEL`、`SUPPORT_VISION_API_KEY`、`SUPPORT_VISION_BASE_URL`，留空模型名则关闭建议功能。OCR 仍负责提取图片中的文字。

上传、修改或删除图片会使文档退回草稿；每张图片都要有审核描述才能发布。已发布描述参与文字和向量检索；AI 引用图片时，玩家聊天显示缩略图、描述及原文链接。员工预览走鉴权接口，玩家图片接口只提供已发布文档的图片。删除文档会清除对应对象。当前只支持员工附加图片，不接收玩家上传图片，也不自动保存 PDF/DOCX 中的每张嵌入图。

Docker Compose 将 MinIO API 与控制台分别绑定到本机 `19000` 和 `19001`，数据保存在 `minio-data` 卷；浏览器可打开 `http://127.0.0.1:19001` 查看对象。设置 `.env.development` 中的 `SUPPORT_MINIO_ACCESS_KEY` 和长随机 `SUPPORT_MINIO_SECRET_KEY`。更新后执行 `docker compose --env-file .env.development up -d --build minio app`，再运行 `docker compose --env-file .env.development exec app /app/.venv/bin/alembic upgrade head`。Compose 使用固定摘要的社区源码构建镜像，因为 MinIO 官方社区预编译镜像已停止发布；生产环境可替换为自行从 MinIO 源码构建的镜像。

## 工具调用与人工转接

Agent 可调用 `search_knowledge`、`list_my_tickets`、`request_human_handoff` 三个白名单工具；玩家也可通过 `POST /api/v1/support/tools/{tool_name}` 调用。玩家先登录，请求头使用 `Authorization: Bearer <账号 JWT>`，请求体为 `{"arguments": {...}}`。服务器把会话 ID 注入工具执行器，模型和请求参数都不能指定其他玩家的会话。工具只会搜索已发布知识、查看本玩家工单或创建人工工单，不会操作 Steam、付款或存档。

回答缺少可引用资料、出现明确故障、复杂问题，或涉及退款、付款、账号、密码、存档恢复等高风险操作时，系统会创建或复用待处理人工工单。工单保留转接原因和创建时的完整对话快照；员工通过 `GET /api/v1/support/admin/tickets/{ticket_id}/context` 读取当前完整对话，在原会话回复。玩家页会在工单创建后提示转接。

员工页面右上角可切换中文、英语或西班牙语。玩家页与员工页使用统一的暗色客服界面。

## 运行边界

- 玩家可使用英语、西班牙语或中文界面及对话；当前首批知识原文为英语。模型不可用时，匹配到的原文资料会连同来源显示。
- AI 只依据已发布资料回答游戏事实，不会确认未核实的 Bug、承诺修复时间或保证存档恢复。无法核实或需人工处理时自动转接；玩家也可手动提交。
- 不接入 Steam 账号、游戏存档或遥测，也不修改玩家数据。工单仅收集描述、游戏版本和设备信息；请勿收集密码、付款资料或无关个人信息。
- 应用数据库中的会话与工单最多保存 30 天；每日清理，玩家也可即时删除。第三方模型和 Langfuse 的保存期限需单独配置。
- `SUPPORT_LLM_TIMEOUT_SECONDS` 默认为 20 秒。模型不可用时，系统仅提供匹配的已发布资料及来源，或明确表示无法核实；故障报告仍可进入人工队列。

## 验证

运行 `uv run ruff check .`、`uv run pyright` 和 `uv run pytest tests/test_support.py`。数据库完成迁移后，设置 `SUPPORT_INTEGRATION=1`，运行 `uv run pytest tests/test_support_integration.py tests/test_support_rag_integration.py tests/test_support_account_integration.py`。集成测试覆盖真实 HTTP、PostgreSQL、pgvector、账号隔离、历史分页、草稿发布、LangGraph、工单、人工回复和删除，模型及向量响应使用测试替身，不调用外部 API。

完整 API 路由和数据流见 [英文参考文档](game-support.md)。

## v1.1–v1.3 迭代

- **人工接管**：员工可领取或释放待处理工单。工单处于“待处理/调查中”时，玩家的新消息继续保存到原对话，AI 暂停回答；员工在同一对话回复。工单标记“已解决/已关闭”后，下一条玩家消息重新交给 AI。玩家页和员工页通过带身份请求头的 SSE 流每两秒感知消息及工单变化；断线后保留手动刷新。
- **反馈与质量**：玩家可对每条 AI 回答评价“有帮助/没帮助”；反馈只允许写入自己的 AI 消息，并保存在数据库。员工页显示反馈数、待领取工单数及到期知识数。运行 `uv run python -m evals.support_eval --output support-eval.json --baseline evals/baselines/v1.3.1.json` 会对 18 条合成的中英西问题测试引用标题、人工转接和延迟，并与已保存基线比较。评测需要数据库与模型服务，不会创建真实工单。该数据集是回归样本，不代表真实玩家流量；生产反馈可用于后续人工标注并扩充样本。
- **知识检索**：客服和 `search_knowledge` 工具优先检索统一的 Milvus collection；旧版 PostgreSQL 全文/关键词和向量查询保留为回填期间的可用性兜底。PDF 在解析及切分时保留页码，引用链接可打开对应页的提取文本。发布的知识条目和文档设定 90 天复审日期，员工页显示到期提醒；替换或重建文档后需重新发布。旧 PDF 如需页码引用，须重新上传原文件。

升级已有环境时运行 `uv run alembic upgrade head`（Windows 也可运行 `.\.venv\Scripts\python.exe -m alembic upgrade head`）。新迁移为会话增加账号归属，并建立历史分页索引。旧匿名对话仍保留至 30 天清理期限；在原浏览器登录账号时可认领。玩家 API 已不再接受单独的旧匿名令牌。
