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
9. [模型管理](#模型管理)
10. [代理池](#代理池)
11. [模型测试](#模型测试)
12. [安全体系](#安全体系)
13. [调度与算法](#调度与算法)
14. [并发与限流](#并发与限流)
15. [多协议转换（实验性）](#多协议转换实验性)
16. [生产部署](#生产部署)
17. [运维手册](#运维手册)
18. [开发与测试](#开发与测试)
19. [项目结构](#项目结构)
20. [版本历史](#版本历史)
21. [开源协议](#开源协议)

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
| **模型管理** | **硬编码白名单下线**：对外模型列表 = 上游实时全量（本机实测 23 → 83 个）± 管理员覆盖层。新增「模型管理」页（12 → 13 页）与 `/gw/admin/models*`：搜索、逐个/批量隐藏、手动补录上游未收录的模型，附「隐藏的模型同时禁止调用」开关（默认关） |
| **模型别名 / 映射** | 同页新增别名层与 `PUT/DELETE /gw/admin/models/alias`：把上游模型改名对外（`moonshotai/kimi-k3` → `nv/kimi-k3`），下游列表与响应体 `model` 都是别名，解析在校验之前完成、全链路仍走真名。语义对齐 CLIProxyAPI（`fork`/`force-mapping`），撞名拒写 |
| **容器化** | 新增 `Dockerfile`（多阶段、非 root、内建健康检查）+ `docker-compose.yml`（网关 + PostgreSQL 17，默认拉 CI 预构建镜像）+ `docker-compose.local.yml`（本机源码构建覆盖层）+ `.dockerignore`，`docker compose pull && docker compose up -d` 起全栈 |
| **CI/CD** | 新增 `.github/workflows/docker-image.yml`：推送即跑「后端单测 + 前端静态检查」，全绿才构建镜像并推送到 GHCR，无需配置任何 Secret；`latest` 恒为 `amd64` + `arm64` 双架构 |
| **上游密钥录入** | 单个添加之外新增**批量添加**：一行一个密钥粘贴进去，名称按「前缀-序号」自动生成，逐行报告成功/跳过原因（批内重复、库内已存在、含空格、长度越界），单次上限 200 行 |
| **代理池录入** | 同样支持**批量添加**：一行一个 `协议://用户名:密码@地址:端口`（无认证写 `协议://地址:端口`），名称按「前缀-序号」自动生成，逐行报告跳过原因（协议不支持、端口非法、带路径、IPv6、批内或库内重复），单次上限 200 行 |
| **管理员密码** | 收敛为**单一明文变量** `ACU_ADMIN_PASSWORD`：删除 bcrypt 哈希方案与 `bcrypt` 依赖，配置即 `.env` 写一行，不必装包也不必手搓哈希 |
| **工程化** | 测试基线 348 个后端单测 + 59 项前端静态检查；新增依赖 `httpx[socks]`，移除 `bcrypt` |

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
| **密钥池化** | 多个 NVIDIA NIM 上游密钥整合为统一入口，密钥 Fernet+HKDF 加密存储；录入支持单个添加与批量粘贴（一行一个、自动命名、逐行查重） |
| **密钥转换** | 下游签发 `sk-` 客户端密钥（仅存 SHA-256 哈希查库），与上游密钥完全隔离 |
| **统一 API** | OpenAI 兼容：`/v1/chat/completions`、`/v1/embeddings`、`/v1/models` |
| **17 算法互锁调度** | RPM、健康度、冷却、预热、熔断等 17 个协同算法计算最优密钥选择 |
| **故障自愈** | 429/5xx 熔断（半开探测恢复）、自适应冷却、冷密钥渐进预热、自动故障转移 |
| **模型 ID 映射** | 数百条别名、6 级匹配策略，非标准 ID 自动纠错映射 |
| **模型管理** | 默认加载上游全量模型（无白名单）；控制台可搜索、隐藏/取消隐藏、手动补录模型，可选让隐藏项同时禁止被调用 |
| **模型别名 / 映射** | 把上游模型 ID 改名对外，下游拿到自定义别名并可直接用别名调用；可选真名并存、可选把响应体 `model` 回写成别名 |
| **代理池** | SOCKS5 / HTTP 代理入池（可带账号密码），录入支持单个添加与批量粘贴（一行一个代理 URL、自动命名、逐行查重）；上游密钥可直连 / 绑定代理 / 池内轮询 |
| **模型连通性测试** | 控制台批量探测：实时模型列表、模型搜索/全选、可自定义提示词，支持「直连上游」与「走本网关中转」双通道对照 |
| **管理控制台** | 零构建网关控制台（13 页面）+ SQLAdmin 数据库面板 |
| **多级缓存** | L1/L2 两级进程内内存缓存（LRU + TTL）：API Key 缓存、限流计数、TPM/RPM 追踪 |
| **安全体系** | 管理密码恒定时间比较、SHA-256 密钥哈希查库、IP 监控、可信代理模型、请求体 10MB 硬限 |

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
| `ACU_ADMIN_PASSWORD` | 管理员密码，**直接写明文**，无需哈希，见[管理员密码](#管理员密码) |
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
cp .env.example .env       # 至少填 ACU_ADMIN_PASSWORD 与 PG_PASSWORD
docker compose pull        # 拉 CI 预构建镜像（GHCR），不在本机编译
docker compose up -d
docker compose logs -f gateway
```

改了源码要立刻验证时，叠加 `docker-compose.local.yml` 改为本机构建：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

默认映射到 `http://127.0.0.1:8000`（宿主地址与端口由 `.env` 的 `GW_BIND`/`GW_PORT` 控制），建库、建表与迁移在容器首次启动时自动完成。运维细节见[Docker Compose 部署](#docker-compose-部署)。

> 💡 **管理员密码就是一个明文变量**：`.env` 里写 `ACU_ADMIN_PASSWORD=你的密码` 即可，不需要生成哈希、不需要装依赖。详见[管理员密码](#管理员密码)。

---

## 环境变量

完整模板见 [`.env.example`](.env.example)（含随机值生成命令）。**应用读取 13 项**，另有 **6 项仅供 `docker-compose.yml` 使用**（应用本身不读）。

加载与优先级：

- 网关启动时由 `gateway/app/main.py` 加载**仓库根目录**的 `.env`（容器内即 `/app/.env`）；`load_dotenv` **不覆盖**已存在的环境变量，故「容器/systemd 传入的环境变量 > `.env` 文件」
- 除 `AQUA_TRUST_PROXY_HEADERS`（每请求读取）外，其余均在模块导入时读取一次，**改动后必须重启进程/容器**才生效
- `.env` 权限建议 `600`；已列入 `.gitignore` 与 `.dockerignore`，不会进仓库和镜像层

### 必填（缺失则启动失败）

| 变量 | 校验时机 | 缺失表现 |
|------|------|------|
| `ACU_ADMIN_PASSWORD` | 导入 `admin_api.py` 时 | `RuntimeError: [FATAL] 未配置管理员密码！请在 .env 中设置 ACU_ADMIN_PASSWORD=你的密码`，进程起不来 |
| `PG_PASSWORD` | 首次建连接池时（惰性，导入不失败） | `RuntimeError: [FATAL] 环境变量 PG_PASSWORD 未设置！` |

### 认证与会话

#### 管理员密码

只有一种配法：明文写进 `.env`，无需生成哈希、无需装任何依赖。控制台 `/admin` 与 SQLAdmin 面板 `/gw/dbadmin` 读同一个变量，行为天然一致。

| 变量 | 取值 | 说明 |
|------|------|------|
| `ACU_ADMIN_PASSWORD` | 明文密码 | 校验走 `hmac.compare_digest` 恒定时间比较，不做 `strip`/大小写归一——配置里写什么就必须一字不差地输入什么。含 `$` 也没问题。留空则启动失败；空值也不会退化成"空密码可登录"（`compare_digest(b"", b"")` 为真，代码里额外挡了一次） |
| `ADMIN_SESSION_SECRET` | 随机串（建议 ≥ 32 字节） | SQLAdmin 面板 Session 签名密钥。未配置时每次启动生成临时值并打 warning，**重启后所有面板登录会话失效** |

**为什么不哈希**：`.env` 本就明文存着库密码（`PG_PASSWORD`）与加密主密钥（`ENCRYPTION_KEY`），且已 `chmod 600` + `gitignore` + `dockerignore`；`.env` 一旦泄露，攻击者拿库密码与主密钥可直接读库解密全部上游密钥，管理员密码再哈希一层的边际收益很低。防护重点应放在**文件权限**与**管理入口的网络隔离**（IP 白名单 / HTTPS / 不对公网暴露 `/admin`）上，而不是口令的存储形式。

```bash
# 随机会话密钥
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```


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

用户输入非标准 ID 时自动映射（如 `deepseek-v4-pro` → `deepseek-ai/deepseek-v4-pro-0813`）。纠错的比对基准是**上游 `/models` 实时全量 ∪ 手动补录**（含被管理员隐藏的模型），`gateway/app/nim_models.py` 退化为**纯展示元数据**来源（显示名 / 上下文长度 / 能力标签），不再参与过滤，详见[模型管理](#模型管理)。

---

## 管理控制台

| 入口 | 地址 | 认证 |
|------|------|------|
| 网关控制台 | `http://127.0.0.1:8000/admin` | 管理员密码登录（HMAC Token，24h） |
| SQLAdmin 面板 | `http://127.0.0.1:8000/gw/dbadmin` | 同一密码（Session） |

**密码契约**（两个登录口读同一个变量 `ACU_ADMIN_PASSWORD`，见[管理员密码](#管理员密码)）：`hmac.compare_digest` 恒定时间比较明文，不做 `strip`/大小写归一。两条兜底：**未配置时控制台模块导入即抛 `RuntimeError` 进程起不来**（面板则记 `[FATAL]` 并禁用全部登录）；即便配置为空串也会先挡一次再比较——`compare_digest(b"", b"")` 为真，不挡就是"空密码即可登录"（v10.1 踩过）。

控制台 13 个页面：仪表盘、上游密钥（含明文 reveal）、模型管理、代理池、下游客户、桶监控、请求日志、算法引擎（17 算法实时状态）、系统监控、系统配置、错误码、商用检测、模型测试。

> v12.0 起管理接口只有**管理员密码**一条认证路径，不再存在机器间令牌（`platform_tokens`）与 scope 授权模型。若需程序化调用 `/gw/admin/*`，先 `POST /gw/admin/login` 换取 24h HMAC Token，再以 `Authorization: Bearer <token>` 携带。

---

## 模型管理

**v12.1 起没有硬编码白名单**：对外模型列表 = 上游 `GET /models` 实时全量 ± 管理员覆盖层。以往写死在代码里的 24 条"已验证可用"清单会与上游结果取交集，本机实测把 83 个真实模型压到 23 个，上游新上的模型必须改代码才能对外可见——现在改为**默认全放，要藏哪个由管理员在控制台决定**。

### 取数链

```
上游 /models ──▶ _models_cache (60s，原样全量) ──▶ get_model_list()
                                                     │
                       model_overrides 覆盖层 ────────┤ 剔除 hidden、追加 manual
                       （30s 快照缓存）               │
                       model_aliases 别名层 ─────────┤ 真名换成自定义别名
                       （同一份 30s 快照）            │
                                                     ├─▶ 对外列表（/v1/models 等）
                                                     └─▶ 模型 ID 纠错集合（真名，含被隐藏项）
```

**关键不变量：可见性 ≠ 名称有效性**。纠错集合喂的是 `all_known_models()`（上游全量 ∪ 手动补录，**含被隐藏的模型**），而不是对外可见列表。否则隐藏 `X` 之后，客户端指名调 `X` 会被 6 级模糊匹配"纠正"成另一个相似模型，静默换模型比直接报错危险得多。

**关键不变量：别名不进纠错集合**。纠错函数的返回值会直接写进 `body["model"]` 再发给上游，别名一旦进了纠错集合，一个手滑的模型名就可能被模糊匹配成别名、再原样发给上游 → 404。所以别名在**纠错之前**就换回真名，纠错集合始终只装真名。

### 控制台「模型管理」页

| 能力 | 说明 |
|------|------|
| 搜索 | 输入即过滤（前端本地过滤，不发请求、不丢焦点），同时匹配模型 ID、备注与别名；同一个搜索框也过滤下方别名表 |
| 隐藏 / 取消隐藏 | 单行按钮，或对**当前搜索结果**批量隐藏 / 批量显示（单批上限 500 个） |
| 手动补录 | 填模型 ID（可选备注）补录上游 `/models` 未收录但实际可用的模型，列表中标记来源 `manual` |
| 删除 | 仅限手动补录项；上游自带模型不可删（删了下次回源又出现），要不可见请用隐藏 |
| 别名 | 行动作「+ 别名」为该模型取一个对外名字（目标模型由行决定，不用手输）；下方「模型别名 / 映射」表可编辑与删除 |
| 刷新 | `refresh=1` 跳过 60 秒上游缓存立即回源 |
| 开关 | 「隐藏的模型同时禁止调用」，默认**关** |

### 「隐藏」的两种语义（开关控制）

| 开关 | 列表可见性 | 下游指名调用 |
|------|-----------|-------------|
| 关（默认） | 不显示 | **放行**，正常转发上游 |
| 开 | 不显示 | 返回 `400`，`code = model_disabled` |

### 模型别名 / 映射

把上游真实模型 ID 改名对外：配 `nv/kimi-k3 → moonshotai/kimi-k3` 后，下游密钥在 `/v1/models` 里看到的是 `nv/kimi-k3`，用它调用照常转发。语义对齐 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)：

| 本项目字段 | CLIProxyAPI | 默认 | 含义 |
|-----------|-------------|------|------|
| `alias` | `alias` | — | 对外暴露的名字（主键，**大小写不敏感唯一**） |
| `target_model` | `name` | — | 真实上游模型 ID，必须真实存在（上游全量 ∪ 手动补录），**不能指向别名** |
| `display_name` | `display-name` | 空 | 可选显示名；留空则继承真模型的目录元数据 |
| `keep_original` | `fork` | `0` | `0` = 别名**替换**真名；`1` = 真名与别名在列表里并存 |
| `force_mapping` | `force-mapping` | `1` | `1` = 把响应体的 `model` 字段也回写成别名；`0` = 保留上游真名 |

与 CLIProxyAPI 一致的行为：别名解析**大小写不敏感**（且忽略 `.`/`_`/`-` 等分隔符差异）、解析发生在**模型校验之前**、多个别名可指向同一个上游模型、`/v1/embeddings` 同样生效。

两处刻意偏离：

- `force_mapping` **默认开**。CLIProxyAPI 默认关是出于多 provider 兼容顾虑；本网关只有 NIM 一个上游，下游只看到别名却在响应里收到真名并不一致，还会把真实厂商名漏回去。逐条可关。
- 别名与真实模型 ID **撞名直接拒写**（`400 alias_conflicts_model`），而 CLIProxyAPI 是别名优先、静默遮蔽真实模型。遮蔽是陷阱，防呆优先。

其余边界：

- **真名一律照旧放行**——别名只是多一个可用名字，不打断已配置好的客户端。要彻底禁掉真名，用「隐藏」+「隐藏的模型同时禁止调用」开关。
- 目标模型被隐藏时，**它的别名条目也一并从 `/v1/models` 消失**（隐藏语义优先）；开了「隐藏即禁用」，用别名调用同样返回 400 `model_disabled`（解析后按真名判定，绕不过去）。
- 解析后**全链路只用真名**：熔断器 key、调度分桶（别名单独分桶会让 429 冷却失效）、请求日志与统计都是真名。别名只出现在对外列表和响应体的 `model` 字段两处。
- 流式响应逐 chunk 回写 `model`；`force_mapping=0` 或无别名的请求完全不进回写分支，保持字节级透传。
- 目标模型后来从上游下架时，别名表该行显示红色「目标已不存在」徽标，提示清理。

### 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/gw/admin/models` | GET | 管理视图（`search=` 关键词、`refresh=1` 强制回源），返回上游/可见/隐藏/补录/别名计数、别名表与开关状态 |
| `/gw/admin/models` | POST | 手动补录（`model_id`、`remark`） |
| `/gw/admin/models` | DELETE | 删除手动补录项（`?model_id=`；非手动项返回 404 `not_manual_model`） |
| `/gw/admin/models/visibility` | PUT | 批量隐藏 / 取消隐藏（`model_ids`、`hidden`） |
| `/gw/admin/models/block-setting` | PUT | 开关「隐藏的模型同时禁止调用」（`block_calls`） |
| `/gw/admin/models/alias` | PUT | 新增或修改别名（`alias`、`target_model`、`display_name`、`keep_original`、`force_mapping`、`remark`），按 alias upsert |
| `/gw/admin/models/alias` | DELETE | 删除别名（`?alias=`；不存在返回 404 `alias_not_found`） |

别名写入的校验顺序：字符合法（`invalid_model_id`）→ 别名 ≠ 目标（`alias_equals_target`）→ 不与真实模型撞名（`alias_conflicts_model`）→ 目标真实存在（`target_not_found`，提示先用「手动添加模型」补录）。

`model_overrides` 表遵循「**只有携带信息的模型才有行**」：取消隐藏上游模型直接删行，取消隐藏手动补录项只清 `hidden`——库里不留一堆 `hidden=0, manual=0` 的空行。模型 ID 非机密，校验失败会原因回显（仅允许 `A-Za-z0-9._:/-`，长度 ≤ 200）。

> **升级提示**：`stepfun-ai/step-3.7-flash` 在旧硬编码清单里但上游 `/models` 已不返回它，白名单下线后旧的"清单兜底"也一并消失，因此它不再出现在列表中。若你的上游账号实际仍可调用它，用「手动添加」补录即可。

---

## 代理池

上游密钥默认直连 NVIDIA NIM。若上游对出口 IP 敏感，或需要把不同密钥分散到不同出口，可在控制台「**代理池**」页录入代理，再为每个上游密钥指定出网方式。

### 代理配置

| 项 | 说明 |
|------|------|
| 协议 | `socks5`、`socks5h`（域名在代理端解析）、`http`、`https` |
| 认证 | 用户名与密码留空即为无认证代理；填写则按「用户名:密码」认证（凭据做 percent-encoding） |
| 录入方式 | 单个添加（逐项填表）与**批量添加**（多行粘贴代理 URL）两条路径并存 |
| 状态 | `active` 参与绑定与轮询；`inactive` 立即退出（绑定它的密钥临时回退直连） |
| 连通性测试 | 经该代理访问上游 `/models`，拿到任意 HTTP 状态即视为通道可用，结果落库展示 |

代理密码使用**独立 HKDF salt**（`acu-proxy-credential-derivation`）加密存储，任何接口都不回显，列表仅返回 `has_auth` 布尔值。

#### 批量添加格式

控制台「代理池」页 →「批量添加」，文本框内**一行一个**代理地址：

```text
http://user:password@1.2.3.4:8080     # 带认证
socks5://5.6.7.8:1080                 # 无认证，省略凭据部分
socks5h://user:password@proxy.example.com:1080
# 以 # 开头的行与空行会被忽略，方便写批次备注
```

- 名称由后端按 `{前缀}-{序号}` 自动生成（默认前缀 `px`），序号从库内同前缀最大值续排
- 查重按**协议 + 地址 + 端口 + 用户名**四元组：同 IP 同端口但账号不同视为**不同**代理（住宅代理常以用户名区分出口）
- 逐行给出跳过原因：缺协议前缀 / 协议不支持 / 端口缺失或不在 1-65535 / 地址后带路径或参数 / IPv6 字面量 / 有密码无用户名 / 批内重复 / 库中已存在
- 密码含 `@` 或 `:` 时建议写成 `%40` / `%3A`；裸 `@` 也能正确解析（按最右侧 `@` 切分 userinfo），但裸 `:` 会被当作用户名与密码的分隔符
- 单次上限 200 行；等价 API：`POST /gw/admin/proxies/bulk`

> **暂不支持 IPv6 字面量地址**（如 `http://[2001:db8::1]:8080`）：内部拼装代理 URL 时不会补方括号，收下即产生不可用记录，故批量与单个添加一致地当场拒收，请用域名或 IPv4。

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
| `GET /gw/admin/model-test/models` | 实时模型列表（`refresh=1` 跳过 60 秒缓存回源），与下游客户看到的一致（已应用[模型管理](#模型管理)覆盖层） |
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
- **认证**：管理密码 `hmac.compare_digest` 恒定时间比较；管理 Token 为 HMAC-SHA256 签名 + 24h 过期 + `role=admin` 校验
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
- **标签**：分支名 / 语义版本（打 tag 时同时发 `{{version}}` 与 `{{major}}.{{minor}}`）/ `sha-<短哈希>` / `latest`。`latest` 的 `enable={{is_default_branch}}` 在**默认分支推送与 tag 推送时都为真**（metadata-action 视 tag 为发版）。
- **架构**：**凡是会更新 `latest` 的构建（`main` 推送、`v*` 标签）恒为 `linux/amd64` + `linux/arm64` 双架构**——`docker compose pull` 拉到的 `latest` 永远同时支持 x86 与 arm（树莓派、Apple Silicon、AWS Graviton 直接可用）。其他分支与 PR 只构 `amd64`，因为 QEMU 模拟 arm64 约慢 3 倍（实测 3.5 分钟 vs 1 分钟），功能分支不值当。
- **双架构守卫**：构建后自动 `docker buildx imagetools inspect` 校验 `latest` 的 manifest 同时含两个平台，缺一即红灯——架构判定逻辑被改坏会立刻暴露，而不是等用户在 arm 机器上 `pull` 才发现。
- **缓存**：`type=gha` 跨 workflow 复用层缓存，依赖未变时 builder 阶段直接命中。
- **PR 只验证构建**，不登录、不推送（fork 的 PR 拿不到写权限）。
- 纯文档改动（`**.md` / `docs/**` / `LICENSE`）不触发流水线。
- 未生成 provenance 证明（`provenance: false`），避免 GHCR 包页面出现 `unknown/unknown` 条目。

> ⚠️ **两个首次使用的坑**：
> 1. `docker compose pull` 只有在 CI **至少成功发布过一次镜像**之后才能拉到，全新 fork 请先推一次代码等流水线跑完，或直接用上面的 local 覆盖层本机构建。
> 2. GHCR 包的可见性可能是 **private**（此时 `docker pull` 报 401 要求登录）。本仓库首发即继承源仓库的 public 可见性，实测匿名 `docker pull` 可直接拉取；若你的 fork 拉取被拒，到 GitHub → Packages → 该包 → Package settings → Change visibility → Public 改一次即可（**只需做一次**）。

已发布镜像（实测）：

| 标签 | 架构 | 来源 |
|------|------|------|
| `latest` | **`linux/amd64` + `linux/arm64`** | `main` 推送 / `v*` 标签，恒双架构 |
| `12.1.0` / `12.1` | **`linux/amd64` + `linux/arm64`** | 推送 `v12.1.0` 标签 |
| `sha-<短哈希>` | 双架构（`main`/tag）或仅 `amd64`（功能分支） | 每次构建 |
| `<功能分支名>` | 仅 `linux/amd64` | 非默认分支推送 |

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
- [ ] `ACU_ADMIN_PASSWORD` 已配置且为强口令；`.env` 权限 600、管理入口不对公网裸奔
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
| 批量导入上游密钥 | 控制台「上游密钥」页 →「批量添加」：一行一个密钥粘贴，空行与 `#` 注释行忽略，名称按「前缀-序号」自动生成（序号从库内同前缀最大值续排并跳过已占用名）。批内重复 / 库内已存在 / 含空格 / 长度越界的行逐行给出跳过原因，其余行照常入库；单次上限 200 行，再多请分批。等价 API：`POST /gw/admin/upstreams/bulk` |
| 批量导入代理 | 控制台「代理池」页 →「批量添加」：一行一个 `协议://[用户名:密码@]地址:端口`，空行与 `#` 注释行忽略，名称按「前缀-序号」自动生成（默认前缀 `px`）。查重按协议+地址+端口+用户名四元组，畸形行逐行给出跳过原因，其余行照常入库；单次上限 200 行。等价 API：`POST /gw/admin/proxies/bulk`，格式细节见[代理池](#代理池) |
| 改管理员密码 | 改 `.env` 的 `ACU_ADMIN_PASSWORD` 后重启（`docker compose restart gateway` / `systemctl restart aqua-gateway`）。密码不入库、无需迁移。注意**已签发的 24h 管理 Token 不会失效**——Token 由库内 `admin_settings.gateway_secret` 签名，与密码无关；密码疑似泄露时需一并轮换该密钥（`docker compose exec db psql -U aqua -d aqua_gateway -c "DELETE FROM admin_settings WHERE key='gateway_secret'"` 后重启，启动时会重新随机生成，全部旧 Token 立即作废） |
| 改配置生效 | `.env` 改动需重启进程/容器（`docker compose restart gateway`）；控制台「系统配置」项为热生效，无需重启 |
| 屏蔽某个模型 | 控制台「模型管理」页搜索到该模型 →「隐藏」（或批量隐藏当前搜索结果）。默认只是从 `/v1/models` 消失、指名调用仍放行；要一并拒绝调用，打开同页开关「隐藏的模型同时禁止调用」（此后调用返回 400 `model_disabled`）。等价 API：`PUT /gw/admin/models/visibility`、`PUT /gw/admin/models/block-setting` |
| 补录上游未列出的模型 | 控制台「模型管理」页 →「手动添加」填模型 ID（上游 `/models` 未返回但账号实际可调用时用）。仅手动补录项可删除，上游自带模型请用隐藏。等价 API：`POST /gw/admin/models`、`DELETE /gw/admin/models?model_id=` |
| 把上游模型改名对外 | 控制台「模型管理」页找到该模型 → 行动作「+ 别名」填对外名字（如 `nv/kimi-k3`）。默认别名替换真名且响应体 `model` 也回写成别名；真名仍可调用。别名与真实模型 ID 撞名会被拒写。等价 API：`PUT /gw/admin/models/alias`、`DELETE /gw/admin/models/alias?alias=` |

---

## 开发与测试

```bash
# 后端单元测试（348 个：安全体系 / 管理员密码 / 时间戳契约 / 代理池 / 统一异常 / 模型测试 / 上游密钥批量添加 / 代理池批量添加 / 模型管理覆盖层与别名层）
python3 -m venv .venv
.venv/bin/pip install -r gateway/requirements.txt pytest pytest-asyncio
.venv/bin/python -m pytest tests/ -v

# 前端静态检查（59 项：引用完整性 / 无内联事件 / XSS 纪律 / API 契约锚点 / 路由注册 / 代理池动作 / 上游密钥单个·批量双路径 / 代理池单个·批量双路径 / 模型测试守卫 / 模型管理与别名守卫 / 时间本地化渲染 / 已下线端点守卫）
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
│   │   ├── model_registry.py # /gw/admin/models* 模型覆盖层（隐藏/手动补录 + 禁调开关）
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
├── tests/                    # pytest ×348 + 前端静态检查 ×59
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
- **依赖**：`httpx>=0.28.0` → `httpx[socks]>=0.28.0`；移除 `bcrypt>=4.0.0`（唯一用途是管理员密码哈希，下游客户密钥用的是 SHA-256）
- **模型测试**：新增控制台「模型测试」页（11 → 12 页）与独立后端模块 `gateway/app/model_test.py`（`/gw/admin/model-test/models|probe|selftest-key|selftest-key/rotate`，均需管理员 Token）。实时模型列表 + 搜索 / 全选 / 自定义提示词（默认 `你是什么模型，你可以帮我干什么事情`），双通道对照：「直连 NVIDIA 上游」由后端持上游密钥走该密钥自身出网通道代请求，「走本网关中转」由浏览器持内置自测密钥打 `/v1/chat/completions` 走完整链路并落日志；批量编排在前端（并发池 1–16 + `AbortController` 中止 + 逐行就地更新 + 切页自动收手），后端只提供单模型探测
- **内置自测密钥**：归属专用客户 `__console_selftest__` 的随机 `sk-` 密钥（部署内固定复用，可轮换，轮换即失效调度器客户端密钥缓存）；明文只下发给管理员、前端仅存闭包不落 localStorage；密钥下发/轮换写审计，批量探测不逐条写审计（避免刷爆 `audit_logs`）；探测响应只回 `key_masked` 与 `egress: direct|proxy`，代理 URL 内嵌凭据绝不外发
- **默认输出预算**：探测 `max_tokens` 默认 256（上限 512）——联调发现推理模型在 64 预算下 `content` 为 `null` 只给 `reasoning_content`，回复提取按 `content` → `reasoning_content` → `text` 回落，前后端同语义
- **修复（联调发现）**：`request_logs` 缺失 `gateway_dispatch_ms` 列——该列在 v11 就已写入 INSERT 但从未建表/迁移，导致**全新部署上每条请求日志写入都失败**（控制台「请求日志」页恒为空）；已补入 `_migrate_request_logs_full` 迁移
- **修复（联调发现）**：控制台「代理池」页「活跃代理」改以库内 `status` 为准——运行时快照惰性加载，冷启动时 `active_proxies=0`/`snapshot_age=-1`，原先用 `??` 取值会把 0 当真值误报为「0 个活跃代理」
- **修复（联调发现）**：统一版本号——`/healthz` 与 OpenAPI 元数据由 `11.0.0`、控制台标题与登录页由 `v10.0` 修正为 `12.1.0`
- **修复（联调发现）**：请求日志时间戳混格式——`request_logs` 的 `created_at`/`started_at`/`completed_at` 由写入端按 `+08:00` 落库，而所有窗口边界（`utcnow`/`utcnow_minus`/`days_ago_utc`）都是 UTC `Z` 格式；这三列是 **TEXT**，过滤走字符串字典序比较，于是每个时间窗口都被向外撑开 8 小时（IP 监控 5 分钟窗变 8h05m、成功日志 3 天保留变 3d08h、"今日"统计多算约 16h）。现写入端统一走 `utcnow()`/新增的 `utc_from_ts()`，删除 `localnow()`/`localnow_ms()`/`today_start_local()` 三个本地时区助手，`_migrate_request_logs_full` 追加幂等归一化把历史 `+08:00` 行改写为 `Z`；控制台 `fmtTime()` 改为解析后按浏览器本地时区渲染（不再字符串截断），请求日志详情的三个时间字段也改经 `fmtTime` 输出
- **容器化**：新增 `Dockerfile`（`python:3.13-slim` 多阶段构建、非 root uid 10001、`/healthz` 内建 HEALTHCHECK、CMD 不带 `--workers`）、`docker-compose.yml`（`gateway` + `postgres:17-alpine`，`depends_on: service_healthy`、命名卷 `pgdata`、日志轮转、`host.docker.internal` 映射）与 `.dockerignore`（`.env`/密钥/`.git`/`.venv` 不进镜像层）；`.env.example` 新增 Docker 小节（`GW_BIND`/`GW_PORT`/`TZ`）
- **CI 自动构建镜像**：新增 `.github/workflows/docker-image.yml`——推送任意分支 / 打 `v*.*.*` 标签 / 向 `main` 提 PR / 手动触发时，先跑「后端单测 + 前端静态检查」两个 job，全绿才构建镜像并推送到 GHCR（`ghcr.io/<owner>/<repo>`，由 `github.repository` 推导，fork 无需改配置）。用内置 `GITHUB_TOKEN` 登录，**零 Secret 配置**；标签含分支名 / 语义版本 / `sha-<短哈希>` / `latest`；`type=gha` 层缓存；**凡是会更新 `latest` 的构建（`main` 推送与 `v*` 标签）恒为 `linux/amd64` + `linux/arm64` 双架构**，功能分支与 PR 只构 `amd64`（QEMU 约慢 3 倍）；构建后自动 `imagetools inspect` 校验 `latest` 双架构，缺一即红灯；PR 只验证构建不推送；`provenance: false` 避免包页面出现 `unknown/unknown`
- **预构建镜像 + local 覆盖层**：`docker-compose.yml` 的 `gateway` 改为消费预构建镜像 `${GW_IMAGE:-ghcr.io/qingdeng888/aqua-platform-open}:${GW_IMAGE_TAG:-latest}`（移除 `build` 段），部署与升级变为 `docker compose pull && docker compose up -d`；新增 `docker-compose.local.yml` 覆盖层（`build` + `image: aqua-gateway:local` + `pull_policy: build`）供本机源码构建，只重定义 `gateway`，`db`/网络/数据卷全部继承，两种方式共用同一 `pgdata` 卷可无损来回切换；`.env.example` 新增 `GW_IMAGE`/`GW_IMAGE_TAG` 两项
- **管理员密码收敛为单一明文变量**：删除 bcrypt 哈希方案（`ACU_ADMIN_PASSWORD_HASH` 变量、`bcrypt.checkpw` 分支、`bcrypt>=4.0.0` 依赖一并移除），只保留 `ACU_ADMIN_PASSWORD` 明文 + `hmac.compare_digest` 恒定时间比较。动因：两套并存带来的复杂度远大于收益——`.env` 本就明文存着库密码与加密主密钥，哈希再包一层的边际收益很低，而"先装 bcrypt 再手搓哈希"在 Debian 上会被 PEP 668 的 `externally-managed-environment` 挡住，且哈希含 `$` 会被 docker compose 对 `env_file` 做变量插值静默截断（`$2b$12$bdJq…` → `$2b$12.yz…`），表现为配置无误却怎么都登不上。顺带修掉两个登录口的行为分叉：此前 `admin_api.py` 只认哈希、`admin_panel.py` 两者都认，只配明文时面板能进而控制台直接启动失败；现在两者读同一变量。校验逻辑收进 `_verify_admin_password()`，空值先挡再比较（`compare_digest(b"", b"")` 为真），登录端点去掉 `to_thread`（恒定时间比较为微秒级，无需线程池）
- **文档（Docker 踩坑）**：`env_file` 里的值含未加引号的 `$xxx` 会被 docker compose 当变量插值静默替换/截断——管理员密码若含 `$` 请用单引号包裹；`.env.example` 已标注
- **上游密钥批量添加**：新增 `POST /gw/admin/upstreams/bulk` 与控制台「上游密钥」页第二个按钮「批量添加」——多行文本框粘贴，一行一个密钥，空行与 `#` 注释行忽略；名称由后端按 `{前缀}-{序号}` 自动生成（默认前缀 `nv`，序号从库内同前缀最大值续排并跳过已被占用的名字，因为 `upstream_keys.name` 无唯一索引，重名不报错但会让运维分不清）。逐行报告结果：批内重复、库中已存在、含空格（多半是粘贴时把两个密钥连成一行）、长度越界的行给出原因并跳过，其余行照常入库；单次上限 200 行（一次请求里做上千次 HKDF+Fernet 不合适）。库内查重必须**解密后比对明文**——Fernet 密文带随机 IV，同一明文两次加密结果不同，比密文永远查不到重复；解密与加密循环都走 `asyncio.to_thread`（每次都要重跑一遍 HKDF）。全部有效行走**一条多值参数化 INSERT**，要么全进要么全不进，不留"导入一半"的中间态；审计仍是一密钥一行（保留 `target_id` 可追溯性），经新增的 `database.insert_audit_many()` 一次往返写入。响应只回行号 / id / 名称 / `mask_secret` 掩码前缀，**绝不回传明文**，跳过原因也不含密钥内容；前端结果面板全程 `textContent` 输出。**单个添加路径（`POST /upstreams` + `GW.actions['upstream-create']`）行为不变**，并由一条 pytest 源码契约与一项前端静态检查双向锁定
- **代理池批量添加**：新增 `POST /gw/admin/proxies/bulk` 与控制台「代理池」页第二个按钮「批量添加」——多行文本框粘贴，一行一个 `协议://[用户名:密码@]地址:端口`（无认证省略凭据部分），空行与 `#` 注释行忽略；名称由后端按 `{前缀}-{序号}` 自动生成（默认前缀 `px`，复用上游密钥批量添加的 `gen_bulk_names()`，不另搓一套序号逻辑）。行解析交给标准库 `urlsplit` 而非手写切分——它已正确处理三件容易写错的事：密码含 `@` 时从**最右侧**切 userinfo、密码含 `:` 时只按**首个** `:` 分割、IPv6 方括号写法；用户名/密码再过一遍 `unquote()`，与 `build_proxy_url()` 的 `quote(safe="")` 形成往返对称（有单测逐字校验 `解析 → 拼装 → 再解析`）。逐行给出跳过原因：缺协议前缀 / 协议不在白名单 / 端口缺失或不在 1-65535（`urlsplit` 放行 `:0`，此处对齐 `build_proxy_url` 的 1-65535 一并拒掉）/ 地址后带路径或参数（裸尾斜杠容忍，浏览器地址栏复制常带）/ IPv6 字面量（`build_proxy_url` 拼回 URL 时不补方括号，收下即存坏数据）/ 有密码无用户名 / 批内重复 / 库中已存在。**查重与上游密钥形成对照**：代理的身份列（协议 / 地址 / 端口 / 用户名）都是明文列，一条 `SELECT` 即可，不必解密；判重按四元组而非三元组——同 IP 同端口不同账号是不同代理（住宅代理常以用户名区分会话/出口）。有密码的行整批在 `asyncio.to_thread` 内加密（每次都要重跑一遍 HKDF），随后走**一条多值参数化 INSERT**，要么全进要么全不进；写库后 `proxy_pool.invalidate()` 让 5 秒快照立即失效，审计一代理一行经 `insert_audit_many()` 一次往返写入。响应只回行号 / id / 名称 / 协议 / 地址 / 端口 / 用户名 / `has_auth`，**不含密码**；跳过原因绝不回显原始行（行里带着密码明文）。**单个添加路径（`POST /proxies` + `GW.actions['proxy-create']`）行为不变**，由 pytest 源码契约与前端静态检查双向锁定
- **模型白名单下线 + 模型管理页**：删除 `public_api.py` 里硬编码的 `_VERIFIED_WORKING_MODELS`（24 条"已验证可用"清单与上游 `/models` 取交集）——本机实测它把上游 83 个真实模型压到 23 个，且上游每上新模型都要改代码发版。现在 `fetch_upstream_models()` 原样收下上游全量，可见性交给新增的覆盖表 `model_overrides`（`hidden` 隐藏 / `manual` 手动补录，遵循「只有携带信息的模型才有行」：取消隐藏上游模型直接删行）。新增独立后端模块 `gateway/app/model_registry.py`（`GET/POST/DELETE /gw/admin/models`、`PUT /gw/admin/models/visibility`、`PUT /gw/admin/models/block-setting`，均需管理员 Token，写操作走 `asyncio.to_thread` + 失效 30 秒快照 + 落审计）与控制台「模型管理」页（12 → 13 页）：搜索（前端即时过滤，同时匹配模型 ID 与备注，重绘只换表格与计数所以不丢焦点）、单行与批量隐藏/显示（批上限 500）、手动补录、仅删手动项。**「隐藏」默认只影响列表**，是否连调用一起禁掉由新设置 `hidden_models_block_calls` 决定（默认 `false`；开启后被隐藏模型的调用返回 400 `model_disabled`）。**关键不变量：可见性 ≠ 名称有效性**——模型 ID 纠错集合喂的是 `all_known_models()`（上游全量 ∪ 手动补录，含被隐藏项），否则隐藏 `X` 后客户端指名调 `X` 会被 6 级模糊匹配静默改写成另一个相似模型。`nim_models.py` 的 `NIM_MODEL_CATALOG` 退化为纯展示元数据（显示名 / 上下文长度 / 能力标签），不再参与过滤；随之消失的"清单兜底"意味着上游已下架的 `stepfun-ai/step-3.7-flash` 不再出现在列表里，需要的话用「手动添加」补录
- **模型别名 / 映射**：新增覆盖层之上的第四层「别名层」——新表 `model_aliases`（`alias` 主键 + `lower(alias)` 唯一索引，因为解析大小写不敏感，允许 `NV/x` 与 `nv/x` 并存会让解析结果不确定）与两个端点 `PUT/DELETE /gw/admin/models/alias`。语义对齐 CLIProxyAPI：`name`→`target_model`、`alias`→`alias`、`display-name`→`display_name`、`fork`→`keep_original`（默认 0：别名替换真名）、`force-mapping`→`force_mapping`（默认 1：把响应体 `model` 回写成别名）。**两处刻意偏离**：`force_mapping` 默认开（CLIProxyAPI 默认关是多 provider 兼容顾虑，本网关只有 NIM 一个上游，下游只看到别名却在响应里收到真名并不一致，还会把真实厂商名漏回去，逐条可关）；别名与真实模型 ID 撞名**直接拒写** `400 alias_conflicts_model`（CLIProxyAPI 是别名优先、静默遮蔽真实模型——遮蔽是陷阱）。**关键不变量：别名不进纠错集合**——纠错函数的返回值会直接写进 `body["model"]`，别名进了集合就可能被 6 级模糊匹配成别名再原样发给上游 → 404；所以解析在 `validate_and_correct_model` **之前**完成，`refresh_verified_models` 仍只吃 `all_known_models`。**解析后全链路只用真名**：熔断器 key、`scheduler.select_key` 分桶（别名单独分桶会让 429 冷却失效）、请求日志与统计都是真名，别名只出现在对外列表与响应体 `model` 两处。旁路一并处理：`/v1/embeddings` 没有纠错步骤，同样先解析；「模型测试」页的 `probe` 直连上游而列表与下游一致，也先解析回真名并回报 `upstream_model`。流式逐 chunk 回写只落在 `json.loads` 成功分支内，`alias_out` 为空时完全不进该分支（无别名请求保持字节级透传）；非流式回写发生在写日志之前，`response_body` 与下游所见一致。真名一律照旧放行（要禁真名请用「隐藏」+「隐藏即禁用」开关）；目标模型被隐藏时其别名条目也一并从 `/v1/models` 消失，用别名调用同样按真名判定返回 400 `model_disabled`。控制台仍是同一个「模型管理」页（不新增导航项）：统计卡加「已设别名」、模型表加「别名」列与行动作「+ 别名」（目标由行决定，避免手输拼错）、下方新增「模型别名 / 映射」表（编辑 / 删除、目标已下架打红色徽标），布尔开关用 `select` 表达故 `core.js` 无需改动，搜索框一词同时过滤两张表
- **测试**：后端 348 passed（代理 URL/客户端/选路 21 个 + 代理凭据加密与三路派生隔离用例；新增 `utc_from_ts` 格式/取值 3 个 + 写库路径不得回退到本地时区写入的守卫 2 个；新增模型测试 45 个：提示词归一 / `max_tokens` 收敛 / 回复与错误提取三态 / 路由与常量契约 / `extra="forbid"` / 不外泄凭据的源码守卫；新增管理员密码 16 个：明文校验（含非 ASCII 与含 `$` 口令）/ 不做 strip 与大小写归一 / 空配置拒绝一切登录 / `[FATAL]` 文案须点名变量名 / 登录端点必须走统一校验函数 / 两个登录口与 requirements 不得残留哈希方案；新增上游密钥批量添加 30 个：逐行解析（行号含空行注释、CRLF、含空格、长度上下边界含端点、批内查重指向首次出现行、跳过原因绝不回显密钥）/ 自动命名（空库从 01 起、按最大值续排、其他前缀不串号、跳过已占用名、`nv-99` → `nv-100`、空白前缀回落默认、超长前缀截断、正则元字符前缀按字面量处理）/ 源码契约（单个添加路径保留、批量端点注册与鉴权、响应只带掩码前缀、行数上限、解密查重、双缓存失效、一密钥一审计行）；新增代理池批量添加 52 个：逐行解析（行号含空行注释、CRLF、首尾空白、协议与主机名大小写归一、四种协议白名单全覆盖、端口边界 1/65535 含端点、`:0` 与 `:65536` 与非数字端口、缺协议/缺地址/缺端口、带路径与参数与 fragment、裸尾斜杠容忍、IPv6 拒收、有密码无用户名、跳过原因绝不回显原始行）/ 凭据保真（密码含裸 `@`、`%40`、裸 `:`、用户名含 `%3A`、中文凭据，以及 `解析 → build_proxy_url → 再解析` 往返逐字一致）/ 批内查重（四元组指向首次出现行、同端点同账号异口令算重复、同端点异账号不算重复、异协议不算重复、尾斜杠形态与裸形态互判重）/ 源码契约（单个添加路径保留、批量端点注册与鉴权、响应不带密码、行数上限、查重不解密、加密在线程内、快照失效、一代理一审计行、复用命名生成器）；新增模型管理 76 个：模型 ID 归一与非法字符拒收、覆盖层应用（剔除隐藏 / 追加排序后的手动项 / 上游收录后不重复追加 / 脏数据跳过）、`all_known_models` 含被隐藏项、管理行的来源与状态标记及按 ID 与备注搜索、`is_call_blocked` 在开关两态下的行为、源码契约（五端点注册与鉴权、删除仅限手动项、取消隐藏不留无信息行、写操作走线程+失效缓存+审计、建表与默认设置、路由注册、白名单确已移除、纠错集合基于全量集合））；新增模型别名层 69 个：`resolve_alias_pure` 三级匹配（精确 / 大小写 / 去分隔符标准化）与未命中原样返回、空值与脏数据（target 为空）不改写、`force_mapping` 透传、`apply_aliases`（替换 / `keep_original` 并存 / 同 target 多别名 OR 语义 / 别名按名排序在原位追加 / `owned_by` 取 provider 段 / 其余字段继承 / 不改原对象 / target 被隐藏则别名条目也不出现）、`build_alias_rows`（排序、`target_missing`、按别名·目标·备注搜索）、管理行携带别名与按别名搜索、源码契约（两端点注册与鉴权、写操作走线程+失效缓存+审计、四个错误码及其校验顺序、撞名与 target 存在性都对照 `all_known_models`、大小写唯一索引与 upsert 前的旧写法清理、别名解析在纠错之前、`refresh_verified_models` 只出现一次且只吃真名、流式回写落在 JSON 成功分支内、非流式回写在写日志之前、`/v1/embeddings` 与 `probe` 均解析、建表与 `lower(alias)` 唯一索引）；前端静态检查 59 项（代理池单个/批量双路径动作齐全、`/proxies/bulk` 调用锚点、批量结果面板被两处复用且仍无 `innerHTML`、批量动作用 textarea 收多行；代理池动作与出网徽标守卫；新增 `fmtTime` 本地化渲染与详情页无裸时间戳守卫；新增模型测试页动作齐全、自测密钥不落盘、可中止、`innerHTML` 只写静态模板、`max_tokens` 默认值前后端一致守卫；新增上游密钥单个/批量双路径动作齐全、`/upstreams/bulk` 调用锚点、`formModal` 支持 textarea、批量结果面板无 `innerHTML`；新增模型管理页动作齐全、搜索为前端即时过滤、隐藏即禁用开关、模型 ID 经 `esc()` 输出且行动作只带 `data-idx`，以及后端白名单已下线、`get_model_list()` 叠覆盖层、纠错基于全量集合、`model_disabled` 拦截四项守卫；新增别名动作齐全（`model-alias`/`alias-edit`/`alias-delete`）、`/models/alias` 双端点调用锚点、别名表与「别名」列经 `esc()`·`badge()` 输出且行动作只带 `data-idx`、同一搜索框过滤两张表且模型搜索命中别名、布尔用 `select` 未改 `core.js`、`target_missing` 红标，以及后端别名层七项守卫：列表流水线含别名层、解析在纠错之前、全链路真名、`/v1/embeddings` 与 `probe` 解析、`lower(alias)` 唯一索引、默认语义）

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
