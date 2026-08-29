# AQUA Gateway

![Version](https://img.shields.io/badge/version-12.1.0-00d4ff) ![Python](https://img.shields.io/badge/Python-3.12%2B-blue) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15%2B-336791) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009485) ![License](https://img.shields.io/badge/License-MIT-green)

> **开源 AI 中转网关**：将多个 NVIDIA NIM 上游密钥池化，对外提供统一的 OpenAI 兼容 API。
> 下游客户端持 `sk-` 密钥调用，网关负责认证、17 算法调度选池、转发上游、限流与日志。
>
> **版本 v12.1.0** · **协议 MIT** · **语言 中文（简体）**

---

## 目录

1. [v12.1 版本亮点](#v121-版本亮点)
2. [v12.0 版本亮点](#v120-版本亮点)
3. [功能特性](#功能特性)
4. [系统架构](#系统架构)
5. [快速开始](#快速开始)
6. [环境变量](#环境变量)
7. [API 使用](#api-使用)
8. [管理控制台](#管理控制台)
9. [代理池](#代理池)
10. [模型测试](#模型测试)
11. [安全体系](#安全体系)
12. [调度与算法](#调度与算法)
13. [并发与限流](#并发与限流)
14. [多协议转换（实验性）](#多协议转换实验性)
15. [生产部署](#生产部署)
16. [运维手册](#运维手册)
17. [开发与测试](#开发与测试)
18. [项目结构](#项目结构)
19. [版本历史](#版本历史)
20. [开源协议](#开源协议)

---

## v12.1 版本亮点

v12.1 为纯中转网关补上**出网通道治理**：上游密钥不再只能直连，可按密钥粒度选择代理。

| 方向 | 变更 |
|------|------|
| **代理池** | 新增 `proxies` 表与 `/gw/admin/proxies` 增删改查 + 连通性测试，支持 `socks5` / `socks5h` / `http` / `https` |
| **认证方式** | 同时支持无认证代理与「用户名 + 密码」代理；密码经独立 HKDF salt 加密存储、永不回显 |
| **三种出网模式** | 每个上游密钥可选 `direct`（直连）/ `bind`（绑定池内指定代理）/ `rotate`（活跃代理轮询） |
| **控制台** | 新增独立「代理池」标签页（页面 10 → 11）；上游密钥列表新增「出网」列与出网方式选择 |
| **探活一致性** | 三条密钥探活路径与 `/upstreams/health-check` 全部改走该密钥自己的出网通道，代理专用密钥不再被误判停用 |
| **模型测试** | 新增「模型测试」页（11 → 12 页）与 `/gw/admin/model-test/*`：实时模型列表 + 搜索/全选 + 并发批量探测，可选「直连 NVIDIA 上游」或「走本网关中转」两条通道 |
| **容器化** | 新增 `Dockerfile`（多阶段、非 root、内建健康检查）+ `docker-compose.yml`（网关 + PostgreSQL 17，默认拉 CI 预构建镜像）+ `docker-compose.local.yml`（本机源码构建覆盖层）+ `.dockerignore`，`docker compose pull && docker compose up -d` 起全栈 |
| **CI/CD** | 新增 `.github/workflows/docker-image.yml`：推送即跑「后端单测 + 前端静态检查」，全绿才构建镜像并推送到 GHCR，无需配置任何 Secret |
| **工程化** | 测试基线 105 个后端单测 + 28 项前端静态检查；新增依赖 `httpx[socks]` |

---

## v12.0 版本亮点

v12.0 将项目**收敛为纯中转网关**（类似 CLI proxy API 形态）：只做"上游 NV 密钥池 → 下游 `sk-` 密钥"的转换与转发。

| 方向 | 变更 |
|------|------|
| **单服务化** | 删除 `platform/` 用户平台服务（注册 / 登录 / 聊天 / 邮件 / 用量页），仓库只保留 `gateway/` 一个进程、一个数据库 |
| **认证收敛** | 彻底移除机器间 `platform_tokens` 令牌机制（表 / 端点 / ORM / scope / 前端页）；管理面只保留管理员密码认证 |
| **依赖解耦** | 移除网关对 platform（:8001）的反向 HTTP 依赖（`/system/user-stats`、`/system/health`）及其前端消费卡片 |
| **瘦身** | 删除 platform 专属运维脚本（`scripts/`）；控制台页面 12 → 10；`.env` 变量 26 → 14 |
| **工程化** | 测试基线 25 个后端单测 + 16 项前端静态检查（新增"已下线端点不得残留"回归守卫） |

---

## 功能特性

| 特性 | 说明 |
|------|------|
| **密钥池化** | 多个 NVIDIA NIM 上游密钥整合为统一入口，密钥 Fernet+HKDF 加密存储 |
| **密钥转换** | 下游签发 `sk-` 客户端密钥（仅存 SHA-256 哈希查库），与上游密钥完全隔离 |
| **统一 API** | OpenAI 兼容：`/v1/chat/completions`、`/v1/embeddings`、`/v1/models` |
| **17 算法互锁调度** | RPM、健康度、冷却、预热、熔断等 17 个协同算法计算最优密钥选择 |
| **故障自愈** | 429/5xx 熔断（半开探测恢复）、自适应冷却、冷密钥渐进预热、自动故障转移 |
| **模型 ID 映射** | 数百条别名、6 级匹配策略，非标准 ID 自动纠错映射 |
| **代理池** | SOCKS5 / HTTP 代理入池（可带账号密码），上游密钥可直连 / 绑定代理 / 池内轮询 |
| **模型连通性测试** | 控制台批量探测：实时模型列表、模型搜索/全选、可自定义提示词，支持「直连上游」与「走本网关中转」双通道对照 |
| **管理控制台** | 零构建网关控制台（12 页面）+ SQLAdmin 数据库面板 |
| **多级缓存** | L1/L2 两级进程内内存缓存（LRU + TTL）：API Key 缓存、限流计数、TPM/RPM 追踪 |
| **安全体系** | bcrypt(12) 管理密码、SHA-256 密钥哈希查库、IP 监控、可信代理模型、请求体 10MB 硬限 |

**如实声明**：多协议转换（Anthropic/Gemini）当前为**内置转换器、实验性、未接入主链路**（详见[多协议转换](#多协议转换实验性)）；分布式缓存（Redis）未引入；仅支持 PostgreSQL；无用户注册体系（密钥由管理员在控制台发放）。

---

## 系统架构

单服务架构，一个进程、一个数据库：

```
┌──────────────────────┐
│      IDE / 客户端      │
│ Cursor / 通用 SDK ...  │
└──────────┬───────────┘
           │ Bearer sk-xxx
           ▼
┌─────────────────────────────────────────┐
│              Gateway (:8000)             │
│  认证(SHA-256 查库) → 校验 → 17算法调度    │
│  → httpx 上游池 → SSE/JSON 响应           │
│  控制台 /admin · SQLAdmin /gw/dbadmin     │
└──────────────────┬──────────────────────┘
                   │ 上游密钥池（Fernet 加密）
                   │ 出网通道：直连 / 绑定代理 / 代理池轮询
                   ▼
        NVIDIA NIM (integrate.api.nvidia.com)
```

- **入口**：`/v1/*` 面向 API 客户端（下游 `sk-` 密钥认证）；`/gw/admin/*` 面向管理控制台（管理员密码）
- **两级密钥**：`upstream_keys`（上游 NV 密钥，加密存储）→ `clients` / `client_api_keys`（下游密钥，哈希查库）
- **数据库**：单个 PostgreSQL 库 `aqua_gateway`，psycopg2 同步池 + asyncpg 异步池
- **进程约束**：调度器算法为进程内状态，**必须单 worker 部署**

---

## 快速开始

### 1. 环境要求

**裸机部署**：

- Python **3.12+**（开发验证于 3.12，推荐 3.13+）
- PostgreSQL **15+**（必需，无 SQLite 模式）
- NVIDIA NIM API 密钥（[build.nvidia.com](https://build.nvidia.com)）
- 无需 Node.js（前端零构建）

**Docker 部署**：只需 Docker **24+** 与 Compose **v2**（`docker compose version` 可验证）+ NVIDIA NIM 密钥，本机无需 Python 与 PostgreSQL；直接跳到[第 6 步](#6-或者docker-一键启动)。

### 2. 安装

```bash
git clone https://github.com/buyi06/aqua-platform-open.git
cd aqua-platform-open

python3 -m venv venv && source venv/bin/activate
pip install -r gateway/requirements.txt

cp .env.example .env   # 然后编辑 .env，逐项填写
```

### 3. 准备数据库

```sql
CREATE USER aqua WITH PASSWORD '强密码';
CREATE DATABASE aqua_gateway OWNER aqua;
```

### 4. 必填环境变量

打开 `.env`，**至少**配置以下两项（完整清单见[环境变量](#环境变量)）：

| 变量 | 说明 |
|------|------|
| `ACU_ADMIN_PASSWORD_HASH` | 管理员密码 bcrypt 哈希（Admin API 必需，缺失则启动报错） |
| `PG_PASSWORD` | Gateway 数据库密码（缺失则启动报错） |

### 5. 启动

```bash
cd gateway && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/admin` 用管理员密码登录：

1. 在「上游密钥」页录入 NVIDIA NIM 密钥（可批量）
2. 在「下游客户」页创建客户端并签发 `sk-` 密钥（**明文仅在签发时返回一次**）
3. 客户端即可通过 `http://127.0.0.1:8000/v1` 调用

> 生产部署（反代 / HTTPS / systemd）见[生产部署](#生产部署)。

### 6. 或者：Docker 一键启动

不想装 Python/PostgreSQL 的话，用 Compose 起「网关 + PostgreSQL 17」两个容器即可（跳过上面第 1～3、5 步，只需准备 `.env`）：

```bash
cp .env.example .env       # 至少填 ACU_ADMIN_PASSWORD_HASH 与 PG_PASSWORD
docker compose pull        # 拉 CI 预构建镜像（GHCR），不在本机编译
docker compose up -d
docker compose logs -f gateway
```

改了源码要立刻验证时，叠加 `docker-compose.local.yml` 改为本机构建：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

默认映射到 `http://127.0.0.1:8000`（宿主地址与端口由 `.env` 的 `GW_BIND`/`GW_PORT` 控制），建库、建表与迁移在容器首次启动时自动完成。运维细节见[Docker Compose 部署](#docker-compose-部署)。

> ⚠️ **bcrypt 哈希必须用单引号包裹**：`ACU_ADMIN_PASSWORD_HASH='$2b$12$...'`。哈希含 `$`，docker compose 会对 `env_file` 里的 `$xxx` 做变量插值，不加引号会把哈希悄悄截断，表现为密码怎么都登不上。

---

## 环境变量

完整模板见 [`.env.example`](.env.example)（含随机值生成命令）。**应用读取 14 项**，另有 **6 项仅供 `docker-compose.yml` 使用**（应用本身不读）。

加载与优先级：

- 网关启动时由 `gateway/app/main.py` 加载**仓库根目录**的 `.env`（容器内即 `/app/.env`）；`load_dotenv` **不覆盖**已存在的环境变量，故「容器/systemd 传入的环境变量 > `.env` 文件」
- 除 `AQUA_TRUST_PROXY_HEADERS`（每请求读取）外，其余均在模块导入时读取一次，**改动后必须重启进程/容器**才生效
- `.env` 权限建议 `600`；已列入 `.gitignore` 与 `.dockerignore`，不会进仓库和镜像层

### 必填（缺失则启动失败）

| 变量 | 校验时机 | 缺失表现 |
|------|------|------|
| `ACU_ADMIN_PASSWORD_HASH` | 导入 `admin_api.py` 时 | 直接 `RuntimeError: [FATAL] 环境变量 ACU_ADMIN_PASSWORD_HASH 未设置！`，进程起不来 |
| `PG_PASSWORD` | 首次建连接池时（惰性，导入不失败） | `RuntimeError: [FATAL] 环境变量 PG_PASSWORD 未设置！` |

### 认证与会话

| 变量 | 默认 | 取值 | 说明 |
|------|------|------|------|
| `ACU_ADMIN_PASSWORD_HASH` | — | bcrypt 哈希（推荐 12 rounds，形如 `$2b$12$...`） | 管理员密码。`/gw/admin/*`（控制台）**只认这一项**；SQLAdmin 面板也优先用它。哈希格式非法（盐/长度不符）按配置错误处理，一律拒绝登录。⚠️ Docker 下必须用单引号包裹，见下方说明 |
| `ACU_ADMIN_PASSWORD` | — | 明文密码 | **仅** SQLAdmin 面板（`/gw/dbadmin`）在 `HASH` 未配置时的回退（恒定时间比较）；对 Admin API 无效。两项都为空时面板启动即打 `[FATAL]` 并禁用一切登录 |
| `ADMIN_SESSION_SECRET` | 随机 `token_hex(32)` | 随机串（建议 ≥ 32 字节） | SQLAdmin 面板 Session 签名密钥。未配置时每次启动生成临时值并打 warning，**重启后所有面板登录会话失效** |

生成方式：

```bash
# bcrypt 哈希（12 rounds）
python3 -c "import bcrypt; print(bcrypt.hashpw('你的密码'.encode(), bcrypt.gensalt(rounds=12)).decode())"
# 随机会话密钥
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

> ⚠️ **`ACU_ADMIN_PASSWORD_HASH` 在 Docker 下必须写成 `ACU_ADMIN_PASSWORD_HASH='$2b$12$...'`**（单引号）。哈希含 `$`，docker compose 会对 `env_file` 里的 `$xxx` 做变量插值，不加引号会把哈希**静默截断**（实测 `$2b$12$bdJq…` → `$2b$12.yz…`），现象是配置看着没错但管理员怎么都登不上。`python-dotenv` 会自动剥掉引号，裸机部署写单引号同样正确。

### 数据库

| 变量 | 默认 | 取值 | 说明 |
|------|------|------|------|
| `PG_HOST` | `localhost` | 主机名 / IP | Compose 下由 `docker-compose.yml` 覆盖为服务名 `db`，`.env` 里的值对容器无效；数据库跑在宿主机时填 `host.docker.internal` |
| `PG_PORT` | `5432` | 整数 | — |
| `PG_DB` | `aqua_gateway` | 库名 | Docker 下同时用于初始化 `db` 容器（`POSTGRES_DB`） |
| `PG_USER` | `aqua` | 用户名 | 同上（`POSTGRES_USER`） |
| `PG_PASSWORD` | — | 强随机密码 | **必填**。Docker 下同时用于初始化 `db`；未设置时 `docker compose up` 直接以 `PG_PASSWORD 未设置，请先在 .env 中填写` 中止 |
| `GW_DB_POOL_SIZE` | `30` | 整数，实际取 `max(5, 值)` | psycopg2 `ThreadedConnectionPool` 上限（`minconn` 固定 5）。非数字会导致启动 `ValueError` |

> 改 `PG_USER`/`PG_PASSWORD`/`PG_DB` 后，Docker 下**只有删除 `pgdata` 卷**（`docker compose down -v`，会丢数据）才会按新值重新初始化——PostgreSQL 官方镜像仅在数据目录为空时读这些变量。

### 网关行为开关

| 变量 | 默认 | 取值 | 说明 |
|------|------|------|------|
| `AQUA_TRUST_PROXY_HEADERS` | 空（= auto） | `1`/`true`/`yes`（不区分大小写）为强制信任，其余值均为 auto | 真实客户端 IP 来源。**auto 不等于"不信任"**：对端为私网/回环时仍会取 `CF-Connecting-IP` / `X-Forwarded-For`（最右侧非私网值），公网直连才忽略一切可伪造头。Cloudflare 等"对端也是公网"的全程代理场景才需要显式设 `1` |
| `AQUA_WATCHLIST` | 空 | 逗号分隔的客户端名称 | 商用检测监控名单，读取时自动 `strip` + 转小写；留空表示无名单 |
| `AQUA_DEBUG_ERRORS` | 空 | **严格等于 `1`** 才开启（`true`/`yes` 无效） | 开启后错误响应携带内部异常详情，仅排障用，生产必须保持关闭 |
| `LOBSTER_MAX_BYTES` | `20971520`（20MB） | 字节数（整数） | 文档解析输入大小上限。另有三项硬编码防护：PDF ≤ 500 页、zip 膨胀比 ≤ 100、zip 条目 ≤ 200 |

### CORS

| 变量 | 默认 | 取值 | 说明 |
|------|------|------|------|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:8000` | 逗号分隔的**完整 Origin**（含协议与端口） | 与 `allow_credentials=True` 同用，**禁止 `*`**。⚠️ 解析为纯 `split(",")` 不做 `strip`，逗号后**不能有空格**（`a.com, b.com` 会把 ` b.com` 当成非法 Origin）。改了 `GW_PORT` 或域名要同步这里 |

### Docker 专用（应用不读，仅 `docker-compose.yml` 引用）

| 变量 | 默认 | 说明 |
|------|------|------|
| `GW_BIND` | `127.0.0.1` | 宿主监听地址。`127.0.0.1` 仅本机可访问（最安全）；`0.0.0.0` 才对局域网/公网暴露，需自行叠加反代、HTTPS 与防火墙 |
| `GW_PORT` | `8000` | 宿主端口（容器内恒为 8000）。改动需同步 `CORS_ALLOWED_ORIGINS` 与反代 `proxy_pass` |
| `TZ` | `Asia/Shanghai` | 只影响 `gateway` 容器日志的时间显示。`db` 容器固定 `TZ/PGTZ=UTC`；写库时间戳恒为 UTC `Z`，控制台按浏览器本地时区渲染 |
| `PG_EXPOSE_PORT` | `5432` | 仅在取消 `docker-compose.yml` 中 `db.ports` 注释后生效（把库暴露到宿主 `127.0.0.1` 以便外部工具连入）；默认全程不暴露 |
| `GW_IMAGE` | `ghcr.io/qingdeng888/aqua-platform-open` | 网关镜像仓库地址。换成自己 fork 的 GHCR 包或私有 registry 时改这里，不必动 `docker-compose.yml` |
| `GW_IMAGE_TAG` | `latest` | 镜像标签。生产建议锁版本（如 `v12.1.0` 或 `sha-a1b2c3d`）而非跟随 `latest`；本机源码构建时该值被 `docker-compose.local.yml` 忽略 |

---

## API 使用

Base URL：`https://你的域名/v1`（直连即 `http://127.0.0.1:8000/v1`）

### 对话补全

```bash
curl https://你的域名/v1/chat/completions \
  -H "Authorization: Bearer sk-你的密钥" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/deepseek-v4-pro-0813",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(base_url="https://你的域名/v1", api_key="sk-你的密钥")
resp = client.chat.completions.create(
    model="nvidia/nemotron-3-super-120b-a12b",
    messages=[{"role": "user", "content": "介绍一下 AQUA"}],
)
print(resp.choices[0].message.content)
```

### 端点一览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | 对话补全（支持流式 / 工具调用 / 多模态透传） |
| `/v1/embeddings` | POST | 向量化 |
| `/v1/models` | GET | 模型列表（公开） |
| `/api/public/models` | GET | 公开模型目录（含能力标签，5 分钟缓存） |
| `/healthz` | GET | 健康检查（公开基础字段；`?verbose=1` 需管理员） |

> `/v1/*` 端点均有 `/api/v1/*` 同义别名，便于反代按前缀分流。

### 模型 ID 智能映射

用户输入非标准 ID 时自动映射（如 `deepseek-v4-pro` → `deepseek-ai/deepseek-v4-pro-0813`）。当前目录以 `gateway/app/nim_models.py` 为唯一权威来源，涵盖 NVIDIA NIM 实测可用模型（Nemotron-3 全系、DeepSeek-V4、Gemma-4、Kimi-K3、MiniMax-M3 等）。

---

## 管理控制台

| 入口 | 地址 | 认证 |
|------|------|------|
| 网关控制台 | `http://127.0.0.1:8000/admin` | 管理员密码登录（HMAC Token，24h） |
| SQLAdmin 面板 | `http://127.0.0.1:8000/gw/dbadmin` | 同一密码（Session） |

**密码契约**：优先校验 `ACU_ADMIN_PASSWORD_HASH`（bcrypt），未配置时回退 `ACU_ADMIN_PASSWORD`（恒定时间比较）；**两者均未配置时一切登录被拒绝**（杜绝空密码）。

控制台 12 个页面：仪表盘、上游密钥（含明文 reveal）、代理池、下游客户、桶监控、请求日志、算法引擎（17 算法实时状态）、系统监控、系统配置、错误码、商用检测、模型测试。

> v12.0 起管理接口只有**管理员密码**一条认证路径，不再存在机器间令牌（`platform_tokens`）与 scope 授权模型。若需程序化调用 `/gw/admin/*`，先 `POST /gw/admin/login` 换取 24h HMAC Token，再以 `Authorization: Bearer <token>` 携带。

---

## 代理池

上游密钥默认直连 NVIDIA NIM。若上游对出口 IP 敏感，或需要把不同密钥分散到不同出口，可在控制台「**代理池**」页录入代理，再为每个上游密钥指定出网方式。

### 代理配置

| 项 | 说明 |
|------|------|
| 协议 | `socks5`、`socks5h`（域名在代理端解析）、`http`、`https` |
| 认证 | 用户名与密码留空即为无认证代理；填写则按「用户名:密码」认证（凭据做 percent-encoding） |
| 状态 | `active` 参与绑定与轮询；`inactive` 立即退出（绑定它的密钥临时回退直连） |
| 连通性测试 | 经该代理访问上游 `/models`，拿到任意 HTTP 状态即视为通道可用，结果落库展示 |

代理密码使用**独立 HKDF salt**（`acu-proxy-credential-derivation`）加密存储，任何接口都不回显，列表仅返回 `has_auth` 布尔值。

> **Docker 部署注意**：代理跑在宿主机上时，主机地址要填 `host.docker.internal`（`docker-compose.yml` 已配 `host-gateway` 映射），填 `127.0.0.1` 指向的是网关容器自身，连通性测试必然失败。

### 上游密钥出网模式

| 模式 | 行为 |
|------|------|
| `direct` | 直连（默认） |
| `bind` | 绑定池内指定代理；该代理被删除或停用时**回退直连并告警**，不让请求直接失败 |
| `rotate` | 在活跃代理间轮询（进程内游标）；池内无活跃代理时回退直连 |

删除代理会把绑定它的上游密钥自动改回 `direct`，接口返回受影响密钥数。

### 实现要点

- 出网解析集中在 `gateway/app/proxy_pool.py`：`build_proxy_url()` 拼装 URL，`build_client()` 是直连池与代理池**唯一**的 httpx 客户端工厂（保持非流式 120s / 流式 600s 与 `limits(100/20, keepalive 60s)` 同一口径）
- 热路径零 DB 查询：活跃代理明文 URL 与 `key → (mode, proxy_id)` 绑定关系以 **5 秒 TTL 快照**缓存，管理端每次增删改主动 `invalidate()`；快照刷新时关闭失效代理的客户端，避免连接泄漏
- 客户端按 `(代理URL, 是否流式)` 复用
- 探活一致性：异步健康检查、无效模型清理、30 分钟停用密钥复活探测、`/upstreams/health-check` **全部**走该密钥自己的出网通道——否则代理专用密钥会因直连不通被误判停用
- 依赖：SOCKS 支持来自 `httpx[socks]`（`socksio`），已声明于 `gateway/requirements.txt`
- 约束：轮询游标与客户端缓存均在进程内，沿用全局**单 worker** 约束

---

## 模型测试

控制台「**模型测试**」页用于批量验证模型可用性：模型清单**实时**取自上游 `/models`（复用公开接口的 60 秒缓存，右上角「刷新模型」强制回源），支持按 ID / 名称搜索、全选、选中匹配项。

默认提示词为 `你是什么模型，你可以帮我干什么事情`，可在页面自定义（留空回落默认）；`max_tokens` 默认 **256**（推理模型的思维链会先吃掉输出配额，给太小只能拿到 `reasoning_content` 拿不到正文），上限 512；并发 1–16，运行中可随时「中止」，切换页面自动收手。

### 两条测试通道

| 通道 | 链路 | 用途 |
|------|------|------|
| **直连 NVIDIA 上游** | 浏览器 → `POST /gw/admin/model-test/probe` → 后端调度器选一个健康上游密钥，走该密钥自己的出网通道（`direct`/`bind`/`rotate`）直连上游 | 排除网关自身因素，验证上游与出网通道是否通 |
| **走本网关中转** | 浏览器持「内置自测密钥」→ `POST /v1/chat/completions` → 完整经过下游认证 → 调度选池 → 转发 → 落日志 | 端到端验证中转链路，结果同时出现在「请求日志」页 |

浏览器**永不接触上游密钥明文**：直连通道由后端代为请求，响应只回结构化结果与掩码密钥（`key_masked`），出网信息只回 `direct` / `proxy` 两态——代理 URL 内嵌账号密码，绝不外发（有源码级守卫测试）。

### 内置自测密钥

「走本网关中转」需要一把真实的下游 `sk-` 密钥。网关为此维护一把**随机生成、部署内固定**的自测密钥：

- 归属专用客户 `__console_selftest__`（双下划线前缀标识「非真实客户」），首次索取时随机生成（`sk-` + 32 位），Fernet 加密落库后固定复用同一把
- 它与普通下游密钥**完全等价**：同样计入调度、限流、日用量、请求日志与商用检测，因此仅供控制台自测，不应外发
- 明文只下发给管理员认证的调用方，前端只存在于模块闭包，**不写 localStorage / sessionStorage / cookie**（有静态检查守卫）
- 疑似泄露时点「轮换密钥」：删除旧密钥并重新随机生成，同时失效调度器的客户端密钥缓存（旧密钥立即 401）
- 密钥的下发与轮换写审计日志；**批量探测本身不逐条写审计**，否则一次全模型测试就会刷爆 `audit_logs`

### 端点与实现要点

| 端点 | 说明 |
|------|------|
| `GET /gw/admin/model-test/models` | 实时模型列表（`refresh=1` 跳过 60 秒缓存回源） |
| `GET /gw/admin/model-test/selftest-key` | 取内置自测密钥（不存在则随机生成并落库） |
| `POST /gw/admin/model-test/selftest-key/rotate` | 轮换内置自测密钥 |
| `POST /gw/admin/model-test/probe` | 单模型直连上游探测（单次语义） |

- 后端独立成模块 `gateway/app/model_test.py`（不进 `admin_api.py`），四个端点均需管理员 Token
- **批量编排在前端**（并发池 + `AbortController` + 逐行就地更新）：后端只提供单模型探测，避免长任务占住事件循环，也让中转通道走的是「浏览器 → 网关」的真实请求
- 判定口径：HTTP 200 且能提取到回复才算通过；回复提取按 `message.content` → `message.reasoning_content` → `choices[0].text` 依次回落，前后端同语义

---

## 安全体系

- **密钥安全**：上游密钥 Fernet+HKDF 加密存储（salt `acu-upstream-key-derivation`）；下游客户端密钥仅存 SHA-256 哈希（查库认证）+ Fernet 密文（salt `acu-client-key-derivation`，发放时一次性返回明文）；代理密码 Fernet 密文（salt `acu-proxy-credential-derivation`，永不回显）；列表接口不回显密文与哈希
- **派生隔离**：上游 / 下游 / 代理凭据三条 HKDF 派生路径互不通解（有专门单测守卫）
- **认证**：管理密码 bcrypt(12)；管理 Token 为 HMAC-SHA256 签名 + 24h 过期 + `role=admin` 校验
- **防滥用**：管理登录 10 次/分钟/IP、其他管理接口 60 次/分钟/IP；上游 429/5xx 熔断与自适应冷却
- **网络层**：可信代理模型（公网直连忽略一切伪造头，私网对端才信任 CF/XFF）；CORS 白名单；请求体 10MB 硬限（含 chunked 编码）
- **Web 安全**：控制台零内联事件 / 零内联脚本，全量 `esc()` 转义（敏感值不进内联属性），由静态检查强制
- **隐私**：日志请求体脱敏（`[REDACTED]`）、响应体截断 8KB、成功日志 3 天自动清理
- **凭据隔离**：下游密钥不转发上游，上游密钥不回传下游，请求头在网关层重建

---

## 调度与算法

调度器（`SurgeScheduler`）以 **17 个互锁算法**协同工作，从请求进入到响应完成全程介入：

| 阶段 | 算法（节选） |
|------|------|
| 密钥选择 | RPM 均值、健康度评分、权重、冷却水位、冷密钥预热 |
| 运行时 | 熔断器（CLOSED/OPEN/**HALF_OPEN** 探测恢复）、并发槽位、慢速模型超时 |
| 自愈 | 阈值自适应、健康探测、自动停用 + **30 分钟探活恢复**、桶解冻 |
| 观测 | 桶快照、错误追踪、P95 延迟、调度耗时（请求级 ContextVar） |

> 算法仅依赖进程内状态，**必须单 worker 部署**（`uvicorn` 不加 `--workers`）；多实例需各自独立数据库分片。算法细节见 [ARCHITECTURE.md](ARCHITECTURE.md) 第 4 章权威表。

---

## 并发与限流

**无硬性并发数限制**（不因并发拒绝请求），保留以下机制：

- **上游熔断**：429/5xx 触发密钥级熔断与冷却，请求自动转移到池内其他密钥
- **IP 监控**：滑窗 RPS + 周期性检测，高分异常 IP 自动封禁 24h（自动过期解封）
- **管理接口 IP 限流**：登录 10 次/分钟、其他 60 次/分钟

> 完整口径见 [docs/rules/concurrency-rules.md](docs/rules/concurrency-rules.md)。v12.0 起软限流排队（原 platform 侧 `soft_limiter`）已随平台模块一并下线。

---

## 多协议转换（实验性）

仓库内置 Anthropic / Gemini 双向协议转换器（`gateway/app/translator.py`、`gateway/app/transformers/`），支持 tool_use/tool_result、多模态块、流式事件序列（符合 Anthropic SDK 规范）。

> **当前状态：未接入主链路。** 生产请求为 OpenAI 协议直通（零转换损耗）。相关模块已修复并标注实验性，启用需在 `public_api` 增挂对应路由（如 `/v1/messages`），欢迎 PR。

---

## 生产部署

### Docker Compose 部署

仓库根目录提供 [`Dockerfile`](Dockerfile) 与 [`docker-compose.yml`](docker-compose.yml)，两个服务：`gateway`（网关）+ `db`（PostgreSQL 17）。**默认使用 CI 预构建镜像**（GitHub Actions 每次推送自动发布到 GHCR），本机不编译：

```bash
docker compose pull                 # 拉取预构建镜像（GHCR）
docker compose up -d                # 启动
docker compose ps                   # 两个服务都应为 healthy
docker compose logs -f gateway      # 跟踪日志
docker compose restart gateway      # 改完 .env 后重启生效
docker compose down                 # 停止（数据卷保留）
docker compose down -v              # 停止并删除数据卷（⚠️ 数据全部丢失）
```

版本升级（数据卷与库内数据保留，建表/迁移在新容器启动时自动补齐）：

```bash
docker compose pull                     # 拉新镜像
docker compose up -d                    # 滚动替换 gateway 容器
docker compose logs --tail=50 gateway   # 确认 lifespan 迁移与预热无异常
```

镜像地址与标签由 `.env` 的 `GW_IMAGE` / `GW_IMAGE_TAG` 控制（默认 `ghcr.io/qingdeng888/aqua-platform-open:latest`），换 fork 仓库或私有 registry 无需改编排文件。**生产建议锁版本**（`GW_IMAGE_TAG=v12.1.0`）而非跟随 `latest`，避免推送触发的镜像更新在下次 `pull` 时被动生效。

#### 本机源码构建（local 版）

改了源码要立刻验证、CI 还没跑完、或所在网络拉不到 `ghcr.io` 时，叠加 [`docker-compose.local.yml`](docker-compose.local.yml) 覆盖层现场编译（**两个 `-f` 缺一不可，顺序不能颠倒**）：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.local.yml logs -f gateway
```

覆盖层只重定义 `gateway` 的 `build` / `image: aqua-gateway:local` / `pull_policy: build`，`db`、网络与数据卷全部继承主文件。项目名同为 `aqua-gateway`，因此两种方式**共用同一个 `pgdata` 卷和同一套容器**——来回切换不会丢库、不会起两份。

镜像与编排要点：

| 项 | 说明 |
|------|------|
| 镜像 | `python:3.13-slim` 多阶段构建：builder 装依赖到 `/opt/venv`，runtime 只带 venv + `gateway/`，无编译器与 pip 缓存 |
| 镜像来源 | 默认 GHCR 预构建（`docker compose pull`）；本机构建走 `docker-compose.local.yml` 覆盖层 |
| 运行用户 | 非 root（`aqua`，uid 10001）；容器内无运行期写盘需求 |
| 单 worker | `CMD` 不带 `--workers`，`deploy.replicas: 1`——调度器算法与代理池轮询游标是进程内状态，**不可 `--scale`** |
| 端口 | 容器内固定 8000；宿主侧由 `GW_BIND`/`GW_PORT` 决定，默认 `127.0.0.1:8000`（仅本机） |
| 健康检查 | 两个服务都有：网关探 `/healthz`（用镜像自带 python 发请求，不装 curl），数据库用 `pg_isready`；`gateway` 的 `depends_on` 等 `db` healthy 才启动 |
| 数据持久化 | 命名卷 `pgdata`；改 `PG_USER/PG_PASSWORD/PG_DB` 后需删卷才会按新值重新初始化 |
| 日志 | 两个服务均限制 json-file 驱动 `max-size=10m` × `max-file=3`，避免日志打满磁盘 |
| 迁移 | 建表与 `_migrate_*` 迁移在容器启动的 lifespan 内自动执行，幂等，无需手工建表 |
| 访问宿主 | 已配 `host.docker.internal:host-gateway`——代理池节点或数据库跑在宿主机时，填 `host.docker.internal` 而**不是** `127.0.0.1`（容器内的 `127.0.0.1` 是容器自己） |
| 外部数据库 | 删掉 `db` 服务与 `depends_on`，把 `gateway` 的 `PG_HOST` 改成实际地址即可 |
| 时区 | `TZ` 只影响容器日志的时间显示；`db` 容器固定 `TZ/PGTZ=UTC`，写库时间戳恒为 UTC `Z` 格式，控制台按浏览器本地时区渲染——宿主与容器时区不一致也不会算错统计窗口 |
| 构建上下文 | [`.dockerignore`](.dockerignore) 排除 `.env`/密钥/`.git`/`.venv`/测试与文档——敏感文件不进镜像层 |

#### CI 自动构建镜像

[`.github/workflows/docker-image.yml`](.github/workflows/docker-image.yml) 在推送任意分支 / 打 `v*.*.*` 标签 / 向 `main` 提 PR / 手动触发时运行，分两个 job：

| Job | 内容 |
|------|------|
| `test` | Python 3.13 装 `gateway/requirements.txt` 跑 `pytest tests/`，Node 20 跑 `node tests/static-smoke.mjs`。两套测试都不连库不连网，**无需注入任何环境变量或 Secret** |
| `build` | `needs: test`——测试红灯不发镜像。用内置 `GITHUB_TOKEN` 登录 GHCR（无需配置 Secret），`docker/build-push-action` 构建并推送 |

要点：

- **镜像地址**：`ghcr.io/<owner>/<repo>`，由 `github.repository` 自动推导，**fork 后无需改任何配置**。
- **标签**：分支名 / 语义版本（打 tag 时同时发 `{{version}}` 与 `{{major}}.{{minor}}`）/ `sha-<短哈希>` / `latest`（仅默认分支）。
- **架构**：日常推送只构 `linux/amd64`；只有打 `v*` 标签发版时才交叉构建 `linux/arm64`（QEMU 模拟慢）。
- **缓存**：`type=gha` 跨 workflow 复用层缓存，依赖未变时 builder 阶段直接命中。
- **PR 只验证构建**，不登录、不推送（fork 的 PR 拿不到写权限）。
- 纯文档改动（`**.md` / `docs/**` / `LICENSE`）不触发流水线。
- 未生成 provenance 证明（`provenance: false`），避免 GHCR 包页面出现 `unknown/unknown` 条目。

> ⚠️ **两个首次使用的坑**：
> 1. `docker compose pull` 只有在 CI **至少成功发布过一次镜像**之后才能拉到，全新 fork 请先推一次代码等流水线跑完，或直接用上面的 local 覆盖层本机构建。
> 2. GHCR 包首次发布默认是 **private**，`docker pull` 会要求登录。公开拉取需到 GitHub → Packages → 该包 → Package settings → Change visibility → Public（**只需做一次**）。

反代与 HTTPS 仍按下面的 Nginx 配置做（`proxy_pass` 指向 `GW_BIND:GW_PORT`），并记得设 `AQUA_TRUST_PROXY_HEADERS=1`。

数据库备份 / 恢复：

```bash
docker compose exec -T db pg_dump -U aqua aqua_gateway > backup.sql
cat backup.sql | docker compose exec -T db psql -U aqua -d aqua_gateway
```

### Nginx 反向代理

```nginx
server {
    listen 443 ssl http2;
    server_name api.你的域名.com;
    ssl_certificate     /etc/letsencrypt/live/api.你的域名.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.你的域名.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;      # 流式长响应
        proxy_buffering off;          # SSE 必关
    }
}
```

> 反代部署时记得 `AQUA_TRUST_PROXY_HEADERS=1`，否则网关看到的客户端 IP 全是 127.0.0.1。
> 管理入口（`/admin`、`/gw/admin/`、`/gw/dbadmin`）建议再叠加来源 IP 白名单。

### Systemd

```ini
# /etc/systemd/system/aqua-gateway.service
[Unit]
Description=AQUA Gateway
After=network.target postgresql.service

[Service]
WorkingDirectory=/opt/aqua/gateway
EnvironmentFile=/opt/aqua/.env
ExecStart=/opt/aqua/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`systemctl enable --now aqua-gateway`。

### 上线检查清单

- [ ] `.env` 全部密钥为随机强值；`.env` 权限 600
- [ ] `ACU_ADMIN_PASSWORD_HASH` 已配置（勿只留明文变量）；Docker 部署时该值用单引号包裹
- [ ] `ADMIN_SESSION_SECRET` 已配置（否则重启后面板会话失效）
- [ ] HTTPS 已启用；管理入口有 IP 白名单或额外防护
- [ ] 反代场景 `AQUA_TRUST_PROXY_HEADERS=1`；直连场景保持 0
- [ ] `CORS_ALLOWED_ORIGINS` 为实际域名（Docker 下与 `GW_PORT` 一致）
- [ ] `AQUA_DEBUG_ERRORS=0`
- [ ] 单 worker 运行（调度器为进程内状态）；Docker 下不要 `--scale gateway`
- [ ] Docker 部署：`GW_BIND` 按需（对外暴露才用 `0.0.0.0`）、`pgdata` 卷已纳入备份

---

## 运维手册

| 场景 | 操作 |
|------|------|
| 日志清理 | 调度器每 6 小时自动执行：成功日志保留 3 天、错误日志 90 天 |
| 手动清理日志 | `DELETE /gw/admin/request-logs/cleanup?days=N` |
| 密钥自愈 | 上游密钥两次 401/403 自动停用后，每 30 分钟探活，恢复即自动回归池内 |
| 维护模式 | 控制台「系统配置」页热开关，开启后 `/v1/*` 统一返回维护响应 |
| 进程守护 | 裸机：systemd `Restart=always`（v12.0 已移除 platform 时代的 `scripts/auto_recovery.sh`）；Docker：`restart: unless-stopped` + 健康检查 |
| 查看日志 | 裸机看 systemd journal；Docker `docker compose logs -f --tail=200 gateway`（json-file 已限 10m×3） |
| 进库操作 | Docker `docker compose exec db psql -U aqua -d aqua_gateway`；备份恢复见[Docker Compose 部署](#docker-compose-部署) |
| 版本升级 | 裸机：拉代码 + 重启服务；Docker：`docker compose pull && docker compose up -d`（本机构建版加两个 `-f` 并带 `--build`）。两者的建表与迁移都在启动 lifespan 内幂等执行 |
| 改配置生效 | `.env` 改动需重启进程/容器（`docker compose restart gateway`）；控制台「系统配置」项为热生效，无需重启 |

---

## 开发与测试

```bash
# 后端单元测试（105 个：安全体系 / 时间戳契约 / 代理池 / 统一异常 / 模型测试）
python3 -m venv .venv
.venv/bin/pip install -r gateway/requirements.txt pytest pytest-asyncio
.venv/bin/python -m pytest tests/ -v

# 前端静态检查（28 项：引用完整性 / 无内联事件 / XSS 纪律 / API 契约锚点 / 路由注册 / 代理池动作 / 模型测试守卫 / 时间本地化渲染 / 已下线端点守卫）
node tests/static-smoke.mjs

# 单文件语法检查
python -m py_compile gateway/app/xxx.py
node --check gateway/app/static/js/console/core.js
```

测试说明：仓库只有一个 `app` 包（`gateway/app`），根目录 `conftest.py` 统一把 `gateway/` 插入 `sys.path`，测试内直接 `from app.xxx import ...`。两套测试都不依赖数据库与网络，可离线运行；`tests/` 已被 `.dockerignore` 排除（不进镜像），因此测试始终在宿主侧执行。

---

## 项目结构

```
aqua-platform-open/
├── gateway/                  # 网关服务 (:8000)，唯一进程
│   ├── app/
│   │   ├── main.py           # 入口：lifespan 预热/后台任务/错误处理
│   │   ├── public_api.py     # OpenAI 兼容 API 主链路（下游 sk- 认证）
│   │   ├── admin_api.py      # /gw/admin/* 管理端点（管理员密码）
│   │   ├── model_test.py     # /gw/admin/model-test/* 模型连通性测试（双通道 + 内置自测密钥）
│   │   ├── admin_panel.py    # SQLAdmin 面板 /gw/dbadmin
│   │   ├── scheduler.py      # 17 算法调度器
│   │   ├── proxy_pool.py     # 代理池：出网解析(direct/bind/rotate) + httpx 客户端工厂
│   │   ├── security.py       # 密钥生成/加解密/管理 Token
│   │   ├── middleware.py     # 大小限制/日志/IP 提取/CORS/维护模式
│   │   ├── database.py       # psycopg2 连接池(autocommit)/时间戳家族
│   │   ├── circuit_breaker.py / ip_monitor.py / commercial_detect.py
│   │   ├── translator.py + transformers/   # 协议转换器（实验性）
│   │   ├── platforms/        # 上游适配器 nvidia/openai（实验性）
│   │   └── static/           # 控制台 UI（console.html + 4 个 JS 模块）
│   └── requirements.txt
├── tests/                    # pytest ×105 + 前端静态检查 ×28
├── docs/rules/               # 并发与限流规则文档
├── .github/workflows/        # CI：docker-image.yml（测试通过才构建并推送 GHCR 镜像）
├── conftest.py               # 统一 sys.path
├── Dockerfile                # 容器镜像（python:3.13-slim 多阶段，非 root，单 worker）
├── docker-compose.yml        # 编排：gateway(预构建镜像) + PostgreSQL 17（含健康检查与数据卷）
├── docker-compose.local.yml  # 覆盖层：gateway 改为本机源码构建（叠加在上面之上）
├── .dockerignore             # 构建上下文裁剪（排除 .env/密钥/.git/.venv）
├── .env.example              # 环境变量模板（含生成命令）
├── ARCHITECTURE.md           # 架构权威文档（17 算法表）
└── LICENSE                   # MIT
```

---

## 版本历史

### v12.1.0（2026-08-29）

- **代理池**：新增 `proxies` 表（`socks5`/`socks5h`/`http`/`https`，支持无认证与账号密码认证）与 `/gw/admin/proxies` 全套端点（列表 / 新增 / 编辑 / 删除 / 启停 / 连通性测试）
- **出网模式**：`upstream_keys` 迁移新增 `proxy_mode`（`direct`/`bind`/`rotate`）+ `proxy_id`；`bind` 代理失效或 `rotate` 池空时回退直连并告警
- **控制台**：新增独立「代理池」页（10 → 11 页），上游密钥列表新增「出网」列，新增/编辑密钥弹窗新增「出网方式 + 绑定代理」选择
- **安全**：代理密码用第三条 HKDF 派生路径（salt `acu-proxy-credential-derivation`）加密，接口只返回 `has_auth`，SQLAdmin 表单与详情排除密文列
- **可靠性**：三条密钥探活路径与 `/upstreams/health-check` 改走密钥自身出网通道，修复代理专用密钥被直连探活误判停用的问题
- **性能**：出网解析走 5 秒 TTL 快照 + 管理端主动失效，热路径零 DB 查询；客户端按 `(代理URL, 流式)` 复用，快照刷新时回收失效客户端
- **依赖**：`httpx>=0.28.0` → `httpx[socks]>=0.28.0`
- **模型测试**：新增控制台「模型测试」页（11 → 12 页）与独立后端模块 `gateway/app/model_test.py`（`/gw/admin/model-test/models|probe|selftest-key|selftest-key/rotate`，均需管理员 Token）。实时模型列表 + 搜索 / 全选 / 自定义提示词（默认 `你是什么模型，你可以帮我干什么事情`），双通道对照：「直连 NVIDIA 上游」由后端持上游密钥走该密钥自身出网通道代请求，「走本网关中转」由浏览器持内置自测密钥打 `/v1/chat/completions` 走完整链路并落日志；批量编排在前端（并发池 1–16 + `AbortController` 中止 + 逐行就地更新 + 切页自动收手），后端只提供单模型探测
- **内置自测密钥**：归属专用客户 `__console_selftest__` 的随机 `sk-` 密钥（部署内固定复用，可轮换，轮换即失效调度器客户端密钥缓存）；明文只下发给管理员、前端仅存闭包不落 localStorage；密钥下发/轮换写审计，批量探测不逐条写审计（避免刷爆 `audit_logs`）；探测响应只回 `key_masked` 与 `egress: direct|proxy`，代理 URL 内嵌凭据绝不外发
- **默认输出预算**：探测 `max_tokens` 默认 256（上限 512）——联调发现推理模型在 64 预算下 `content` 为 `null` 只给 `reasoning_content`，回复提取按 `content` → `reasoning_content` → `text` 回落，前后端同语义
- **修复（联调发现）**：`request_logs` 缺失 `gateway_dispatch_ms` 列——该列在 v11 就已写入 INSERT 但从未建表/迁移，导致**全新部署上每条请求日志写入都失败**（控制台「请求日志」页恒为空）；已补入 `_migrate_request_logs_full` 迁移
- **修复（联调发现）**：控制台「代理池」页「活跃代理」改以库内 `status` 为准——运行时快照惰性加载，冷启动时 `active_proxies=0`/`snapshot_age=-1`，原先用 `??` 取值会把 0 当真值误报为「0 个活跃代理」
- **修复（联调发现）**：统一版本号——`/healthz` 与 OpenAPI 元数据由 `11.0.0`、控制台标题与登录页由 `v10.0` 修正为 `12.1.0`
- **修复（联调发现）**：请求日志时间戳混格式——`request_logs` 的 `created_at`/`started_at`/`completed_at` 由写入端按 `+08:00` 落库，而所有窗口边界（`utcnow`/`utcnow_minus`/`days_ago_utc`）都是 UTC `Z` 格式；这三列是 **TEXT**，过滤走字符串字典序比较，于是每个时间窗口都被向外撑开 8 小时（IP 监控 5 分钟窗变 8h05m、成功日志 3 天保留变 3d08h、"今日"统计多算约 16h）。现写入端统一走 `utcnow()`/新增的 `utc_from_ts()`，删除 `localnow()`/`localnow_ms()`/`today_start_local()` 三个本地时区助手，`_migrate_request_logs_full` 追加幂等归一化把历史 `+08:00` 行改写为 `Z`；控制台 `fmtTime()` 改为解析后按浏览器本地时区渲染（不再字符串截断），请求日志详情的三个时间字段也改经 `fmtTime` 输出
- **容器化**：新增 `Dockerfile`（`python:3.13-slim` 多阶段构建、非 root uid 10001、`/healthz` 内建 HEALTHCHECK、CMD 不带 `--workers`）、`docker-compose.yml`（`gateway` + `postgres:17-alpine`，`depends_on: service_healthy`、命名卷 `pgdata`、日志轮转、`host.docker.internal` 映射）与 `.dockerignore`（`.env`/密钥/`.git`/`.venv` 不进镜像层）；`.env.example` 新增 Docker 小节（`GW_BIND`/`GW_PORT`/`TZ`）
- **CI 自动构建镜像**：新增 `.github/workflows/docker-image.yml`——推送任意分支 / 打 `v*.*.*` 标签 / 向 `main` 提 PR / 手动触发时，先跑「后端单测 + 前端静态检查」两个 job，全绿才构建镜像并推送到 GHCR（`ghcr.io/<owner>/<repo>`，由 `github.repository` 推导，fork 无需改配置）。用内置 `GITHUB_TOKEN` 登录，**零 Secret 配置**；标签含分支名 / 语义版本 / `sha-<短哈希>` / `latest`（仅默认分支）；`type=gha` 层缓存；日常推送只构 `linux/amd64`，打 tag 才交叉构建 `arm64`；PR 只验证构建不推送；`provenance: false` 避免包页面出现 `unknown/unknown`
- **预构建镜像 + local 覆盖层**：`docker-compose.yml` 的 `gateway` 改为消费预构建镜像 `${GW_IMAGE:-ghcr.io/qingdeng888/aqua-platform-open}:${GW_IMAGE_TAG:-latest}`（移除 `build` 段），部署与升级变为 `docker compose pull && docker compose up -d`；新增 `docker-compose.local.yml` 覆盖层（`build` + `image: aqua-gateway:local` + `pull_policy: build`）供本机源码构建，只重定义 `gateway`，`db`/网络/数据卷全部继承，两种方式共用同一 `pgdata` 卷可无损来回切换；`.env.example` 新增 `GW_IMAGE`/`GW_IMAGE_TAG` 两项
- **文档（Docker 踩坑）**：bcrypt 哈希含 `$`，docker compose 会对 `env_file` 中的 `$xxx` 做变量插值，`ACU_ADMIN_PASSWORD_HASH` 不加单引号会被静默截断（`$2b$12$bdJq…` → `$2b$12.yz…`），表现为配置无误却怎么都登不上；`.env.example`、README 快速开始与上线清单均已标注单引号写法
- **测试**：后端 105 passed（代理 URL/客户端/选路 21 个 + 代理凭据加密与三路派生隔离用例；新增 `utc_from_ts` 格式/取值 3 个 + 写库路径不得回退到本地时区写入的守卫 2 个；新增模型测试 45 个：提示词归一 / `max_tokens` 收敛 / 回复与错误提取三态 / 路由与常量契约 / `extra="forbid"` / 不外泄凭据的源码守卫）；前端静态检查 28 项（代理池动作与出网徽标守卫；新增 `fmtTime` 本地化渲染与详情页无裸时间戳守卫；新增模型测试页动作齐全、自测密钥不落盘、可中止、`innerHTML` 只写静态模板、`max_tokens` 默认值前后端一致守卫）

### v12.0.0（2026-08-29）

- **形态**：收敛为纯中转网关（单服务），删除 `platform/` 用户平台（53 个文件：注册登录、Web 聊天、邮件、用量统计、用户端 SPA）
- **认证**：彻底移除 `platform_tokens` 机制——建表语句、3 个 `/gw/admin/platform-tokens` 端点、ORM 模型、SQLAdmin 视图、scope 授权（`SENSITIVE_SCOPES` / `_require_platform_scope`）、控制台「平台令牌」页与 `AQUA_PLATFORM_TOKEN` 自动播种全部删除
- **解耦**：删除网关对 platform（`http://127.0.0.1:8001`）的反向 HTTP 依赖端点 `/system/user-stats`、`/system/health` 及前端对应卡片（原本会永久报错）
- **瘦身**：删除 `scripts/`（5 个 platform 专属运维脚本）；控制台页面 12 → 10；`.env` 变量精简至 14 项；移除两个同名 `app` 包所需的 `tests/_app_path.py` sys.path 切换 hack
- **文档**：README / ARCHITECTURE / 并发规则文档按单服务架构改写
- **测试**：后端 25 passed（原 30，减少的 5 个为 platform 并发控制器用例）；前端静态检查 16 项，新增"已下线 platform 耦合端点不得残留"回归守卫

### v11.0.0（2026-08-27）

- **安全**：修复密钥吊销失效（缓存逐条 TTL + 即时失效）、三处管理面空密码、XFF 伪造绕过、未鉴权端点、管理页存储型 XSS、chunked 绕过体积限制；新增限流/会话吊销/Origin 校验/Cookie Secure/日志脱敏
- **性能**：事件循环 209 处同步调用异步化；连接池 `GW_DB_POOL_SIZE` 可配 + 预热；httpx 池与超时调优；SSE 心跳保活（15s ping / 180s 空闲上限）；启动缓存预热
- **可靠性**：时间戳统一 UTC Z 格式（修复统计 +8h）；上游密钥停用后探活自动恢复；熔断器半开状态机与滚动窗口失败率；6 个 ImportError 端点修复
- **前端**：管理控制台重构为零构建模块化 JS，统一深色设计语言、危险操作二次确认
- **工程**：requirements 修正、恢复 `.env.example`、文档与现实对齐

### v10.0.0（2026-07）

- 初始开源版本：双服务架构、17 算法调度、密钥池化、SQLAdmin、多级缓存

---

## 开源协议

[MIT](LICENSE) © 2026 AQUA Platform Contributors

欢迎 Issue / PR。贡献前请阅读 [ARCHITECTURE.md](ARCHITECTURE.md) 了解架构约束（单 worker、仅 PostgreSQL、零构建前端）。
