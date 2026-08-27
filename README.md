# AQUA AI Platform

![Version](https://img.shields.io/badge/version-11.0.0-00d4ff) ![Python](https://img.shields.io/badge/Python-3.12%2B-blue) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15%2B-336791) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009485) ![License](https://img.shields.io/badge/License-MIT-green)

> **开源 AI 网关平台**：将多个 NVIDIA NIM 上游密钥池化，对外提供统一的 OpenAI 兼容 API，配合完整的用户平台（注册 / 密钥管理 / 聊天 / 用量统计）与管理后台。
>
> **版本 v11.0.0** · **协议 MIT** · **语言 中文（简体）**

---

## 目录

1. [v11.0 版本亮点](#v110-版本亮点)
2. [功能特性](#功能特性)
3. [系统架构](#系统架构)
4. [快速开始](#快速开始)
5. [环境变量](#环境变量)
6. [API 使用](#api-使用)
7. [管理后台](#管理后台)
8. [安全体系](#安全体系)
9. [调度与算法](#调度与算法)
10. [并发与限流](#并发与限流)
11. [多协议转换（实验性）](#多协议转换实验性)
12. [生产部署](#生产部署)
13. [运维手册](#运维手册)
14. [开发与测试](#开发与测试)
15. [项目结构](#项目结构)
16. [版本历史](#版本历史)
17. [开源协议](#开源协议)

---

## v11.0 版本亮点

v11.0 是一次**全面安全加固 + 性能重构 + 前端重写**的版本：

| 方向 | 变更 |
|------|------|
| **安全加固** | 修复密钥吊销失效、管理面空密码、XFF 伪造、未鉴权端点、存储型 XSS、chunked 绕过限流、平台令牌越权等全部已知高危；新增登录/发码/注册限流、会话吊销、Origin 同源校验、Cookie Secure |
| **性能** | 事件循环零同步阻塞（209 处 `to_thread` 化）、连接池可配置 + 预热、httpx 池/超时显式调优、SSE 真心跳保活、启动缓存预热 |
| **可靠性** | 时间戳统一 UTC（修复统计 +8h 漂移）、上游密钥自动停用探活恢复、熔断器半开状态机修复、6 个 500 端点修复 |
| **前端重写** | 用户端与管理端全部重构：零构建原生 ES 模块、统一深色设计语言、危险操作二次确认；修复原版 10 余处失效交互 |
| **工程化** | requirements 双侧修正、`.env.example` 完备、文档与现实对齐、30 个单元测试 + 21 项前端静态检查 |

---

## 功能特性

| 特性 | 说明 |
|------|------|
| **密钥池化** | 多个 NVIDIA NIM 上游密钥整合为统一入口，密钥 Fernet+HKDF 加密存储 |
| **统一 API** | OpenAI 兼容：`/v1/chat/completions`、`/v1/embeddings`、`/v1/models` |
| **17 算法互锁调度** | RPM、健康度、冷却、预热、熔断等 17 个协同算法计算最优密钥选择 |
| **故障自愈** | 429/5xx 熔断（半开探测恢复）、自适应冷却、冷密钥渐进预热、自动故障转移 |
| **模型 ID 映射** | 数百条别名、6 级匹配策略，非标准 ID 自动纠错映射 |
| **用户平台** | 注册登录（邮箱验证码）、密钥管理、Web 聊天（流式）、用量统计、请求日志、反馈 |
| **管理后台** | 网关控制台（12 页面）+ SQLAdmin 数据库面板 + 平台用户管理 |
| **多级缓存** | L1/L2 两级进程内内存缓存（LRU + TTL）：API Key 缓存、限流计数、TPM/RPM 追踪 |
| **安全体系** | bcrypt(12)、SHA-256 密钥哈希查库、平台令牌 scope、IP 监控、可信代理模型 |

**如实声明**：多协议转换（Anthropic/Gemini）当前为**内置转换器、实验性、未接入主链路**（详见[多协议转换](#多协议转换实验性)）；分布式缓存（Redis）未引入；仅支持 PostgreSQL。

---

## 系统架构

双服务架构，两个独立进程各司其职：

```
┌──────────────┐                                  ┌──────────────────────┐
│  用户/浏览器   │── HTTP ──► Platform (:8001)     │      IDE / 客户端      │
└──────────────┘            注册·登录·密钥·聊天     │ Cursor / 通用 SDK ...  │
                            SQLAdmin(:8001)       └──────────┬───────────┘
                                     │ AQUA_PLATFORM_TOKEN    │ Bearer sk-xxx
                                     ▼                        ▼
                            ┌─────────────────────────────────────┐
                            │          Gateway (:8000)             │
                            │  认证(SHA-256查库) → 校验 → 17算法调度  │
                            │  → httpx 上游池 → SSE/JSON 响应       │
                            │  控制台 /gw/admin · SQLAdmin /gw/dbadmin │
                            └──────────────────┬──────────────────┘
                                               │ 密钥池（Fernet 加密）
                                               ▼
                                   NVIDIA NIM (integrate.api.nvidia.com)
```

- **Platform（:8001）**：面向终端用户的 Web 平台，以客户端身份调用 Gateway 管理 API 发放密钥
- **Gateway（:8000）**：面向 API 客户端的入口，负责认证、调度、转发、限流、日志
- **服务间认证**：`AQUA_PLATFORM_TOKEN`（`apt_` 前缀，SHA-256 哈希存储，**按 scope 授权**）
- **数据库**：两个独立的 PostgreSQL 库（`aqua_gateway` / `aqua_platform`），psycopg2 同步池 + asyncpg 异步池

---

## 快速开始

### 1. 环境要求

- Python **3.12+**（开发验证于 3.12，推荐 3.13+）
- PostgreSQL **15+**（必需，无 SQLite 模式）
- NVIDIA NIM API 密钥（[build.nvidia.com](https://build.nvidia.com)）
- 无需 Node.js（前端零构建）

### 2. 安装

```bash
git clone https://github.com/buyi06/aqua-platform-open.git
cd aqua-platform-open

python3 -m venv venv && source venv/bin/activate
pip install -r gateway/requirements.txt -r platform/requirements.txt

cp .env.example .env   # 然后编辑 .env，逐项填写
```

### 3. 准备数据库

```sql
CREATE USER aqua WITH PASSWORD '强密码';
CREATE DATABASE aqua_gateway OWNER aqua;
CREATE DATABASE aqua_platform OWNER aqua;
```

### 4. 必填环境变量

打开 `.env`，**至少**配置以下各项（完整清单见[环境变量](#环境变量)）：

| 变量 | 说明 |
|------|------|
| `ACU_ADMIN_PASSWORD_HASH` | 管理员密码 bcrypt 哈希（Gateway Admin API 必需） |
| `AQUA_PLATFORM_TOKEN` | 服务间令牌（随机长串，两服务共用） |
| `JWT_SECRET_KEY` / `PLATFORM_ENCRYPT_KEY` | Platform JWT 签名 / 用户密钥加密（Fernet） |
| `PG_PASSWORD` / `PG_PLATFORM_PASSWORD` | 两个数据库的密码 |

### 5. 启动

```bash
# 网关（端口 8000）
cd gateway && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 平台（端口 8001，另开终端）
cd platform && python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

浏览器打开 `http://127.0.0.1:8001` 注册账号，在控制台创建 API 密钥，即可通过 `http://127.0.0.1:8000/v1` 调用。

> 生产部署（反代 / HTTPS / systemd）见[生产部署](#生产部署)。

---

## 环境变量

完整模板见 [`.env.example`](.env.example)（含生成命令）。常用项：

| 变量 | 默认 | 说明 |
|------|------|------|
| `ACU_ADMIN_PASSWORD_HASH` | — | 管理员密码 bcrypt 哈希，**优先**；`/gw/admin/*` 必需 |
| `ACU_ADMIN_PASSWORD` | — | 明文回退（恒定时间比较）；两者均未配置时管理面板禁用 |
| `AQUA_PLATFORM_TOKEN` | — | Platform→Gateway 服务间令牌，两侧一致 |
| `ADMIN_SESSION_SECRET` | 随机 | SQLAdmin 面板 Session 密钥（未设置则重启失效） |
| `PLATFORM_ADMIN_SESSION_SECRET` | — | Platform 管理会话签名密钥（未配置则拒绝签发） |
| `JWT_SECRET_KEY` | — | Platform 用户 JWT（HS256），必需 |
| `PLATFORM_ENCRYPT_KEY` | — | 用户 API Key 加密（Fernet），**启用后不可更换** |
| `PG_HOST/PORT/DB/USER/PASSWORD` | localhost | Gateway 数据库；`PG_PASSWORD` 必填 |
| `PG_PLATFORM_*` | localhost | Platform 数据库 |
| `PG_GATEWAY_*` | localhost | Platform 跨库读网关数据（控制台用量） |
| `GW_BASE_URL` | `http://127.0.0.1:8000` | Platform 调用网关地址 |
| `GW_DB_POOL_SIZE` | 30 | Gateway psycopg2 连接池大小（下限 5） |
| `SESSION_COOKIE_SECURE` | 1 | 登录 Cookie 仅 HTTPS；本地 HTTP 调试设 0 |
| `REGISTRATION_OPEN` | 1 | 是否开放注册 |
| `AQUA_TRUST_PROXY_HEADERS` | 0 | 反代部署设 1（信任 XFF 等头获取真实 IP） |
| `AQUA_WATCHLIST` | 空 | 商用检测监控名单（逗号分隔客户端名） |
| `AQUA_DEBUG_ERRORS` | 0 | 错误响应返回内部详情（仅调试） |
| `CORS_ALLOWED_ORIGINS` | localhost | CORS 白名单，禁止 `*` |
| `SMTP_HOST/PORT/USER/PASSWORD` | 空 | 注册验证码 / 维护通知邮件 |

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
| `/api/public/models` | GET | Platform 侧公开模型目录 |
| `/healthz` | GET | 健康检查（公开基础字段；`?verbose=1` 需管理员） |

### 模型 ID 智能映射

用户输入非标准 ID 时自动映射（如 `deepseek-v4-pro` → `deepseek-ai/deepseek-v4-pro-0813`）。当前目录以 `gateway/app/nim_models.py` 为唯一权威来源，涵盖 NVIDIA NIM 实测可用模型（Nemotron-3 全系、DeepSeek-V4、Gemma-4、Kimi-K3、MiniMax-M3 等）。

---

## 管理后台

| 入口 | 地址 | 认证 |
|------|------|------|
| 网关控制台 | `http://127.0.0.1:8000/admin` | 管理员密码登录（HMAC Token，24h） |
| 网关 SQLAdmin | `/gw/dbadmin` | 同一密码（Session） |
| 平台管理页 | `http://127.0.0.1:8001/admin` | 同一密码 |
| 平台 SQLAdmin | `/platform/dbadmin` | 同一密码 |

**密码契约**（所有管理面统一）：优先校验 `ACU_ADMIN_PASSWORD_HASH`（bcrypt），未配置时回退 `ACU_ADMIN_PASSWORD`（恒定时间比较）；**两者均未配置时一切登录被拒绝**（杜绝空密码）。

网关控制台 12 个页面：仪表盘、上游密钥（含明文 reveal）、下游客户、桶监控、请求日志、算法引擎（17 算法实时状态）、系统监控、系统配置、平台令牌（**scopes 细粒度授权**）、错误码、商用检测。

**平台令牌 scope**：`upstreams:reveal` / `keys:reveal` / `keys:write` / `clients:write` / `clients:delete` / `settings:write` / `models:read`。Platform 正常运行只需默认四项（`clients:write`、`keys:write`、`keys:reveal`、`models:read`）；上游密钥明文与配置写入默认不对平台开放。

---

## 安全体系

v11.0 加固后的完整防线：

- **密钥安全**：上游密钥 Fernet+HKDF 加密存储；客户端密钥仅存 SHA-256 哈希（查库认证）+ Fernet 密文（发放时一次性返回明文）；面板/列表接口不回显密文与哈希
- **认证**：bcrypt(12) 密码哈希；JWT 绑定会话（重置密码即全量吊销）；平台令牌 scope 强制
- **防滥用**：登录失败 5 次/15 分钟锁定、发码 10 次/小时/IP、注册 5 次/天/IP；429/5xx 熔断与自适应冷却
- **网络层**：可信代理模型（公网直连忽略一切伪造头，私网对端才信任 CF/XFF）；CORS 白名单；请求体 10MB 硬限（含 chunked 编码）
- **Web 安全**：全前端 XSS 纪律（escapeHtml / 事件委托，密钥等敏感值不进内联属性）；Origin 同源校验 + SameSite=lax 双保险；Cookie `Secure`（默认开）
- **隐私**：日志请求体脱敏（`[REDACTED]`）、响应体截断 8KB、3 天自动清理成功日志；管理面板不展示用户对话内容
- **凭据隔离**：客户端密钥不转发上游，上游密钥不回传客户端，请求头在网关层重建

---

## 调度与算法

调度器（`SurgeScheduler`）以 **17 个互锁算法**协同工作，从请求进入 to 响应完成全程介入：

| 阶段 | 算法（节选） |
|------|------|
| 密钥选择 | RPM 均值、健康度评分、权重、冷却水位、冷密钥预热 |
| 运行时 | 熔断器（CLOSED/OPEN/**HALF_OPEN** 探测恢复）、并发槽位、慢速模型超时 |
| 自愈 | 阈值自适应、健康探测、自动停用 + **30 分钟探活恢复**、桶解冻 |
| 观测 | 桶快照、错误追踪、P95 延迟、调度耗时（请求级 ContextVar） |

> 算法仅依赖进程内状态，**必须单 worker 部署**（`uvicorn` 不加 `--workers`）；多实例需各自独立数据库分片。算法细节见 [ARCHITECTURE.md](ARCHITECTURE.md) 第 4 章权威表。

---

## 并发与限流

v11.0 起**无硬性并发数限制**（不因并发拒绝请求），保留以下机制：

- **软限流排队**：新注册用户请求按 120 RPM 均匀铺开（响应完成间隔 ≥500ms），不返回 429
- **上游熔断**：429/5xx 触发密钥级熔断与冷却，请求自动转移到池内其他密钥
- **IP 监控**：滑窗 RPS + 周期性检测，高分异常 IP 自动封禁 24h（自动过期解封）

---

## 多协议转换（实验性）

仓库内置 Anthropic / Gemini 双向协议转换器（`gateway/app/translator.py`、`gateway/app/transformers/`），支持 tool_use/tool_result、多模态块、流式事件序列（符合 Anthropic SDK 规范）。

> **当前状态：未接入主链路。** 生产请求为 OpenAI 协议直通（零转换损耗）。相关模块已修复并标注实验性，启用需在 `public_api` 增挂对应路由（如 `/v1/messages`），欢迎 PR。

---

## 生产部署

### Nginx 反向代理

```nginx
# 网关 API
server {
    listen 443 ssl http2;
    server_name api.你的域名.com;
    ssl_certificate     /etc/letsencrypt/live/api.你的域名.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.你的域名.com/privkey.pem;

    location /v1/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;      # 流式长响应
        proxy_buffering off;          # SSE 必关
    }
}

# 平台 Web
server {
    listen 443 ssl http2;
    server_name 你的域名.com;
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> 反代部署时记得 `AQUA_TRUST_PROXY_HEADERS=1`，否则网关看到的客户端 IP 全是 127.0.0.1。

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

Platform 同理（`WorkingDirectory=/opt/aqua/platform`，端口 8001）。`systemctl enable --now aqua-gateway aqua-platform`。

### 上线检查清单

- [ ] `.env` 全部密钥为随机强值；`.env` 权限 600
- [ ] `ACU_ADMIN_PASSWORD_HASH` 已配置（勿只留明文变量）
- [ ] HTTPS 已启用（`SESSION_COOKIE_SECURE=1` 才能正常登录）
- [ ] 反代场景 `AQUA_TRUST_PROXY_HEADERS=1`；直连场景保持 0
- [ ] `CORS_ALLOWED_ORIGINS` 为实际域名
- [ ] 不需要公开注册时 `REGISTRATION_OPEN=0`
- [ ] `AQUA_DEBUG_ERRORS=0`
- [ ] 单 worker 运行（调度器为进程内状态）

---

## 运维手册

| 场景 | 操作 |
|------|------|
| 自动恢复 | `scripts/auto_recovery.sh`（cron 每 1 分钟；`VENV_DIR` 环境变量可覆盖虚拟机路径） |
| 日志清理 | 调度器每 6 小时自动执行：成功日志保留 3 天、错误日志 90 天 |
| 维护通知 | `scripts/send_maintenance_notice.py`（分批发送防 SMTP 限频） |
| SQLite 迁移 | `scripts/migrate_to_postgresql.py`（历史版本数据迁移，非运行时依赖） |
| 密钥自愈 | 上游密钥两次 401/403 自动停用后，每 30 分钟探活，恢复即自动回归池内 |

---

## 开发与测试

```bash
# 后端单元测试（30 个：安全/时间戳/并发/错误体系）
python -m venv .venv-test
.venv-test/Scripts/pip install -r gateway/requirements.txt -r platform/requirements.txt pytest pytest-asyncio   # Linux 为 .venv-test/bin/pip
.venv-test/Scripts/python -m pytest tests/ -v

# 前端静态检查（21 项：引用完整性/无内联事件/XSS 纪律/API 契约锚点/路由注册）
node tests/static-smoke.mjs

# 单文件语法检查
python -m py_compile gateway/app/xxx.py
node --check platform/app/static/js/xxx.js
```

测试说明：`gateway/app` 与 `platform/app` 是两个同名 `app` 包，`tests/_app_path.py` 负责切换，无需手动处理 sys.path。

---

## 项目结构

```
aqua-platform-open/
├── gateway/                  # 网关服务 (:8000)
│   ├── app/
│   │   ├── main.py           # 入口：lifespan 预热/后台任务/错误处理
│   │   ├── public_api.py     # OpenAI 兼容 API 主链路
│   │   ├── admin_api.py      # /gw/admin/* 管理端点（scope 强制）
│   │   ├── scheduler.py      # 17 算法调度器
│   │   ├── middleware.py     # 大小限制/日志/IP 提取/CORS/维护模式
│   │   ├── database.py       # psycopg2 连接池(autocommit)/时间戳家族
│   │   ├── circuit_breaker.py / ip_monitor.py / commercial_detect.py
│   │   ├── translator.py + transformers/   # 协议转换器（实验性）
│   │   ├── platforms/        # 上游适配器（实验性）
│   │   └── static/           # 控制台 UI（模块化 JS）
│   └── requirements.txt
├── platform/                 # 用户平台 (:8001)
│   ├── app/
│   │   ├── main.py           # 入口：Origin 校验/Session 中间件
│   │   ├── routes/           # auth / console / chat / public / platform_admin
│   │   ├── gateway_client.py # 调用网关管理 API
│   │   ├── email_service.py / soft_limiter.py / behavior.py
│   │   └── static/           # 用户端 UI（零构建 ES 模块 ×26）
│   └── requirements.txt
├── scripts/                  # auto_recovery / 迁移 / 维护通知
├── tests/                    # pytest ×30 + 前端静态检查
├── docs/rules/               # 并发规则文档
├── .env.example              # 环境变量模板（含生成命令）
├── ARCHITECTURE.md           # 架构权威文档（17 算法表）
└── LICENSE                   # MIT
```

---

## 版本历史

### v11.0.0（2026-08-27）

- **安全**：修复密钥吊销失效（缓存逐条 TTL + 即时失效）、三处管理面空密码、XFF 伪造绕过、`/api/user/system/*` 未鉴权、管理页存储型 XSS、chunked 绕过体积限制、平台令牌 scope 未强制；新增限流/会话吊销/Origin 校验/Cookie Secure/日志脱敏
- **性能**：事件循环 209 处同步调用异步化；连接池 `GW_DB_POOL_SIZE` 可配 + 预热；httpx 池与超时调优；SSE 心跳保活（15s ping / 180s 空闲上限）；启动缓存预热
- **可靠性**：时间戳统一 UTC Z 格式（修复统计 +8h）；上游密钥停用后探活自动恢复；熔断器半开状态机与滚动窗口失败率；6 个 ImportError 端点修复；SSE 部分内容恢复
- **前端**：用户端 index.html 3165→154 行、26 个 ES 模块重写；网关控制台与平台管理页重构；修复原版 10 余处失效交互与 4 个存量前端 bug
- **工程**：requirements 双侧修正（补 bcrypt/psycopg2/asyncpg/dotenv/itsdangerous 等）；恢复 `.env.example`；文档与现实全面对齐；测试 30 + 静态检查 21；清理未接线的 vendored 组件与内部脚本

### v10.0.0（2026-07）

- 初始开源版本：双服务架构、17 算法调度、密钥池化、SQLAdmin、多级缓存

---

## 开源协议

[MIT](LICENSE) © 2026 AQUA Platform Contributors

欢迎 Issue / PR。贡献前请阅读 [ARCHITECTURE.md](ARCHITECTURE.md) 了解架构约束（单 worker、仅 PostgreSQL、零构建前端）。
