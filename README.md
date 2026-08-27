# AQUA AI Platform — 开源 AI 网关平台

> **版本**: v10.0
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
5. [16 算法互锁调度体系](#5-16-算法互锁调度体系)
6. [多协议转换机制](#6-多协议转换机制)
7. [模型 ID 智能映射](#7-模型-id-智能映射)
8. [安全体系](#8-安全体系)
9. [慷慨型网关](#9-慷慨型网关)
10. [龙虾文档适配器](#10-龙虾文档适配器)
11. [管理后台](#11-管理后台)
12. [项目目录结构](#12-项目目录结构)
13. [快速部署](#13-快速部署)
14. [配置说明](#14-配置说明)
15. [API 使用指南](#15-api-使用指南)
16. [Nginx 反向代理部署](#16-nginx-反向代理部署)
17. [Systemd 服务部署](#17-systemd-服务部署)
18. [ALTCHA 人机验证组件](#18-altcha-人机验证组件)
19. [数据库说明](#19-数据库说明)
20. [开源协议与贡献](#20-开源协议与贡献)

---

## 1. 核心特性

| 特性 | 说明 |
|------|------|
| **密钥池化** | 将多个 NVIDIA NIM 上游 API 密钥整合为统一入口，提升利用率与可用性 |
| **统一 API** | 提供 OpenAI 兼容的 `/v1/chat/completions`、`/v1/embeddings`、`/v1/models` 端点 |
| **16 算法互锁调度** | 16 个协同算法从 RPM、健康度、冷却、预热等维度计算最优密钥选择 |
| **多协议转换** | 自动检测 IDE 类型（Cursor、Claude Code、Cline、Continue、Cherry Studio），在 Anthropic/Gemini/Ollama/OpenAI 协议间互转 |
| **模型 ID 智能映射** | 400+ 别名条目，6 级匹配策略，用户输入非标准 ID 时自动映射到正确模型 |
| **故障自愈** | 429/5xx 熔断、自适应冷却、冷密钥渐进式预热、自动故障转移 |
| **安全体系** | Fernet 对称加密、JWT 认证、bcrypt 密码哈希、ALTCHA PoW 验证码、IP 监控、软限流 |
| **管理后台** | SQLAdmin 自动生成数据库 CRUD 后台，Admin API 提供密钥/客户/桶监控管理 |
| **文档解析** | PDF/DOCX/HTML/TXT 多格式解析并注入 LLM 上下文（龙虾文档适配器） |
| **多级缓存** | 内存缓存 + Redis 缓存，用于 API Key 缓存、限流计数器、TPM/RPM 追踪 |

---

## 2. 技术栈

### 2.1 后端

| 类别 | 技术 | 说明 |
|------|------|------|
| **运行时** | Python 3.13+（目标 3.14） | 充分利用最新语言特性与性能提升 |
| **Web 框架** | FastAPI >= 0.115.0 | 全异步框架，原生 OpenAPI 文档 |
| **ORM** | SQLAlchemy 2.0 async | 异步 ORM，支持 aiosqlite / asyncpg 驱动 |
| **数据库** | PostgreSQL 15+ 或 SQLite (WAL) | 生产环境推荐 PostgreSQL，开发可用 SQLite |
| **管理后台** | SQLAdmin >= 0.20.0 | 自动生成数据库 CRUD 管理界面 |
| **数据校验** | Pydantic V2 | 请求/响应数据校验与序列化 |
| **HTTP 客户端** | httpx >= 0.28.0 | 异步连接池，用于上游 API 调用 |
| **加密** | cryptography >= 43.0.0 | Fernet 对称加密 + HKDF 密钥派生 |
| **重试** | tenacity >= 8.2.0 | 指数退避重试策略 |
| **JWT** | python-jose >= 3.3.0 | JWT 令牌签发与验证（HS256） |
| **密码哈希** | bcrypt >= 4.0.0 | 用户密码哈希，12 rounds |
| **文档解析** | pypdf / python-docx / beautifulsoup4 / lxml | PDF/DOCX/HTML 多格式解析 |
| **模板** | Jinja2 >= 3.1.0 | HTML 模板渲染 |

### 2.2 前端组件

| 类别 | 技术 | 说明 |
|------|------|------|
| **框架** | Svelte 5 + Vite 7 | ALTCHA 验证码组件 |
| **样式** | Tailwind CSS 4 + daisyUI 5 | 组件化样式 |
| **语言** | TypeScript | 类型安全 |
| **加密** | hash-wasm | PoW（工作量证明）验证 |

---

## 3. 系统架构

### 3.1 整体架构图

```
                           ┌──────────────────────────────────┐
                           │     AQUA AI Platform v10.0       │
                           └──────────────────────────────────┘

 ┌──────────────┐                                                ┌──────────────────────┐
 │  用户/浏览器   │─────── HTTP ────────►  Platform (:8001)       │     IDE 工具          │
 └──────────────┘                       ┌────────────────┐       │                      │
                                        │  用户注册/登录   │       │  Cursor / Claude Code│
                                        │  密钥管理       │       │  Cline / Continue    │
                                        │  用量统计       │       │  Cherry Studio       │
                                        │  SQLAdmin      │       │  通用 IDE 插件        │
                                        └───────┬────────┘       └──────────┬───────────┘
                                                │                     多协议请求   │
                                    AQUA_PLATFORM_TOKEN              (Anthropic/ │
                                    (内部服务间认证)                   Gemini/    │
                                                │                    OpenAI)    │
                                                ▼                               │
                                        ┌────────────────┐                         │
                                        │  Gateway (:8000)│ ◄── 多协议 API ────────┘
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │多协议转换器  │ │  IDE 智能协议检测
                                        │ │Anthropic↔  │ │  多轮对话上下文保持
                                        │ │OpenAI      │ │
                                        │ │Gemini↔     │ │
                                        │ │OpenAI      │ │
                                        │ │Ollama↔     │ │
                                        │ │OpenAI      │ │
                                        │ └────────────┘ │
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │16算法调度器  │ │  score = base×A8×A9×
                                        │ │            │ │    A11×A13×A15×A16
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
                                    ┌───────────┼───────────┐
                                    ▼           ▼           ▼
                              ┌──────────┐ ┌──────────┐ ┌──────────┐
                              │NVIDIA NIM│ │NVIDIA NIM│ │  多供应商  │
                              │ 上游密钥1 │ │ 上游密钥N │ │ 免费额度池 │
                              └──────────┘ └──────────┘ └──────────┘
                                    │           │           │
                                    └───────────┼───────────┘
                                                ▼
                              integrate.api.nvidia.com/v1
```

### 3.2 请求流转

```
客户端/IDE 请求 (任意协议)
    │
    ▼
Gateway 入口 → 认证 → 限流 → 路由
    │
    ▼
多协议检测与转换 → 统一为 OpenAI 格式
    │
    ▼
16 算法互锁调度 → 选出最优桶+密钥
    │
    ▼
上游请求转发 → httpx 异步连接池 → tenacity 重试
    │
    ▼
响应处理与回转 → 流式/非流式 → 协议格式响应
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

## 5. 16 算法互锁调度体系

Gateway 的调度引擎由 **16 个算法**组成，形成一个互锁协同的调度体系。

**核心评分公式**：

```
score = base_weight × A8 × A9 × A11 × A13 × A15 × A16
```

| 算法 | 名称 | 类别 | 触发时机 | 说明 |
|------|------|------|----------|------|
| 1 | RPM 滑动窗口计数器 | 状态追踪 | 每次请求 | 60秒滑动窗口统计每个桶的 RPM |
| 2 | 软繁忙检测器 | 状态追踪 | 密钥选择阶段 | RPM 超阈值时标记为软繁忙并跳过 |
| 3 | 自适应阈值调节器 | 动态调参 | 每30秒后台运行 | 统一阈值 38 RPM，确保所有模型容量一致 |
| 4 | 自适应冷却时长计算器 | 故障保护 | 上游返回 429/403/超时 | 差异化冷却：429→5s，403→60s起+退避，超时→15s |
| 5 | 桶级隔离管理器 | 故障保护 | 连续失败超阈值 | 桶级隔离，阻止该桶参与选择 |
| 6 | 全局故障密度监控器 | 全局保护 | 每次失败 | 全局故障率超阈值时触发降级 |
| 7 | 模型级熔断器 | 全局保护 | 模型级 429/5xx 累积 | 模型级熔断，返回 503 |
| 8 | 基础权重计算器 | 权重计算 | 每次选择 | 基于管理员配置和桶状态计算基础权重 |
| 9 | P95 延迟追踪器 | 权重计算 | 每次请求 | 追踪桶的 P95 延迟，影响权重 |
| 10 | 健康度评估器 | 权重计算 | 每次请求 | 基于成功/失败率计算健康度 |
| 11 | 动态阈值调节器 | 权重计算 | 每次选择 | 根据全局负载动态调节选择阈值 |
| 12 | 优先级排序器 | 选择 | 每次选择 | 按综合得分排序候选桶 |
| 13 | 冷密钥渐进式预热器 | 恢复 | 冷却到期后 | 健康评估渐进恢复（0.3→0.6→0.9→1.0） |
| 14 | 请求级负载均衡 | 选择 | 每次选择 | 在同分桶间轮询负载均衡 |
| 15 | 全局并发控制器 | 并发控制 | 每次请求 | 全局并发数限制 |
| 16 | 自愈引擎 | 恢复 | 定时后台 | 自动检测并恢复异常桶 |

---

## 6. 多协议转换机制

Gateway 支持自动检测客户端协议并转换为 OpenAI 统一格式：

| 协议 | 检测方式 | 转换方向 |
|------|----------|----------|
| **OpenAI** | `/v1/chat/completions` 路径 | 原样透传 |
| **Anthropic** | `/v1/messages` 路径 + header 检测 | Anthropic ↔ OpenAI |
| **Gemini** | `/v1beta/models/*:generateContent` 路径 | Gemini ↔ OpenAI |
| **Ollama** | `/api/chat` 路径 | Ollama ↔ OpenAI |

### IDE 智能检测

`detect_protocol_with_ide()` 函数通过请求特征自动识别 IDE 类型：

- **Cursor**：`x-cursor-version` header
- **Claude Code**：User-Agent 特征
- **Cline**：特定 header 组合
- **Continue**：User-Agent 特征
- **Cherry Studio**：特定 header 组合

### 多轮对话上下文

`ConversationContext` 类维护多轮对话的上下文，确保在协议转换过程中不丢失对话历史。

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

内置 400+ 别名条目，覆盖所有模型的社会工程学子 ID 变体：

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
| **上游密钥加密** | Fernet + HKDF-SHA256 | 加密存储的上游 NVIDIA NIM API 密钥 |
| **客户端密钥加密** | Fernet + HKDF-SHA256 | 加密存储的用户 API Key |
| **密码哈希** | bcrypt (12 rounds) | 用户密码存储 |
| **JWT 令牌** | python-jose (HS256) | 管理后台认证 |
| **Session** | 签名 Cookie | 平台用户会话 |

### 8.2 防护机制

- **ALTCHA PoW 验证码**：自托管的人机验证，隐私优先，无追踪
- **IP 监控**：实时监控异常 IP 访问
- **软限流**：基于令牌桶的请求限流
- **请求校验**：请求体格式强制校验，参数容错与默认值
- **熔断器**：模型级 429/5xx 熔断
- **商用检测**：6 维度检测商业用途

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

---

## 12. 项目目录结构

```
aqua-platform-open/
├── gateway/                          # API 网关（端口 8000）
│   ├── app/
│   │   ├── main.py                   # FastAPI 应用入口
│   │   ├── public_api.py             # OpenAI 兼容 API 端点
│   │   ├── admin_api.py              # 管理 API 端点
│   │   ├── admin_panel.py            # SQLAdmin 管理面板
│   │   ├── scheduler.py              # 16 算法调度引擎
│   │   ├── translator.py             # 多协议转换器
│   │   ├── nim_models.py             # NVIDIA NIM 模型目录
│   │   ├── request_validator.py      # 模型 ID 智能映射
│   │   ├── security.py               # 加密与密钥管理
│   │   ├── middleware.py             # 中间件（CORS、限流等）
│   │   ├── database.py               # 数据库初始化
│   │   ├── db_async.py               # 异步数据库连接
│   │   ├── db_asyncpg.py             # asyncpg 连接池
│   │   ├── models.py                 # SQLAlchemy ORM 模型
│   │   ├── generous_gateway.py       # 慷慨型网关
│   │   ├── lobster_doc_adapter.py    # 龙虾文档适配器
│   │   ├── circuit_breaker.py        # 熔断器
│   │   ├── commercial_detect.py      # 商用检测
│   │   ├── ip_monitor.py             # IP 监控
│   │   ├── behavior.py               # 行为分析
│   │   ├── errors.py                 # 错误处理
│   │   ├── error_tracker.py          # 错误追踪
│   │   ├── scheduler.py              # 调度器
│   │   ├── request_validator.py      # 请求校验
│   │   ├── transformers/             # 协议转换器
│   │   │   ├── anthropic.py          #   Anthropic 转换
│   │   │   └── gemini.py             #   Gemini 转换
│   │   ├── platforms/                # 平台适配器
│   │   │   ├── base.py               #   适配器基类
│   │   │   ├── nvidia.py             #   NVIDIA NIM 适配
│   │   │   └── openai.py             #   OpenAI 适配
│   │   ├── routers/                  # 路由模块
│   │   │   ├── token_router.py       #   令牌路由
│   │   │   └── health.py             #   健康检查
│   │   ├── cache/                    # 缓存模块
│   │   │   └── multilevel.py         #   多级缓存
│   │   └── static/                   # 静态资源
│   └── requirements.txt              # 网关依赖
├── platform/                         # 用户平台（端口 8001）
│   ├── app/
│   │   ├── main.py                   # FastAPI 应用入口
│   │   ├── database.py               # 数据库初始化
│   │   ├── db_async.py               # 异步数据库连接
│   │   ├── security.py               # 安全与加密
│   │   ├── email_service.py          # 邮件服务
│   │   ├── gateway_client.py         # Gateway 客户端
│   │   ├── admin_panel.py            # SQLAdmin 管理面板
│   │   ├── soft_limiter.py           # 软限流器
│   │   ├── concurrency.py            # 并发控制
│   │   ├── behavior.py               # 行为分析
│   │   ├── ip_monitor.py             # IP 监控
│   │   ├── models.py                 # SQLAlchemy ORM 模型
│   │   ├── nim_models_compat.py      # NIM 模型兼容层
│   │   ├── routes/                   # 路由模块
│   │   │   ├── auth.py               #   认证路由
│   │   │   ├── public.py             #   公开页面
│   │   │   ├── console.py            #   用户控制台
│   │   │   └── platform_admin.py     #   平台管理
│   │   └── static/                   # 静态资源（前端页面）
│   │       ├── index.html            #   主页面
│   │       ├── admin.html            #   管理页面
│   │       ├── platform.js           #   前端逻辑
│   │       └── platform.css          #   样式
│   └── requirements.txt              # 平台依赖
├── altcha-widget/                    # ALTCHA PoW 验证码组件
│   ├── src/                          # 源码
│   ├── package.json                  # Node.js 依赖
│   └── ...
├── scripts/                          # 运维脚本
│   ├── auto_recovery.sh              # 自动恢复脚本
│   ├── migrate_to_postgresql.py      # SQLite → PostgreSQL 迁移
│   ├── migrate_data.py               # 数据迁移
│   ├── send_maintenance_notice.py    # 维护通知
│   ├── send_manual_email.sh          # 手动邮件发送
│   └── track_upstream.sh             # 上游跟踪
├── tests/                            # 测试
│   ├── test_concurrency.py           # 并发测试
│   └── test_errors.py                # 错误测试
├── docs/                             # 文档
│   └── rules/                        # 规则文档
├── ARCHITECTURE.md                   # 完整架构文档（1485行）
├── conftest.py                       # pytest 配置
├── .env.example                      # 环境变量模板
├── .gitignore                        # Git 忽略规则
├── LICENSE                           # MIT 开源协议
└── README.md                         # 本文件
```

---

## 13. 快速部署

### 13.1 环境要求

- Python 3.13+
- PostgreSQL 15+（生产环境推荐）或 SQLite（开发环境）
- Node.js 18+（仅 ALTCHA 组件构建需要）
- NVIDIA NIM API 密钥（从 [build.nvidia.com](https://build.nvidia.com) 获取）

### 13.2 安装步骤

```bash
# 克隆仓库
git clone https://github.com/your-username/aqua-platform.git
cd aqua-platform

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r gateway/requirements.txt
pip install -r platform/requirements.txt

# 复制环境变量模板
cp .env.example .env
# 编辑 .env，填入你的配置
```

### 13.3 启动服务

```bash
# 启动网关（端口 8000）
cd gateway
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 启动平台（端口 8001）
cd platform
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

---

## 14. 配置说明

编辑 `.env` 文件，填入以下配置：

```ini
# ===== 管理员密码 =====
# 生成 bcrypt 哈希：
# python3 -c "import bcrypt; print(bcrypt.hashpw('你的密码'.encode(), bcrypt.gensalt(rounds=12)).decode())"
ACU_ADMIN_PASSWORD_HASH=

# ===== 加密密钥 =====
# 生成 Fernet 密钥：
# python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
PLATFORM_ENCRYPT_KEY=

# ===== 平台间认证令牌 =====
# 随机生成一个长字符串
AQUA_PLATFORM_TOKEN=

# ===== CORS 允许的域名 =====
# 替换为你的域名
CORS_ALLOWED_ORIGINS=http://localhost:8001,http://localhost:8000

# ===== JWT 签名密钥 =====
JWT_SECRET_KEY=

# ===== 管理后台 Session 密钥 =====
ADMIN_SESSION_SECRET=

# ===== 数据库密码 =====
PG_PASSWORD=

# ===== SMTP 邮件服务 =====
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=
SMTP_PASSWORD=

# ===== PostgreSQL 数据库连接 =====
PG_GATEWAY_HOST=localhost
PG_GATEWAY_PORT=5432
PG_GATEWAY_DB=aqua_gateway
PG_GATEWAY_USER=aqua
PG_GATEWAY_PASSWORD=

PG_PLATFORM_HOST=localhost
PG_PLATFORM_PORT=5432
PG_PLATFORM_DB=aqua_platform
PG_PLATFORM_USER=aqua
PG_PLATFORM_PASSWORD=

# ===== ALTCHA 人机验证 HMAC 密钥 =====
ALTCHA_HMAC_KEY=
```

### 密钥生成命令

```bash
# Fernet 加密密钥
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# bcrypt 密码哈希
python3 -c "import bcrypt; print(bcrypt.hashpw('你的密码'.encode(), bcrypt.gensalt(rounds=12)).decode())"

# 随机令牌（用于 JWT_SECRET_KEY、ADMIN_SESSION_SECRET 等）
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# ALTCHA HMAC 密钥
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 15. API 使用指南

### 15.1 支持的端点

```
POST /v1/chat/completions     # 对话补全（支持流式）
POST /v1/embeddings           # 文本嵌入
GET  /v1/models               # 模型列表
```

### 15.2 对话补全示例

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

### 15.3 模型 ID 智能映射

网关支持模糊模型 ID 匹配，以下写法都会自动映射到 `deepseek-ai/deepseek-v4-pro`：

- `DeepSeek-V4-Pro`（大小写变体）
- `deepseekv4pro`（无分隔符）
- `deepseek v4 pro`（空格分隔）
- `ds-v4-pro`（缩写）
- `deepseek-pro`（品牌名）

### 15.4 在 IDE 中使用

| IDE | 配置方式 |
|-----|---------|
| **Cursor** | Settings → Models → OpenAI API Base URL: `https://你的域名/v1` |
| **Claude Code** | 配置文件中设置 `apiBase: "https://你的域名/v1"` |
| **Cline** | API Provider 选 OpenAI Compatible，Base URL 填 `https://你的域名/v1` |
| **Continue** | `baseURL: "https://你的域名/v1"` |
| **Cherry Studio** | 添加自定义提供商，API 地址填 `https://你的域名/v1` |

---

## 16. Nginx 反向代理部署

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

---

## 17. Systemd 服务部署

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

## 18. ALTCHA 人机验证组件

ALTCHA 是一个隐私优先、无追踪的 PoW（工作量证明）验证码组件。

```bash
cd altcha-widget
npm install
npm run build
```

构建产物会输出到 `dist/` 目录，由 Platform 服务静态托管。

配置 HMAC 密钥（在 `.env` 中设置）：

```ini
ALTCHA_HMAC_KEY=你的HMAC密钥
```

---

## 19. 数据库说明

### 19.1 数据库选择

| 模式 | 适用场景 | 配置 |
|------|----------|------|
| **SQLite (WAL)** | 开发环境、小规模部署 | 默认，无需额外配置 |
| **PostgreSQL** | 生产环境、高并发 | 在 `.env` 中配置 PG 连接参数 |

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
- `verification_codes`：邮箱验证码
- `feedbacks`：用户反馈
- `chat_history`：聊天历史

### 19.3 数据库迁移

从 SQLite 迁移到 PostgreSQL：

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
- [ALTCHA](https://altcha.org) — 隐私优先验证码方案
- [SQLAlchemy](https://sqlalchemy.org/) — ORM 框架
- [Svelte](https://svelte.dev/) — 前端框架

---

> **安全提示**：部署前请务必修改 `.env` 中的所有密钥为随机生成的强密码，切勿使用默认值或空值。所有包含密钥/密码的环境变量切勿硬编码到代码中。
