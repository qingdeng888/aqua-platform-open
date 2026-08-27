# AQUA AI Platform — 开源 AI 网关平台

> **版本**: v11.0
> **协议**: MIT
> **语言**: 中文（简体）

---

## 项目简介

AQUA AI Platform 是一个开源的 AI 网关平台，核心使命是将多个 NVIDIA NIM 上游密钥**池化**，为终端用户提供**统一的 OpenAI 兼容 API** 入口。平台通过密钥池化技术，将分散的上游 API 密钥整合为一个高可用的统一入口，降低用户接入门槛，提升密钥利用率，并实现智能负载均衡与故障自愈。

开源版本支持用户在自己的域名、服务器上独立部署，完整代码贡献给社区。

---

## 目录

1. [核心特性](#1-核心特性)
2. [技术栈](#2-技术栈)
3. [系统架构](#3-系统架构)
4. [双服务架构](#4-双服务架构)
5. [17 算法互锁调度体系](#5-17-算法互锁调度体系)
6. [协议转换与多协议支持](#6-协议转换与多协议支持)
7. [模型 ID 智能映射](#7-模型-id-智能映射)
8. [安全体系](#8-安全体系)
9. [慷慨型网关](#9-慷慨型网关)
10. [龙虾文档适配器](#10-龙虾文档适配器)
11. [管理后台](#11-管理后台)
12. [并发与限流策略（v11.0）](#12-并发与限流策略v110)
13. [项目目录结构](#13-项目目录结构)
14. [快速部署](#14-快速部署)
15. [配置说明](#15-配置说明)
16. [API 使用指南](#16-api-使用指南)
17. [Nginx 反向代理部署](#17-nginx-反向代理部署)
18. [Systemd 服务部署](#18-systemd-服务部署)
19. [数据库说明](#19-数据库说明)
20. [开源协议与贡献](#20-开源协议与贡献)

---

## 1. 核心特性

| 特性 | 说明 |
|------|------|
| **密钥池化** | 将多个 NVIDIA NIM 上游 API 密钥整合为统一入口，提升利用率与可用性 |
| **统一 API** | 提供 OpenAI 兼容的 `/v1/chat/completions`、`/v1/embeddings`、`/v1/models` 端点 |
| **17 算法互锁调度** | 17 个协同算法从 RPM、健康度、冷却、预热等维度守护密钥池，最终由严格公平调度器均匀分配流量 |
| **协议转换（实验性）** | 内置 Anthropic / Gemini 协议转换器（`transformers/`）；当前主链路直通 OpenAI 协议，转换器未默认启用 |
| **模型 ID 智能映射** | 400+ 别名条目，6 级匹配策略，用户输入非标准 ID 时自动映射到正确模型 |
| **故障自愈** | 429/5xx 熔断、自适应冷却、冷密钥渐进式预热、自动故障转移 |
| **安全体系** | Fernet 对称加密 + HKDF 密钥派生、JWT 认证、bcrypt 密码哈希、IP 监控 |
| **管理后台** | SQLAdmin 自动生成数据库 CRUD 后台，Admin API 提供密钥/客户/桶监控管理 |
| **文档解析** | PDF/DOCX/HTML/TXT 多格式解析并注入 LLM 上下文（龙虾文档适配器） |
| **多级缓存** | L1/L2 两级进程内内存缓存（LRU + TTL），用于 API Key 缓存、限流计数、TPM/RPM 追踪；分布式缓存（Redis）为未来规划，当前未引入 |

---

## 2. 技术栈

### 2.1 后端

| 类别 | 技术 | 说明 |
|------|------|------|
| **运行时** | Python 3.13+（目标 3.14） | 充分利用最新语言特性与性能提升 |
| **Web 框架** | FastAPI >= 0.115.0 | 全异步框架，原生 OpenAPI 文档 |
| **数据库** | PostgreSQL 15+（**必需**） | 仅支持 PostgreSQL，无 SQLite 模式 |
| **同步驱动** | psycopg2-binary >= 2.9.9 | ThreadedConnectionPool 同步连接池 |
| **异步驱动** | asyncpg >= 0.30.0 | 异步 SQL 访问（SQLAlchemy async engine） |
| **ORM** | SQLAlchemy 2.0 async | 模型定义与异步查询 |
| **管理后台** | SQLAdmin >= 0.20.0 | 自动生成数据库 CRUD 管理界面（依赖 itsdangerous 会话签名） |
| **数据校验** | Pydantic V2 | 请求/响应数据校验与序列化 |
| **HTTP 客户端** | httpx >= 0.28.0 | 异步连接池，用于上游 API 调用 |
| **加密** | cryptography >= 43.0.0 | Fernet 对称加密 + HKDF 密钥派生 |
| **重试** | tenacity >= 8.2.0 | 指数退避重试策略（仅 Gateway 使用） |
| **JWT** | python-jose >= 3.3.0 | JWT 令牌签发与验证（HS256，仅 Platform 使用） |
| **密码哈希** | bcrypt >= 4.0.0 | 用户/管理员密码哈希，12 rounds |
| **文档解析** | pypdf / python-docx / beautifulsoup4 / lxml | PDF/DOCX/HTML 多格式解析（仅 Gateway 使用） |
| **环境变量** | python-dotenv >= 1.0.0 | 从项目根 `.env` 加载配置 |
| **模板** | Jinja2 >= 3.1.0 | HTML 模板渲染（Platform） |

---

## 3. 系统架构

### 3.1 整体架构图

```
                           ┌──────────────────────────────────┐
                           │     AQUA AI Platform v11.0       │
                           └──────────────────────────────────┘

 ┌──────────────┐                                                ┌──────────────────────┐
 │  用户/浏览器   │─────── HTTP ────────►  Platform (:8001)       │     IDE 工具          │
 └──────────────┘                       ┌────────────────┐       │                      │
                                        │  用户注册/登录   │       │  Cursor / Claude Code│
                                        │  密钥管理       │       │  Cline / Continue    │
                                        │  用量统计       │       │  Cherry Studio       │
                                        │  SQLAdmin      │       │  通用 IDE 插件        │
                                        └───────┬────────┘       └──────────┬───────────┘
                                                │                     OpenAI 协议请求
                                    AQUA_PLATFORM_TOKEN                (主链路直通)
                                    (内部服务间认证)                           │
                                                ▼                               │
                                        ┌────────────────┐                         │
                                        │  Gateway (:8000)│ ◄── OpenAI 兼容 API ───┘
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │协议转换器    │ │  Anthropic/Gemini 转换器
                                        │ │(实验性,未   │ │  （内置但未默认启用）
                                        │ │ 默认启用)   │ │
                                        │ └────────────┘ │
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │17算法调度器  │ │  健康性过滤 + 严格公平调度
                                        │ └────────────┘ │
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │龙虾文档适配器│ │  PDF/DOCX/HTML/TXT
                                        │ └────────────┘ │
                                        │                │
                                        │ Admin API      │
                                        │ SQLAdmin       │
                                        └───────┬────────┘
                                                │
                                                ▼
                              ┌────────────────────────────┐
                              │ PostgreSQL（网关库 + 平台库）│
                              └────────────────────────────┘
                                                │
                                    ┌───────────┼───────────┐
                                    ▼           ▼           ▼
                              ┌──────────┐ ┌──────────┐ ┌──────────┐
                              │NVIDIA NIM│ │NVIDIA NIM│ │  多供应商  │
                              │ 上游密钥1 │ │ 上游密钥N │ │ 免费额度池 │
                              └──────────┘ └──────────┘ └──────────┘
                                                │
                                                ▼
                              integrate.api.nvidia.com/v1
```

### 3.2 请求流转

```
客户端/IDE 请求（OpenAI 协议，主链路直通）
    │
    ▼
Gateway 入口 → 认证 → 中间件链（维护模式/请求大小限制/日志/CORS）
    │
    ▼
17 算法互锁调度 → 健康性过滤 + 严格公平调度选出最优桶+密钥
    │
    ▼
上游请求转发 → httpx 异步连接池 → tenacity 重试 + 429 自动切换密钥
    │
    ▼
响应处理 → 流式/非流式 → OpenAI 格式响应
```

---

## 4. 双服务架构

AQUA 采用**双服务**架构，两个独立进程各司其职：

| 服务 | 职责 | 默认端口 | 说明 |
|------|------|----------|------|
| **Platform** | 用户平台 | `8001` | 面向终端用户的 Web 界面：注册、登录、密钥管理、用量查看 |
| **Gateway** | API 网关 | `8000` | 面向第三方客户端的 OpenAI 兼容 API 入口，负责调度、转发、限流 |

两服务通过内部平台令牌（`AQUA_PLATFORM_TOKEN`）进行服务间认证，Platform 以客户端身份调用 Gateway 的管理 API 完成密钥发放等操作。

---

## 5. 17 算法互锁调度体系

Gateway 的调度引擎（SurgeScheduler）由 **17 个算法**组成，形成一个互锁协同的调度体系。

**核心机制（v10 重构后）**：评分仅用于**健康性过滤**（评分 < 0.1 视为不健康而被排除），最终的密钥选择由**算法 17 严格公平调度器**决定——在健康候选中按"近期使用次数最少 → 分配序号最早"排序，确保流量均匀分布。

**评分公式**（用于健康性过滤与权重参考）：

```
score = base_weight × A8 × A9 × A11 × A13 × A15 × A16
```

| 算法 | 名称 | 类别 | 触发时机 | 说明 |
|------|------|------|----------|------|
| 1 | 分桶滑动窗口计数器 | 数据采集 | 每次请求完成后 | 为每个 (密钥,模型) 复合桶维护 60 秒滑动窗口，是全部算法的数据底座 |
| 2 | 软繁忙标记器 | 流量控制 | 密钥选择阶段 | RPM 超过动态阈值时标记软繁忙并跳过（与冷却完全分离） |
| 3 | 自适应阈值调节器 | 动态调参 | 每 30 秒后台 | 统一阈值 38 RPM，密钥池总容量 = 密钥数 × 38 |
| 4 | 自适应冷却时长计算器 | 故障保护 | 上游 429/403/超时 | 差异化冷却：429→5s，403→60s 起步指数退避，超时→15s |
| 5 | 客户端并发监测器 | 客户端监控 | 请求入口处 | 记录客户端在途请求数，**只监测不拦截** |
| 6 | 客户端突发率检测器 | 客户端监控 | 请求入口处 | 3 秒内 >20 次请求标记高突发，**只记录不拦截** |
| 7 | 客户端日用量监测器 | 客户端监控 | 评分阶段 | 客户端当日累计用量统计，**只统计不设上限** |
| 8 | 5xx 退避权重衰减器 | 健康评估 | 上游返回 5xx | 连续 5xx 阶梯衰减权重：0.8/0.5/0.2/0，成功即重置 |
| 9 | 区域故障隔离器 | 健康评估 | 连接超时/SSL 错误 | 连续 3 次失败隔离 30 分钟，隔离期权重归零 |
| 10 | 全局健康度评分器 | 后台周期 | 每 30 秒后台 | 综合成功率/P95/429 率/5xx 率计算 0~100 健康分 |
| 11 | 池化动态权重调节器 | 负载均衡 | 评分阶段 | 由健康分线性映射权重乘数 0.5~2.0 |
| 12 | 自适应负载预判器 | 流量控制 | 算法 2 之前 | 5 分钟 RPM 趋势预判，提前排除将达阈值的桶 |
| 13 | 冷密钥渐进式预热器 | 恢复机制 | 冷却/隔离恢复后 | 前 30 个请求权重 0.3→0.6→0.9→1.0 渐进恢复 |
| 14 | 智能异常自愈引擎 | 后台管控 | 每 60 秒后台 | 四级自愈：轻度观察 / 中度流量迁移 / 重度移出候选池 / 全局降级 |
| 15 | 趋势感知自适应均衡 | 负载均衡 | 评分阶段 | 30 秒趋势斜率调节乘数 0.6~1.3 |
| 16 | 龙虾脱壳式弹性调度 | 弹性调度 | 每 120 秒后台 | RPM 持续超 90% 阈值的密钥进入 15 秒脱壳期（乘数 0.5） |
| 17 | 严格公平调度器 | 最终选择 | select_key 最终阶段 | 核心：滑动窗口使用计数 + 公平轮询，目标均衡度 90%+ |

### 桶级冷却四条强制约束

1. 冷却状态只存储在 (key_id, model) 复合桶级别，不存在任何密钥级冷却状态字段
2. `select_key` 检查冷却状态时只使用复合键查询，不存在任何密钥级查询路径
3. 软繁忙与冷却完全分离：RPM 超限只标记桶级软繁忙，永不调用冷却函数
4. 后台异步任务只操作桶级状态，不修改任何密钥级聚合状态

---

## 6. 协议转换与多协议支持

**当前主链路**：OpenAI 协议直通。`/v1/chat/completions`、`/v1/embeddings`、`/v1/models` 端点接收 OpenAI 格式请求并原样转发，不做协议转换。

**内置转换器（实验性，未默认启用）**：

| 转换器 | 位置 | 状态 |
|--------|------|------|
| **Anthropic ↔ OpenAI** | `gateway/app/transformers/anthropic.py` | 实验性实现（含 Tool Calling 转换），当前未接入主链路 |
| **Gemini ↔ OpenAI** | `gateway/app/transformers/gemini.py` | 实验性实现，当前未接入主链路 |
| **translator.py 汇总层** | `gateway/app/translator.py` | 协议检测与转换编排（实验性），当前未接入主链路 |

> 如实说明：网关保留了多协议转换的模块代码（Anthropic/Gemini 双向转换、IDE 特征检测、多轮对话上下文 `ConversationContext`），但这些转换器**未在默认请求链路中启用**。默认部署下，所有请求按 OpenAI 协议直通处理；在 IDE 中使用时请将其配置为 OpenAI Compatible 模式。

---

## 7. 模型 ID 智能映射

网关内置**防呆防傻机制**，用户输入非标准模型 ID 时自动映射到正确的官方模型 ID。

### 6 级匹配策略（按优先级）

| 级别 | 策略 | 示例 |
|------|------|------|
| 1 | 精确匹配（大小写敏感） | `deepseek-ai/deepseek-v4-pro` 直接命中 |
| 2 | 大小写不敏感精确匹配 | `DeepSeek-V4-Pro` → `deepseek-ai/deepseek-v4-pro` |
| 3 | 标准化匹配（去除所有分隔符） | `deepseekv4pro` → `deepseek-ai/deepseek-v4-pro` |
| 4 | 模糊匹配（相似度 ≥ 0.85） | `deepseek-pro-v4` → `deepseek-ai/deepseek-v4-pro` |
| 5 | 子串包含匹配 | `deepseek` → 匹配最短名称的模型 |
| 6 | 智能补全后缀 | `meta/llama-3.1-70b` → `meta/llama-3.1-70b-instruct` |

### 别名表

内置 400+ 别名条目，覆盖所有模型的常见子 ID 变体：

- 大小写变体（`DeepSeek-V4-Pro`）
- 驼峰命名（`DeepSeekV4Pro`）
- 无分隔符（`deepseekv4pro`）
- 空格分隔（`deepseek v4 pro`）
- 缩写（`ds-v4-pro`）
- 品牌名（`deepseek-pro`、`doubao`、`kimi`）

---

## 8. 安全体系

### 8.1 加密体系

| 机制 | 技术 | 用途 |
|------|------|------|
| **上游密钥加密** | Fernet + HKDF-SHA256（salt: `acu-upstream-key-derivation`） | 加密存储的上游 NVIDIA NIM API 密钥 |
| **客户端密钥加密** | Fernet + HKDF-SHA256（salt: `acu-client-key-derivation`） | 加密存储的用户 API Key，与上游密钥派生路径完全隔离 |
| **密码哈希** | bcrypt (12 rounds) | 用户密码与管理员密码存储 |
| **JWT 令牌** | python-jose (HS256) | Platform 用户认证 |
| **Admin Token** | HMAC-SHA256（24h 有效） | Gateway Admin API 认证 |
| **Session** | itsdangerous 签名 Cookie（SessionMiddleware） | SQLAdmin 面板会话 |
| **平台 API 密钥** | Fernet（`PLATFORM_ENCRYPT_KEY`） | Platform 侧用户 API Key 加密 |

### 8.2 防护机制

- **IP 监控**：实时监控异常 IP 访问；登录接口 10 次/分钟、管理接口 60 次/分钟限流
- **软限流排队**：见[第 12 章](#12-并发与限流策略v110)
- **请求校验**：请求体格式强制校验，参数容错与默认值
- **熔断器**：上游连续失败自动熔断，模型级 429/5xx 熔断
- **商用检测**：多维度检测商业用途（监控名单通过 `AQUA_WATCHLIST` 环境变量配置）
- **CORS 白名单**：`CORS_ALLOWED_ORIGINS` 显式域名列表，不使用通配符

---

## 9. 慷慨型网关

`generous_gateway.py` 模块提供独立的多供应商负载均衡：

- **FreeQuotaPool**：管理多个供应商的免费额度
- **GenerousLoadBalancer**：根据 `provider.supported_models` 过滤并选择供应商
- **故障自动转移**：供应商不可用时自动切换

---

## 10. 龙虾文档适配器

`lobster_doc_adapter.py` 支持多格式文档解析并注入 LLM 上下文：

| 格式 | 解析库 | 说明 |
|------|--------|------|
| PDF | pypdf | 文本与表格提取 |
| DOCX | python-docx | Word 文档内容解析 |
| HTML | beautifulsoup4 + lxml | 结构化提取 |
| TXT | 内置 | 纯文本直接读取 |

---

## 11. 管理后台

### Gateway 管理后台

- **Admin API** (`/gw/admin/*`)：密钥管理、客户管理、桶监控、算法可视化面板、时间段对比、商用检测控制、慷慨网关状态、NIM 模型目录、仪表盘、日志、策略、令牌、维护模式、调试
- **SQLAdmin** (`/gw/dbadmin`)：数据库表 CRUD

### Platform 管理后台

- **内置管理**：用户管理、密钥查看、用量统计
- **SQLAdmin** (`/platform/dbadmin`)：数据库表 CRUD

管理员密码通过环境变量注入（见[第 15 章](#15-配置说明)）。

---

## 12. 并发与限流策略（v11.0）

v11.0 起，平台**取消了所有硬性并发限制**，不再存在"老用户/新用户分级并发额度"的机制：

- **无硬性并发上限**：并发控制器（`platform/app/concurrency.py`）的 `try_acquire` 恒返回 True，所有用户统一无限制额度（哨兵值 9999），不拒绝任何请求。
- **软限流排队（保留）**：软限速器（`platform/app/soft_limiter.py`）采用均匀间隔排队模式——通过控制两次响应之间的最小间隔平滑请求速率，**排队等待而非拒绝**，绝不返回 429。
- **429 熔断（保留）**：上游返回 429/403 时由调度器算法 4 执行差异化桶级冷却，由熔断器防止雪崩；登录/管理接口保留固定 IP 限流（10/60 次/分钟）防暴力破解。
- **客户端治理只监测不拦截**：算法 5/6/7（并发/突发/日用量）仅作监控指标与统计展示，高并发客户端会被标记但不被拒绝。

---

## 13. 项目目录结构

```
aqua-platform-open/
├── gateway/                          # API 网关（端口 8000）
│   ├── app/
│   │   ├── main.py                   # FastAPI 应用入口
│   │   ├── public_api.py             # OpenAI 兼容 API 端点
│   │   ├── admin_api.py              # 管理 API 端点
│   │   ├── admin_panel.py            # SQLAdmin 管理面板
│   │   ├── scheduler.py              # 17 算法调度引擎（SurgeScheduler）
│   │   ├── translator.py             # 协议转换编排（实验性，未默认启用）
│   │   ├── transformers/             # 协议转换器（实验性）
│   │   │   ├── anthropic.py          #   Anthropic ↔ OpenAI
│   │   │   └── gemini.py             #   Gemini ↔ OpenAI
│   │   ├── nim_models.py             # NVIDIA NIM 模型目录
│   │   ├── request_validator.py      # 模型 ID 智能映射
│   │   ├── security.py               # 加密与令牌管理
│   │   ├── middleware.py             # 中间件（CORS、限流、维护模式等）
│   │   ├── database.py               # PostgreSQL 同步访问层
│   │   ├── db_async.py               # SQLAlchemy 异步引擎
│   │   ├── db_async_pool.py          # asyncpg 连接池
│   │   ├── models.py                 # SQLAlchemy ORM 模型
│   │   ├── generous_gateway.py       # 慷慨型网关
│   │   ├── lobster_doc_adapter.py    # 龙虾文档适配器
│   │   ├── circuit_breaker.py        # 熔断器
│   │   ├── commercial_detect.py      # 商用检测
│   │   ├── ip_monitor.py             # IP 监控
│   │   ├── behavior.py               # 行为分析
│   │   ├── errors.py / errors_v2.py  # 错误处理
│   │   ├── error_tracker.py          # 错误追踪
│   │   ├── platforms/                # 平台适配器（nvidia/openai）
│   │   ├── routers/                  # 路由模块
│   │   ├── cache/                    # 多级缓存（L1 LRU / L2 TTL，进程内内存）
│   │   └── static/                   # 静态资源
│   └── requirements.txt              # 网关依赖
├── platform/                         # 用户平台（端口 8001）
│   ├── app/
│   │   ├── main.py                   # FastAPI 应用入口
│   │   ├── database.py               # PostgreSQL 访问层
│   │   ├── db_async.py               # asyncpg 异步连接池
│   │   ├── security.py               # 密码哈希/会话/CSRF
│   │   ├── email_service.py          # 邮件服务
│   │   ├── gateway_client.py         # Gateway 客户端
│   │   ├── admin_panel.py            # SQLAdmin 管理面板
│   │   ├── soft_limiter.py           # 软限流器（均匀间隔排队）
│   │   ├── concurrency.py            # 并发控制器（v11.0 无限制语义）
│   │   ├── behavior.py               # 行为分析
│   │   ├── ip_monitor.py             # IP 监控
│   │   ├── models.py                 # SQLAlchemy ORM 模型
│   │   ├── nim_models_compat.py      # NIM 模型兼容层
│   │   ├── routes/                   # 路由模块（auth/public/console/chat/platform_admin）
│   │   └── static/                   # 静态资源（前端页面）
│   └── requirements.txt              # 平台依赖
├── scripts/                          # 运维脚本
│   ├── auto_recovery.sh              # 自动恢复脚本
│   ├── migrate_to_postgresql.py      # SQLite → PostgreSQL 历史数据迁移
│   ├── migrate_data.py               # 数据迁移
│   ├── send_maintenance_notice.py    # 维护通知
│   └── send_manual_email.sh          # 手动邮件发送
├── tests/                            # 测试
│   ├── _app_path.py                  # 同名 app 包的 sys.path 切换辅助
│   ├── test_concurrency.py           # 并发控制器（v11.0 语义）
│   ├── test_errors.py                # 统一异常体系
│   ├── test_gateway_security.py      # Gateway security 纯单元测试
│   └── test_timestamps.py            # 时间戳契约函数
├── docs/                             # 文档
│   └── rules/                        # 规则文档（并发与限流）
├── ARCHITECTURE.md                   # 完整架构文档
├── conftest.py                       # pytest 配置（说明性）
├── pytest.ini                        # pytest 配置
├── .env.example                      # 环境变量模板
├── .gitignore                        # Git 忽略规则
├── LICENSE                           # MIT 开源协议
└── README.md                         # 本文件
```

---

## 14. 快速部署

### 14.1 环境要求

- Python 3.13+
- **PostgreSQL 15+（必需）**——平台仅支持 PostgreSQL，无 SQLite 模式，部署前必须先建好数据库
- NVIDIA NIM API 密钥（从 [build.nvidia.com](https://build.nvidia.com) 获取）
- （可选）SMTP 邮箱账号，用于注册验证码与通知邮件

### 14.2 安装步骤

```bash
# 克隆仓库
git clone https://github.com/your-username/aqua-platform.git
cd aqua-platform

# 创建虚拟环境（通用命名 venv，可按需调整）
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 安装依赖
pip install -r gateway/requirements.txt
pip install -r platform/requirements.txt

# 复制环境变量模板并填写配置
cp .env.example .env
# 编辑 .env，填入你的配置（见下方说明）
```

### 14.3 初始化 PostgreSQL

先在 PostgreSQL 中创建数据库和用户（两个服务各一个库）：

```sql
CREATE USER aqua WITH PASSWORD '你的数据库密码';
CREATE DATABASE aqua_gateway OWNER aqua;
CREATE DATABASE aqua_platform OWNER aqua;
```

并在 `.env` 中配置对应的 `PG_PASSWORD`（Gateway 必填）与 `PG_PLATFORM_PASSWORD` / `PG_GATEWAY_PASSWORD`。表结构在服务首次启动时自动初始化。

### 14.4 启动服务

```bash
# 启动网关（端口 8000）
cd gateway
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 启动平台（端口 8001，另开终端）
cd platform
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### 14.5 关键配置说明

- **管理员密码（双变量）**：优先使用 `ACU_ADMIN_PASSWORD_HASH`（bcrypt 哈希，Gateway Admin API 必需）；未配置哈希时 SQLAdmin 面板回退校验 `ACU_ADMIN_PASSWORD`（明文，恒定时间比较）。生成命令见 `.env.example`。
- **AQUA_PLATFORM_TOKEN**：Platform 与 Gateway **两侧必须配置完全一致的值**，否则平台调用网关管理 API（发放密钥等）将全部返回 401。
- 完整变量清单与分组说明见 [.env.example](.env.example) 与[第 15 章](#15-配置说明)。

### 14.6 安全部署清单

上线前逐项核对：

| 项目 | 配置 | 说明 |
|------|------|------|
| **反代场景信任代理头** | `AQUA_TRUST_PROXY_HEADERS=1` | 经 Nginx/Caddy 等反向代理部署时必须开启，否则网关记录的客户端 IP 全是反代地址，IP 限流与监控失效；服务直连暴露时保持 0 |
| **Cookie 仅 HTTPS** | `SESSION_COOKIE_SECURE=1` | 默认 1；仅在本地无 HTTPS 调试时可临时设 0，生产必须保持 1 |
| **注册开关** | `REGISTRATION_OPEN` | 默认 1 开放注册；如需邀请制/封闭部署，设 0 关闭注册入口 |
| **强密码** | 所有 `*_PASSWORD` / `*_SECRET` / `*_KEY` | 全部使用随机强值（生成命令见 `.env.example`），切勿复用或使用弱密码；管理员密码建议直接用 bcrypt 哈希变量 |
| **限流说明** | 登录 10 次/分钟、管理 60 次/分钟 | 平台内置固定 IP 限流（防暴力破解）；业务 API 无硬性并发限制（v11.0），上游 429 由调度器自动冷却与切换密钥消化；公网部署建议在反代层（如 Nginx `limit_req`）按需追加整体速率限制 |
| **CORS 白名单** | `CORS_ALLOWED_ORIGINS` | 只填实际访问域名，禁止使用 `*` |
| **调试模式关闭** | `AQUA_DEBUG_ERRORS=0` | 生产保持 0，避免泄露内部错误详情 |

---

## 15. 配置说明

完整分组与注释见 [.env.example](.env.example)。摘要：

```ini
# ===== 管理员密码（二选一，HASH 优先）=====
# python3 -c "import bcrypt; print(bcrypt.hashpw('你的密码'.encode(), bcrypt.gensalt(rounds=12)).decode())"
ACU_ADMIN_PASSWORD_HASH=
ACU_ADMIN_PASSWORD=

# ===== 服务间认证与加密 =====
AQUA_PLATFORM_TOKEN=          # 两侧一致
ADMIN_SESSION_SECRET=         # SQLAdmin 面板 Session 密钥（共用）
PLATFORM_ADMIN_SESSION_SECRET=  # Platform 管理会话令牌密钥
JWT_SECRET_KEY=               # Platform 用户 JWT（HS256）
PLATFORM_ENCRYPT_KEY=         # Fernet 密钥（python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"）

# ===== PostgreSQL =====
PG_HOST=localhost             # Gateway 库（PG_PASSWORD 必填）
PG_PORT=5432
PG_DB=aqua_gateway
PG_USER=aqua
PG_PASSWORD=

PG_PLATFORM_HOST=localhost    # Platform 库
PG_PLATFORM_PORT=5432
PG_PLATFORM_DB=aqua_platform
PG_PLATFORM_USER=aqua
PG_PLATFORM_PASSWORD=

PG_GATEWAY_HOST=localhost     # Platform 跨库读 Gateway 库（控制台用量统计）
PG_GATEWAY_PORT=5432
PG_GATEWAY_DB=aqua_gateway
PG_GATEWAY_USER=aqua
PG_GATEWAY_PASSWORD=

# ===== 行为开关 =====
GW_BASE_URL=http://127.0.0.1:8000
SESSION_COOKIE_SECURE=1
REGISTRATION_OPEN=1
AQUA_TRUST_PROXY_HEADERS=0
AQUA_WATCHLIST=
AQUA_DEBUG_ERRORS=0

# ===== CORS / SMTP =====
CORS_ALLOWED_ORIGINS=http://localhost:8001,http://localhost:8000
SMTP_HOST=
SMTP_PORT=465
SMTP_USER=
SMTP_PASSWORD=
```

### 密钥生成命令

```bash
# Fernet 加密密钥
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# bcrypt 密码哈希
python3 -c "import bcrypt; print(bcrypt.hashpw('你的密码'.encode(), bcrypt.gensalt(rounds=12)).decode())"

# 随机令牌（用于 AQUA_PLATFORM_TOKEN、JWT_SECRET_KEY、各 SESSION_SECRET 等）
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 16. API 使用指南

### 16.1 支持的端点

```
POST /v1/chat/completions     # 对话补全（支持流式）
POST /v1/embeddings           # 文本嵌入
GET  /v1/models               # 模型列表
```

### 16.2 对话补全示例

```bash
curl https://你的域名/v1/chat/completions \
  -H "Authorization: Bearer 你的API密钥" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/deepseek-v4-pro",
    "messages": [{"role": "user", "content": "你好！"}],
    "stream": false
  }'
```

### 16.3 模型 ID 智能映射

网关支持模糊模型 ID 匹配，以下写法都会自动映射到 `deepseek-ai/deepseek-v4-pro`：

- `DeepSeek-V4-Pro`（大小写变体）
- `deepseekv4pro`（无分隔符）
- `deepseek v4 pro`（空格分隔）
- `ds-v4-pro`（缩写）
- `deepseek-pro`（品牌名）

### 16.4 在 IDE 中使用

所有 IDE 均以 **OpenAI Compatible** 模式接入（主链路直通 OpenAI 协议）：

| IDE | 配置方式 |
|-----|---------|
| **Cursor** | Settings → Models → OpenAI API Base URL: `https://你的域名/v1` |
| **Claude Code** | 配置文件中设置 `apiBase: "https://你的域名/v1"` |
| **Cline** | API Provider 选 OpenAI Compatible，Base URL 填 `https://你的域名/v1` |
| **Continue** | `baseURL: "https://你的域名/v1"` |
| **Cherry Studio** | 添加自定义提供商，API 地址填 `https://你的域名/v1` |

---

## 17. Nginx 反向代理部署

```nginx
# 网关 API
server {
    listen 443 ssl http2;
    server_name api.你的域名.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式响应支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}

# 用户平台
server {
    listen 443 ssl http2;
    server_name 你的域名.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> 反代部署时记得在 `.env` 中设置 `AQUA_TRUST_PROXY_HEADERS=1`，否则网关取不到真实客户端 IP。

---

## 18. Systemd 服务部署

创建 `/etc/systemd/system/aqua-gateway.service`：

```ini
[Unit]
Description=AQUA Gateway
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/aqua-platform/gateway
EnvironmentFile=/opt/aqua-platform/.env
ExecStart=/opt/aqua-platform/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

创建 `/etc/systemd/system/aqua-platform.service`：

```ini
[Unit]
Description=AQUA Platform
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/aqua-platform/platform
EnvironmentFile=/opt/aqua-platform/.env
ExecStart=/opt/aqua-platform/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level warning
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
# 启用并启动
systemctl daemon-reload
systemctl enable --now aqua-gateway
systemctl enable --now aqua-platform

# 查看状态
systemctl status aqua-gateway
systemctl status aqua-platform
```

---

## 19. 数据库说明

### 19.1 数据库要求

**仅支持 PostgreSQL 15+**。代码全部使用 PostgreSQL 语法（psycopg2 同步连接池 + asyncpg 异步连接池），没有 SQLite 运行模式。`scripts/migrate_to_postgresql.py` 仅用于从历史 SQLite 数据文件迁移存量数据，不是运行时依赖。

| 库 | 默认名 | 使用方 |
|------|--------|--------|
| 网关库 | `aqua_gateway` | Gateway 读写；Platform 控制台跨库只读 |
| 平台库 | `aqua_platform` | Platform 读写 |

### 19.2 数据库表结构

**网关数据库 (aqua_gateway)**：
- `upstream_keys`：上游 NVIDIA NIM 密钥（加密存储）
- `client_keys`：客户端 API 密钥（加密存储）
- `request_logs`：请求日志
- `bucket_snapshots`：桶状态快照
- `commercial_detections`：商用检测记录

**平台数据库 (aqua_platform)**：
- `users`：用户表（邮箱、密码哈希）
- `user_api_keys`：用户 API 密钥（加密存储）
- `verification_codes` / `email_verification`：邮箱验证码
- `feedbacks`：用户反馈
- `chat_history`：聊天历史

### 19.3 数据库迁移（历史数据）

从历史 SQLite 数据文件迁移到 PostgreSQL：

```bash
python3 scripts/migrate_to_postgresql.py
```

### 19.4 数据库表源码

数据库表结构定义在以下文件中，开源版本仅包含表结构定义（源表），不含任何数据：

- 网关：`gateway/app/models.py`
- 平台：`platform/app/models.py`

---

## 20. 开源协议与贡献

### 开源协议

本项目基于 **MIT 协议**开源，详见 [LICENSE](LICENSE) 文件。

### 贡献指南

欢迎提交 Issue 和 Pull Request。贡献时请确保：

1. 不提交任何真实密钥、密码、用户数据
2. 不硬编码敏感信息到源代码中
3. 所有配置通过 `.env` 环境变量注入
4. 遵循现有代码风格和架构分层

### 致谢

- [NVIDIA NIM](https://build.nvidia.com) — 上游模型 API 提供商
- [FastAPI](https://fastapi.tiangolo.com/) — Web 框架
- [SQLAlchemy](https://sqlalchemy.org/) — ORM 框架
- [PostgreSQL](https://www.postgresql.org/) — 数据库

---

> **安全提示**：部署前请务必修改 `.env` 中的所有密钥为随机生成的强密码，切勿使用默认值或空值。所有包含密钥/密码的环境变量切勿硬编码到代码中。
