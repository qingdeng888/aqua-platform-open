# AQUA Gateway v12.0 — 项目架构文档

> **版本**: v12.0（纯中转网关形态，单服务）
> **最后更新**: 2026-08-29
> **语言**: 中文（简体）

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈](#2-技术栈)
3. [系统架构图](#3-系统架构图)
4. [17算法互锁调度体系](#4-17算法互锁调度体系)
5. [多协议转换机制](#5-多协议转换机制)
6. [NVIDIA NIM集成](#6-nvidia-nim集成)
7. [慷慨型网关](#7-慷慨型网关)
8. [龙虾文档适配](#8-龙虾文档适配)
9. [安全体系](#9-安全体系)
10. [管理后台](#10-管理后台)
11. [部署运维指南](#11-部署运维指南)
12. [代理池与出网通道](#12-代理池与出网通道)

---

## 1. 项目概述

### 1.1 定位

**AQUA Gateway v12.0** 是一个**开源 AI 中转网关**，其核心使命是：

> 将多个 NVIDIA NIM 上游密钥**池化**，对外提供**统一的 OpenAI 兼容 API**。

网关通过密钥池化技术，将分散的上游 API 密钥整合为一个高可用的统一入口，降低接入门槛，提升密钥利用率，并实现智能负载均衡与故障自愈。

### 1.2 单服务架构

v12.0 起项目收敛为**单服务**形态（原 `platform/` 用户平台模块已删除）：

| 服务 | 职责 | 默认端口 | 说明 |
|------|------|----------|------|
| **Gateway** | API 网关 | `8000` | 面向第三方客户端的 OpenAI 兼容 API 入口，负责认证、调度、转发、限流、日志 |

网关自带零构建管理控制台（`/admin`）与 SQLAdmin 数据库面板（`/gw/dbadmin`），二者共用同一管理员密码。**不存在服务间令牌**：下游密钥由管理员在控制台签发。

### 1.3 核心使命分解

```
┌──────────────────────────────────────────────────────────────┐
│                        核心使命                               │
├──────────────┬──────────────┬────────────────────────────────┤
│   密钥池化    │   统一API    │         智能调度               │
│  N个上游密钥  │  OpenAI兼容  │     17算法互锁                 │
│  → 1个入口    │  多协议转换   │  故障自愈 + 负载均衡            │
│  → M个下游key │  IDE协议适配  │  慷慨型网关 + 龙虾文档适配       │
└──────────────┴──────────────┴────────────────────────────────┘
```

---

## 2. 技术栈

### 2.1 运行时与框架

| 类别 | 技术 | 版本要求 | 说明 |
|------|------|----------|------|
| **Runtime** | Python | 3.13+（目标 3.14） | 充分利用最新语言特性与性能提升 |
| **Web Framework** | FastAPI | >= 0.115.0 | 全异步框架，原生 OpenAPI 文档 |
| **Database** | PostgreSQL 15+ | **必需** | 唯一支持的数据库，无 SQLite 运行模式 |
| **同步驱动** | psycopg2-binary | >= 2.9.9 | ThreadedConnectionPool 同步连接池 |
| **异步驱动** | asyncpg | >= 0.30.0 | 异步 SQL 访问（SQLAlchemy async engine） |
| **ORM** | SQLAlchemy 2.0 async | psycopg2/asyncpg 驱动 | 模型定义与异步查询 |
| **Admin** | SQLAdmin | >= 0.20.0 | 数据库管理后台，自动生成 CRUD |
| **Validation** | Pydantic V2 | model_config = ConfigDict | 数据校验与序列化 |
| **HTTP Client** | httpx[socks] | >= 0.28.0 | 异步连接池，替代 requests；`[socks]` 提供代理池 SOCKS5 支持 |
| **Encryption** | cryptography | >= 43.0.0 | Fernet 对称加密 + HKDF 密钥派生 |
| **Retry** | tenacity | >= 8.2.0 | 指数退避重试策略，提升请求可靠性 |
| **Session 签名** | itsdangerous | >= 2.1.0 | SQLAdmin 面板 Session 中间件 |
| **PDF 解析** | pypdf | >= 4.0.0 | PDF 文档文本与表格提取 |
| **DOCX 解析** | python-docx | >= 1.1.0 | Word 文档内容解析 |
| **HTML 解析** | beautifulsoup4 | >= 4.12.0 | HTML 文档结构化提取 |
| **HTML 引擎** | lxml | >= 5.0.0 | 高性能 XML/HTML 解析后端 |

> 权威依赖清单以 `gateway/requirements.txt` 为准。v12.0 起网关侧**不使用 JWT**（原 `python-jose` 为 platform 用户登录所需，已随模块删除）；管理 Token 为自实现的 HMAC-SHA256 签名（`gateway/app/security.py`）。

### 2.2 技术栈关系图

```
┌────────────────────────────────────────────────────────────────┐
│                      Application Layer                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────┐ │
│  │ FastAPI   │  │ SQLAdmin │  │ 零构建前端 │  │ Lobster 文档  │ │
│  │ (路由)    │  │ (管理后台)│  │ (原生 JS) │  │ 适配器        │ │
│  └────┬─────┘  └────┬─────┘  └───────────┘  └──────┬───────┘ │
│       │              │                              │         │
│  ┌────┴──────────────┴──────────────────────────────┴──────┐  │
│  │              Pydantic V2 (校验/序列化)                    │  │
│  └────┬────────────────────────────────────────────────────┘  │
│       │                                                        │
│  ┌────┴─────────────┐  ┌─────────────────┐  ┌─────────────┐  │
│  │ SQLAlchemy 2.0   │  │     httpx       │  │  tenacity   │  │
│  │ async (asyncpg)  │  │  (异步HTTP池)   │  │ (指数退避)   │  │
│  └────┬─────────────┘  └────────┬────────┘  └─────────────┘  │
│       │                       │                                │
│  ┌────┴───────────────────────┴──────────────────────────┐    │
│  │    cryptography (加密) + hmac / hashlib               │    │
│  │    Fernet + HKDF-SHA256   HMAC-SHA256 / SHA-256       │    │
│  └────┬──────────────────────────────────────────────────┘    │
│       │                                                        │
│  ┌────┴────────────────────────────────────────────────────┐  │
│  │  pypdf + python-docx + beautifulsoup4 + lxml (文档解析) │  │
│  └────┬────────────────────────────────────────────────────┘  │
│       │                                                        │
│  ┌────┴────────────────────────────────────────────────────┐  │
│  │    PostgreSQL 15+ (psycopg2 + asyncpg) — 数据持久层      │  │
│  └─────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. 系统架构图

### 3.1 整体架构

```
                           ┌──────────────────────────────────┐
                           │      AQUA Gateway v12.0          │
                           └──────────────────────────────────┘

 ┌──────────────┐                                    ┌──────────────────────┐
 │  管理员/浏览器  │                                    │     IDE 工具          │
 └──────┬───────┘                                    │                      │
        │ 管理员密码                                   │  Cursor / Claude Code│
        │ (/admin 控制台)                              │  Cline / Continue    │
        │                                             │  Cherry Studio       │
        │                                             │  通用 IDE 插件        │
        │                                             └──────────┬───────────┘
        │                                                        │
        │                                            OpenAI 协议  │
        │                                            (主链路直通)  │
        ▼                                                        │
                                        ┌────────────────┐       │
                                        │ Gateway (:8000)│ ◄── 多协议 API ────┘
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │下游密钥认证  │ │  ← Bearer sk-xxx
                                        │ │SHA-256查库 │ │    + L1/L2 缓存
                                        │ └────────────┘ │
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │协议转换器    │ │  ← 实验性（Anthropic/Gemini）
                                        │ │(实验性,未   │ │    当前主链路直通 OpenAI
                                        │ │ 默认启用)   │ │    协议，转换器未接入
                                        │ └────────────┘ │
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │17算法调度器  │ │  ← 评分仅作健康性过滤，
                                        │ │            │ │    最终选择由A17公平调度
                                        │ └────────────┘ │
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │慷慨型网关    │ │  ← FreeQuotaPool
                                        │ │Generous    │ │    GenerousLoadBalancer
                                        │ │Gateway     │ │    故障自动转移
                                        │ └────────────┘ │
                                        │                │
                                        │ ┌────────────┐ │
                                        │ │龙虾文档适配器│ │  ← PDF/DOCX/HTML/TXT
                                        │ │Lobster Doc │ │    多格式解析
                                        │ │Adapter     │ │    LLM上下文注入
                                        │ └────────────┘ │
                                        │                │
                                        │ 控制台 /admin   │
                                        │ Admin API      │
                                        │ (/gw/admin/*) │
                                        │ SQLAdmin       │
                                        │ (/gw/dbadmin) │
                                        └───────┬────────┘
                                                │
                                    ┌───────────┼───────────┐
                                    │           │           │
                                    ▼           ▼           ▼
                              ┌──────────┐ ┌──────────┐ ┌──────────┐
                              │NVIDIA NIM│ │NVIDIA NIM│ │  多供应商  │
                              │ 上游密钥1 │ │ 上游密钥N │ │ 免费额度池 │
                              └──────────┘ └──────────┘ └──────────┘
                                    │           │           │
                                    └───────────┼───────────┘
                                                ▼
                              integrate.api.nvidia.com/v1
                              + 多供应商免费 API 端点
```

### 3.2 请求流转详图

```
 客户端/IDE 请求 (OpenAI 协议，主链路直通)
        │
        ▼
 ┌──────────────────────┐
 │  Gateway 入口          │
 │  认证 → 限流 → 路由    │
 └────────┬─────────────┘
          │
          ▼
 ┌──────────────────────┐
 │  OpenAI 协议直通       │
 │  （Anthropic/Gemini    │
 │   转换器为实验性内置，  │
 │   当前未接入主链路）    │
 └────────┬─────────────┘
          │
          ▼
 ┌──────────────────────┐
 │  17算法互锁调度        │
 │  健康性过滤 → 严格公平  │
 │  调度（A17）选出        │
 │  最优桶+密钥            │
 └────────┬─────────────┘
          │
          ▼
 ┌──────────────────────┐
 │  上游请求转发          │
 │  httpx 异步连接池      │
 │  tenacity 指数退避    │
 │  → NVIDIA NIM /      │
 │    多供应商端点        │
 └────────┬─────────────┘
          │
          ▼
 ┌──────────────────────┐
 │  响应处理与回转        │
 │  流式/非流式           │
 │  龙虾文档适配注入      │
 │  指标采集 → 算法反馈   │
 └────────┬─────────────┘
          │
          ▼
    客户端/IDE 协议格式响应
```

### 3.3 管理后台架构

```
 ┌──────────────────────────────────────────────────────────────┐
│                      管理后台入口（单服务）                      │
├───────────────────────────┬──────────────────────────────────┤
│      控制台 UI            │        Admin API                  │
│                           │                                   │
│  /admin （登录页）        │   /gw/admin/*                     │
│  /admin/console           │   ├─ 登录（HMAC Token 24h）        │
│  ├─ 仪表盘                │   ├─ 上游密钥管理                  │
│  ├─ 上游密钥              │   ├─ 下游客户/密钥管理             │
│  ├─ 下游客户              │   ├─ 桶监控                       │
│  ├─ 桶监控                │   ├─ 请求日志                     │
│  ├─ 请求日志              │   ├─ 算法统计/实时                 │
│  ├─ 算法引擎              │   ├─ 系统配置/维护模式             │
│  ├─ 系统监控              │   ├─ 错误码                       │
│  ├─ 系统配置              │   ├─ 商用检测                     │
│  ├─ 错误码                │   └─ 并发/IP 监控                 │
│  └─ 商用检测              │                                   │
│                           │                                   │
│  /gw/dbadmin （SQLAdmin：数据库表 CRUD）                       │
└───────────────────────────┴──────────────────────────────────┘
                            │
        └────────── 统一认证 ACU_ADMIN_PASSWORD ──────────────────┘
```

---

## 4. 17算法互锁调度体系

### 4.1 体系总览

Gateway 的调度引擎（SurgeScheduler）由 **17 个算法** 组成，形成一个**互锁协同**的调度体系。

**核心机制（v10 重构后）**：评分仅用于**健康性过滤**（评分 < 0.1 视为不健康而被排除），最终的密钥选择由**算法 17 严格公平调度器**决定——在健康候选中按"近期使用次数最少 → 分配序号最早"排序，确保流量均匀分布（目标均衡度 90%+）。

> 权威算法清单以 `gateway/app/scheduler.py` 模块头注释与算法元数据（`get_algorithm_card`）为准。

**评分公式**（用于健康性过滤与权重参考）：

```
score = base_weight × A8 × A9 × A11 × A13 × A15 × A16
```

其中：
- `base_weight`：密钥/桶的基础权重（管理员可配置）
- `A8`：5xx 退避衰减因子（0~1）
- `A9`：区域故障隔离因子（0 或 1，隔离时为 0）
- `A11`：池化动态权重调节因子（0.5~2.0）
- `A13`：冷密钥预热因子（0.3→0.6→0.9→1.0）
- `A15`：Trae 趋势感知因子（0.6~1.3）
- `A16`：Lobster 弹性调度因子（0.5 为脱壳谷值）

> A1~A7 为**数据采集与标记**层算法，不直接参与评分计算，但为其他算法提供输入。
> A10 为**全局监控**算法，用于仪表盘展示。
> A14 为**自愈引擎**，在异常时触发恢复动作。
> A17 为**最终选择器**，替代旧方案的乘数式轮询。

### 4.2 十七算法清单（与 scheduler.py 一致）

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

### 4.3 桶级冷却四条强制约束

1. 冷却状态只存储在 (key_id, model) 复合桶级别，不存在任何密钥级冷却状态字段
2. `select_key` 检查冷却状态时只使用复合键查询，不存在任何密钥级查询路径
3. 软繁忙与冷却完全分离：RPM 超限只标记桶级软繁忙，永不调用冷却函数
4. 后台异步任务只操作桶级状态，不修改任何密钥级聚合状态

### 4.4 select_key 流程

```
请求到达
   │
   ▼
1. 获取所有活跃密钥
   │
   ▼
2. 硬性过滤：冷却、隔离、自愈、密钥解密失败
   │
   ▼
3. 健康性评分过滤：评分 < 0.1 视为不健康，排除
   │
   ▼
4. 严格公平选择（A17）：按"近期使用次数最少 → 分配序号最早"排序
   │
   ▼
5. 最小候选池保障：若健康候选 < 3，放宽 predicted_busy/soft_busy 限制
   │
   ▼
6. 记录使用并返回
```

---


## 5. 多协议转换机制

### 5.1 支持的协议

> **当前状态（如实说明）**：以下转换能力为**实验性内置**，当前**未接入默认请求链路**——主链路（`/v1/chat/completions` 等端点）直通 OpenAI 协议，不做协议转换。转换器代码位于 `gateway/app/transformers/`（Anthropic/Gemini）与 `gateway/app/translator.py`（编排与 Ollama），供后续启用或二次开发使用。

Gateway 内置了**三种非 OpenAI 协议**到 **OpenAI Chat Completions** 格式的双向转换（实验性，未默认启用）：

| 协议 | API 端点 | 转换方向 | 特殊支持 |
|------|----------|----------|----------|
| **Anthropic** | Messages API (`/v1/messages`) | Anthropic ↔ OpenAI | Tool Calling 转换 |
| **Gemini** | generateContent API (`/v1/models/:model:generateContent`) | Gemini ↔ OpenAI | 流式响应转换 |
| **Ollama** | `/api/chat` | Ollama ↔ OpenAI | 流式响应转换 |

### 5.2 智能协议检测

通过 `detect_protocol_with_ide()` 函数实现**含 IDE 识别**的智能协议检测：

```python
def detect_protocol_with_ide(request_headers, request_body, model_name) -> tuple[str, str]:
    """
    检测优先级:
    1. 显式指定 (headers 中 x-protocol)
    2. IDE 特征识别 (User-Agent / 请求特征)
    3. 模型名推断 (claude-* → anthropic, gemini-* → gemini)
    4. 请求体特征推断 (anthropic 字段存在 → anthropic)
    5. 默认 → openai

    返回: (protocol, ide_type)
    """
```

### 5.3 IDE 协议适配

Gateway 对主流 IDE 工具进行**原生协议适配**，使各 IDE 可直接连接使用：

| IDE 工具 | 适配方式 | 协议支持 |
|----------|----------|----------|
| **Cursor** | Anthropic 原生协议适配 | Anthropic ↔ OpenAI + Tool Calling |
| **Claude Code** | Anthropic 原生协议适配 | Anthropic ↔ OpenAI + Tool Calling |
| **Cline** | OpenAI 兼容协议 | OpenAI 直通 |
| **Continue** | OpenAI 兼容协议 | OpenAI 直通 |
| **Cherry Studio** | 多协议自动检测 | Anthropic/Gemini/OpenAI 自动识别 |
| **通用 IDE 插件** | 自动协议检测 | 根据请求特征自动选择转换策略 |

### 5.4 转换流程

```
入站请求 (任意协议 / 任意 IDE)
        │
        ▼
  detect_protocol_with_ide()
        │
        ├── 返回 (protocol, ide_type)
        │
   ┌────┼────────┬──────────┐
   │    │        │          │
   ▼    ▼        ▼          ▼
OpenAI Anthropic  Gemini    Ollama
(直通)  │        │          │
        ▼        ▼          ▼
  anthropic_to_  gemini_to_  ollama_to_
  openai()      openai()    openai()
        │        │          │
        └────┬───┴──────────┘
             │
             ▼
    统一 OpenAI 格式
             │
             ▼
      Gateway 调度转发
             │
             ▼
    上游响应 (OpenAI)
             │
        ┌────┴────────┬──────────┐
        │             │          │
        ▼             ▼          ▼
   openai_to_   openai_to_  openai_to_
   anthropic()  gemini()    ollama()
        │             │          │
        └────┬────────┴──────────┘
             │
             ▼
    客户端/IDE 协议格式响应 (流式)
```

### 5.5 Tool/Function Calling 转换

支持各协议间的 Tool/Function Calling 格式转换：

| 转换函数 | 说明 |
|----------|------|
| `anthropic_tools_to_openai()` | Anthropic tool 格式 → OpenAI function 格式 |
| `openai_tools_to_anthropic()` | OpenAI function 格式 → Anthropic tool 格式 |
| `gemini_tools_to_openai()` | Gemini FunctionDeclaration → OpenAI function 格式 |
| `openai_tools_to_gemini()` | OpenAI function 格式 → Gemini FunctionDeclaration |

### 5.6 流式响应转换

所有协议均支持**流式（Streaming）**响应的实时转换：

| 源格式 | 目标格式 | 说明 |
|--------|----------|------|
| OpenAI SSE | Anthropic SSE | 逐事件转换，保持流式语义 |
| OpenAI SSE | Gemini SSE | 逐事件转换 |
| OpenAI SSE | Ollama NDJSON | SSE → NDJSON 格式转换 |
| Anthropic SSE | OpenAI SSE | 逐事件转换 |
| Gemini SSE | OpenAI SSE | 逐事件转换 |
| Ollama NDJSON | OpenAI SSE | NDJSON → SSE 格式转换 |

### 5.7 多轮对话上下文保持

通过 `ConversationContext` 类实现多轮对话的上下文保持，确保协议转换过程中对话连贯性不丢失：

```
ConversationContext
├── messages: list[dict]      # 对话历史
├── system_prompt: str        # 系统提示词
├── model: str                # 模型标识
├── tools: list               # 工具定义
├── preserve_context()        # 保留上下文
├── to_openai_messages()      # 转为 OpenAI 消息格式
├── to_anthropic_messages()   # 转为 Anthropic 消息格式
├── to_gemini_contents()      # 转为 Gemini contents 格式
└── to_ollama_messages()      # 转为 Ollama 消息格式
```

---

## 6. NVIDIA NIM集成

### 6.1 集成概述

AQUA Gateway 通过 **NVIDIA NIM** (NVIDIA Inference Microservices) 提供大模型推理能力，采用 OpenAI 兼容接口标准接入。

| 属性 | 说明 |
|------|------|
| **Base URL** | `integrate.api.nvidia.com/v1` |
| **接口标准** | OpenAI Chat Completions 兼容 |
| **认证方式** | Bearer Token（上游密钥池化） |
| **协议** | HTTPS / SSE（流式） |

### 6.2 模型目录

Gateway 支持以下 **11+** 模型，涵盖不同参数规模与应用场景：

| 模型系列 | 模型名称 | 关键能力 |
|----------|----------|----------|
| **Nemotron** | Nemotron Ultra | 旗舰级推理，复杂任务 |
| | Nemotron Super | 高性能推理，平衡效率 |
| | Nemotron Nano | 轻量推理，低延迟 |
| **Llama** | Llama-4 | Meta 开源旗舰，通用能力 |
| **DeepSeek** | DeepSeek R1 | 推理增强，数学/代码 |
| | DeepSeek V3 | 通用对话，多语言 |
| **Kimi** | Kimi K2.6 | **1M 上下文**，长文档理解 |
| **Qwen** | Qwen3 | 阿里通义，中文优化 |
| **Step** | Step 3.7 | 阶跃星辰，中文推理 |
| **MiniMax** | MiniMax M3 | 多模态，音视频理解 |

### 6.3 模型能力矩阵

| 能力 | 支持模型 | 说明 |
|------|----------|------|
| **1M 上下文** | Kimi K2.6 等 | 支持百万级 Token 输入，长文档/代码库级理解 |
| **工具调用** | Nemotron 系列、Llama-4 等 | OpenAI 兼容 Function Calling |
| **流式输出** | 全部模型 | SSE 流式响应，逐 Token 输出 |
| **多模态** | MiniMax M3 等 | 图像/音频/视频输入理解 |

### 6.4 NIM 集成架构

```
Gateway (:8000)
      │
      │  OpenAI 兼容请求
      ▼
┌──────────────────────────┐
│  NVIDIA NIM 路由层        │
│  base_url: integrate.    │
│  api.nvidia.com/v1       │
│                          │
│  请求格式: OpenAI 兼容    │
│  响应格式: OpenAI 兼容    │
│  流式: SSE               │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  密钥池化调度             │
│  17算法选出最优密钥       │
│  → Bearer Token 注入     │
└──────────┬───────────────┘
           │
           ▼
   integrate.api.nvidia.com/v1
           │
     ┌─────┼─────┐
     ▼     ▼     ▼
  Nemotron Llama DeepSeek
  Kimi    Qwen  Step
  MiniMax ...
```

---

## 7. 慷慨型网关

### 7.1 设计理念

慷慨型网关（Generous Gateway）是 AQUA Gateway 的特色模块，旨在将**多个供应商的免费额度**聚合为统一的免费服务入口，为用户提供零成本 AI 访问能力。

```
核心思路:
  多个供应商免费额度 → 聚合池 → 统一免费入口 → 用户
```

### 7.2 核心组件

#### FreeQuotaPool — 免费额度聚合池

| 属性 | 说明 |
|------|------|
| **职责** | 聚合多个供应商的免费 API 额度，形成统一池 |
| **数据结构** | 每个供应商记录：端点 URL、免费额度上限、已用量、状态 |
| **水位线** | 实时计算各供应商额度水位（高/中/低/耗尽） |

```
FreeQuotaPool
├── Supplier A: { url, quota_total, quota_used, status: ACTIVE }
├── Supplier B: { url, quota_total, quota_used, status: ACTIVE }
├── Supplier C: { url, quota_total, quota_used, status: DOWN }
└── ...
    │
    ▼
  水位线监控:
  ┌────────────────────────────────┐
  │  ████████░░  高水位 (>70%)     │
  │  ██████░░░░  中水位 (30~70%)   │
  │  ████░░░░░░  低水位 (<30%)     │
  │  ░░░░░░░░░░  耗尽 (0%)        │
  └────────────────────────────────┘
```

#### GenerousLoadBalancer — 多供应商负载均衡

| 属性 | 说明 |
|------|------|
| **职责** | 在多个免费额度供应商间进行负载均衡调度 |
| **策略** | 优先选择额度水位最高的供应商，避免单供应商过载 |
| **故障转移** | 自动检测供应商故障并切换到备用供应商 |

### 7.3 故障自动转移机制

```
请求到达
   │
   ▼
GenerousLoadBalancer 选择供应商
   │
   ├── 供应商 A 请求 ──► 成功 ──► 返回响应
   │
   └── 供应商 A 请求 ──► 失败
           │
           ▼
      错误计数 +1
           │
      ┌────┴────┐
      │         │
  错误 < 3   错误 ≥ 3
      │         │
      ▼         ▼
   重试      标记 DOWN
   下一个供应商  │
                ▼
           启动 5 分钟恢复定时器
                │
           5 分钟后
                │
                ▼
           尝试恢复 → 探测请求
                │
           ┌────┴────┐
           │         │
        成功       失败
           │         │
           ▼         ▼
      标记 ACTIVE  保持 DOWN
```

| 参数 | 值 | 说明 |
|------|------|------|
| **故障判定阈值** | 3 次连续错误 | 3 次错误后标记供应商为 DOWN |
| **恢复等待时间** | 5 分钟 | DOWN 状态 5 分钟后尝试恢复 |
| **恢复探测** | 单次请求 | 成功则恢复 ACTIVE，失败则继续保持 DOWN |

### 7.4 额度水位线监控

| 水位线 | 范围 | 行为 |
|--------|------|------|
| **高水位** | > 70% 额度剩余 | 正常分配，优先使用 |
| **中水位** | 30%~70% 额度剩余 | 正常分配，关注趋势 |
| **低水位** | < 30% 额度剩余 | 降低分配权重，准备切换 |
| **耗尽** | 0% 额度剩余 | 停止分配，切换到其他供应商 |

---

## 8. 龙虾文档适配

### 8.1 设计理念

龙虾文档适配器（Lobster Document Adapter）是 AQUA Gateway 的文档处理模块，支持将多种格式的文档解析后注入 LLM 上下文，实现"文档→对话"的无缝衔接。

> 命名由来：龙虾脱壳——文档解析如同脱去外壳，提取精华内容注入 LLM。

### 8.2 支持的文档格式

| 格式 | 解析库 | 能力 |
|------|--------|------|
| **PDF** | pypdf | 文本提取、表格转 Markdown、页面级解析 |
| **DOCX** | python-docx | 段落提取、表格转 Markdown、样式保留 |
| **HTML** | beautifulsoup4 + lxml | 结构化提取、标签清洗、表格转 Markdown |
| **TXT** | 原生 Python | 纯文本直接读取 |

### 8.3 自动类型检测

```python
def detect_document_type(content: bytes, filename: str) -> str:
    """
    双重检测策略:
    1. Magic Bytes 检测 (二进制特征)
       - PDF:  %PDF-  (0x25504446)
       - DOCX: PK\x03\x04 (ZIP 签名)
       - HTML: <!DOCTYPE 或 <html
    2. 文件扩展名检测 (后备)
       - .pdf, .docx, .html, .htm, .txt
    """
```

```
文档输入
   │
   ├── 方式1: 二进制内容 (bytes)
   │     │
   │     ▼
   │   Magic Bytes 检测
   │   %PDF- → PDF
   │   PK\x03\x04 → DOCX (ZIP)
   │   <!DOCTYPE → HTML
   │   纯文本 → TXT
   │
   └── 方式2: 文件名 (filename)
         │
         ▼
       扩展名检测
       .pdf → PDF
       .docx → DOCX
       .html/.htm → HTML
       .txt → TXT
```

### 8.4 表格转Markdown

所有文档格式中的表格统一转换为 Markdown 格式，确保 LLM 可理解：

```
PDF/DOCX/HTML 表格
       │
       ▼
  解析表格结构 (行/列/合并单元格)
       │
       ▼
  生成 Markdown 表格
       │
       ▼
  | 列1 | 列2 | 列3 |
  |------|------|------|
  | 数据 | 数据 | 数据 |
```

### 8.5 LLM 上下文注入

龙虾文档适配器提供两种上下文注入方式：

#### `to_context_string()` — 文本上下文注入

```
文档内容 → 格式化文本 → 拼接到 system_prompt 或 user message
```

#### `to_openai_messages()` — OpenAI 消息格式注入

```
文档内容 → OpenAI message 格式 → 直接作为对话历史输入
```

```
文档 (PDF/DOCX/HTML/TXT)
       │
       ▼
  Lobster Document Adapter
       │
       ├─ to_context_string()
       │    │
       │    ▼
       │  "以下是文档内容:\n## 标题\n正文...\n| 表格 |\n..."
       │    │
       │    ▼
       │  拼接到 system_prompt 或 user message
       │
       └─ to_openai_messages()
            │
            ▼
          [
            {"role": "system", "content": "你是一个文档分析助手..."},
            {"role": "user", "content": "请分析以下文档:\n..."}
          ]
            │
            ▼
          直接作为对话历史输入
```

### 8.6 智能截断（Token 估算）

当文档内容超出模型上下文窗口时，适配器执行智能截断：

| 策略 | 说明 |
|------|------|
| **Token 估算** | 按字符数/4 粗估 Token 数（中文约 1 字符 ≈ 1~2 Token） |
| **截断策略** | 保留文档头部（标题/摘要）+ 尾部（结论/总结），中间省略 |
| **截断标记** | 插入 `[...文档中间部分已省略...]` 提示 |
| **优先级** | 系统提示词 > 文档摘要 > 文档正文 > 表格 > 附录 |

---

## 9. 安全体系

### 9.1 加密体系总览

```
┌───────────────────────────────────────────────────────────────────┐
│                          加密体系                                  │
├─────────────────────────┬─────────────────────────────────────────┤
│      密钥加密            │         认证与哈希                       │
│                         │                                         │
│  上游 NV 密钥:           │  下游客户端密钥:                         │
│    Fernet(HKDF-SHA256)  │    SHA-256 哈希入库（认证查库用）         │
│    salt=acu-upstream-   │                                         │
│         key-derivation  │  Admin Token:                           │
│                         │    HMAC-SHA256 (24h有效期)               │
│  下游客户端密钥:          │                                         │
│    Fernet(HKDF-SHA256)  │  管理员密码:                             │
│    salt=acu-client-     │    明文恒定时间比较                       │
│         key-derivation  │                                         │
│                         │  SQLAdmin Session:                      │
│                         │    itsdangerous 签名 cookie              │
└─────────────────────────┴─────────────────────────────────────────┘
```

### 9.2 密钥加密详情

#### 上游密钥 & 客户端密钥加密

采用 **Fernet + HKDF-SHA256** 双层加密：

```
原始密钥 (plaintext)
      │
      ▼
 HKDF-SHA256 (派生加密密钥)
 ├── salt: 固定域分隔字符串（上游/客户端/代理凭据各一条派生路径，互不通用）
 │    上游密钥:   acu-upstream-key-derivation
 │    客户端密钥:  acu-client-key-derivation
 │    代理凭据:   acu-proxy-credential-derivation
 ├── info: 上下文信息
 └── length: 32 bytes (Fernet 要求)
      │
      ▼
 Fernet 对称加密
 ├── key: HKDF 派生的 32 字节密钥
 └── 输出: Fernet token (含时间戳、HMAC)
      │
      ▼
 存储加密密钥到数据库
```

主密钥 `upstream_master_key`（32 字节 base64）存于 `settings` 表，首次启动自动生成。三条派生路径的隔离性由单测 `tests/test_gateway_security.py::TestDerivationIsolation` 守卫：任一路径的密文用其他路径解密必抛 `InvalidToken`。

> **Fernet 密文不可用于查重**：token 内含随机 IV，同一明文两次加密结果不同，因此"这个密钥是否已入库"只能**解出库内明文再比对**（批量添加 `POST /gw/admin/upstreams/bulk` 即如此）。又因每次加解密都要重跑一遍 HKDF，批量场景的加解密循环一律放进 `asyncio.to_thread`，不占事件循环；单行密文损坏只 `except: continue` 丢掉该行的查重能力，不阻断整批导入。
>
> **代理池是反例，不要照抄解密查重**：`proxies` 表只把密码列加密，代理的身份信息（`scheme`/`host`/`port`/`username`）都是明文列，因此 `POST /gw/admin/proxies/bulk` 的查重只需一条 `SELECT name, scheme, host, port, username FROM proxies`，完全不碰解密（有源码级契约测试守卫"批量代理添加函数体内不得出现 `decrypt`"）。判重口径是**四元组**而非"地址+端口"：住宅代理常以用户名区分会话/出口，同 IP 同端口不同账号是不同代理；反之同端点同账号只是口令不同，几乎只会是粘贴错误，按重复跳过。

#### 下游客户端密钥认证路径

客户端密钥（`sk-` + 32 随机字符）在库中同时保留两种形态：

```
generate_client_key() → "sk-xxxx..."（明文仅在签发响应中返回一次）
      │
      ├── SHA-256 哈希  → client_api_keys.key_hash（认证时按哈希查库，O(1)）
      └── Fernet 密文   → client_api_keys.key_ciphertext（管理员 reveal 用）
```

认证入口 `authenticate_client()`（`gateway/app/public_api.py`）走「哈希查库 + L1/L2 缓存」，明文密钥不落日志、不进上游请求头。

### 9.3 管理 Token（HMAC-SHA256）

网关侧**不使用 JWT**。管理 Token 由 `gateway/app/security.py` 自实现，格式为 `base64url(payload).hex(hmac_sha256)`：

| 属性 | 说明 |
|------|------|
| **算法** | HMAC-SHA256（`hmac` + `hashlib`，无第三方依赖） |
| **签名密钥** | `settings` 表中的 `gateway_secret`（首次启动自动生成） |
| **有效期** | 24 小时（`exp - iat == 86400`） |
| **载荷** | `{role: "admin", iat: timestamp, exp: timestamp}` |
| **校验** | 签名比对（`hmac.compare_digest`）→ `exp` 未过期 → `role == "admin"`，任一不满足返回 `None` |

```
管理员密码 (恒定时间比较)
       │
       ▼
  create_admin_token(secret)
       │
       ▼
  返回 Token（同时写入 admin_token cookie）
       │
       ▼
  后续请求携带: Authorization: Bearer <token> 或 cookie
       │
       ▼
  verify_admin_token(token, secret)
       │
       ├── 有效 → 放行
       └── 过期/篡改/角色不符 → 401 Unauthorized
```

### 9.4 认证与会话

| 机制 | 算法 | 有效期 | 用途 |
|------|------|--------|------|
| **下游 API 密钥** | SHA-256 哈希查库 | 长期（可吊销） | `/v1/*` 客户端认证 |
| **Admin Token** | HMAC-SHA256 | 24 小时 | 控制台与 `/gw/admin/*` 认证 |
| **管理员密码** | `hmac.compare_digest` 恒定时间比较 | 由环境变量决定 | 换取 Admin Token / SQLAdmin 登录 |
| **SQLAdmin Session** | itsdangerous 签名 cookie | 进程内有效 | `/gw/dbadmin` 面板会话 |

### 9.5 网络安全

| 机制 | 配置 | 说明 |
|------|------|------|
| **CORS** | `CORS_ALLOWED_ORIGINS` 环境变量 | 白名单域名，逗号分隔 |
| **IP 限流** | 登录：10次/分钟 | 防暴力破解 |
| **IP 限流** | 管理：60次/分钟 | 防管理接口滥用 |
| **请求体上限** | 10 MB（含 chunked） | `RequestSizeLimitMiddleware` |
| **可信代理** | `AQUA_TRUST_PROXY_HEADERS` | 直连场景忽略一切 XFF/CF 伪造头 |

### 9.6 商用检测六维度

Gateway 实现了**6 维度商用检测**体系，识别可能将免费 API 用于商业目的的行为：

| 维度 | 检测指标 | 商用特征 |
|------|----------|----------|
| **1. 请求间隔** | 请求时间间隔分布 | 极其规律的间隔（如固定 2s），疑似自动化脚本 |
| **2. 模型切换** | 短时间内模型切换频率 | 频繁切换模型做 A/B 对比，疑似商业产品调优 |
| **3. 并发量** | 同时进行的请求数 | 持续高并发（>10），疑似批量服务 |
| **4. 语义相似度** | 连续请求的语义相似度 | 大量高度相似请求（模板化 Prompt），疑似批量生成 |
| **5. IP 分布** | 同一密钥关联的 IP 数 | 多 IP 共用一密钥，疑似密钥共享/代理 |
| **6. 突发模式** | 请求量的突发特征 | 定期爆发式请求，疑似定时任务/批量处理 |

```
请求进入
   │
   ▼
┌──────────────────────────────────────────┐
│           商用检测引擎 (6维度)             │
│                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ 1.间隔    │ │ 2.模型切换│ │ 3.并发量  │ │
│  │ 规律性    │ │ 频率     │ │ 持续高并发│ │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ │
│        │            │            │       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ 4.语义    │ │ 5.IP分布  │ │ 6.突发   │ │
│  │ 相似度    │ │ 多IP共用  │ │ 定期爆发  │ │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ │
│        │            │            │       │
│        └────────────┼────────────┘       │
│                     │                    │
│                     ▼                    │
│              综合评分 (0~100)             │
│              > 阈值 → 标记商用            │
└──────────────────────────────────────────┘
```

### 9.7 安全最佳实践

- ✅ 所有密钥存储前加密，永不明文持久化（下游密钥明文仅在签发响应中返回一次）
- ✅ 管理员密码不硬编码，通过环境变量注入；未配置时一切登录被拒绝（杜绝空密码）
- ✅ 上游/下游/代理凭据三条 HKDF 派生路径隔离，互不通解（单测守卫）
- ✅ CORS 白名单替代通配符
- ✅ IP 限流防暴力破解
- ✅ Admin Token 恒定时间签名比对（`hmac.compare_digest`）+ 24h 过期 + 角色校验
- ✅ Fernet + HKDF 工业级对称加密
- ✅ 6 维度商用检测防止免费资源滥用
- ✅ 控制台零内联事件/零内联脚本 + 全量 `esc()` 转义（由 `tests/static-smoke.mjs` 强制）

---

## 10. 管理后台

### 10.1 Admin API (`/gw/admin/*`)

共 73 个端点，全部要求 `require_admin`（Admin Token）。按分类：

| 端点分类 | 代表路径 | 功能 |
|----------|----------|------|
| **认证** | `POST /gw/admin/login` | 管理员密码校验 → 签发 24h HMAC Admin Token（写入 cookie） |
| **上游密钥** | `/gw/admin/upstreams`、`/upstreams/bulk`、`/upstreams/{id}/reveal`、`/upstreams/health-check` | 上游密钥 CRUD、批量添加（每行一个密钥、自动命名）、明文 reveal、启停、探活、解冻、出网模式绑定 |
| **代理池** | `/gw/admin/proxies`、`/proxies/bulk`、`/proxies/{id}`、`/proxies/{id}/test` | 代理 CRUD（socks5/socks5h/http/https，无认证或账号密码）、批量添加（每行一个 `协议://[用户名:密码@]地址:端口`、自动命名）、启停、连通性测试 |
| **下游客户** | `/gw/admin/clients`、`/clients/{id}/keys`、`/clients/{id}/keys/{kid}/reveal` | 客户与密钥 CRUD、签发/吊销、明文 reveal、用量查询 |
| **桶监控** | `/gw/admin/buckets`、`/buckets/{key_id}/{model}/unfreeze` | 桶状态查看、RPM/TPM 统计、手动解冻 |
| **算法** | `/gw/admin/algorithms/realtime`、`/algorithm-stats`、`/algorithm/{num}` | 17 算法实时状态、统计与单算法详情 |
| **仪表盘/统计** | `/gw/admin/dashboard`、`/dashboard/comparison`、`/stats/*`、`/realtime-traffic` | 全局概览、时间段对比、趋势/延迟/错误分析 |
| **请求日志** | `/gw/admin/request-logs`、`/request-logs/cleanup` | 日志查询、详情、汇总统计、按天清理 |
| **商用检测** | `/gw/admin/commercial-detection`、`/commercial/*` | 检测结果、阈值/开关、白名单、封禁与解封 |
| **慷慨网关** | `GET /gw/admin/generous/status` | 免费额度水位线、供应商状态、负载均衡分布 |
| **模型目录** | `/gw/admin/nim/models`、`/models/status`、`/sync-models`、`/validate-models` | NIM 模型列表、状态、同步与校验 |
| **系统监控** | `/gw/admin/system/concurrency`、`/system/ip-monitor/*` | 并发汇总、IP 监控、异常与封禁列表、手动解封 |
| **熔断/错误** | `/gw/admin/circuit-breakers`、`/error-codes`、`/error-stats`、`/active-errors` | 熔断器状态与重置、错误码字典与统计 |
| **配置** | `/gw/admin/settings`、`/maintenance` | 系统配置读写、维护模式热切换 |
| **审计/调试** | `/gw/admin/audit-logs`、`/debug/test` | 管理操作审计、连通性自检 |

> v12.0 起管理接口只有 **Admin Token** 一条认证路径：原 `/gw/admin/platform-tokens`（机器间令牌 + scope 授权）已彻底移除。

### 10.2 算法可视化面板

`/gw/admin/algorithms/realtime` 提供 17 算法的实时可视化：

```
┌─────────────────────────────────────────────────────┐
│              算法可视化面板 (Realtime)                │
├─────────────────────────────────────────────────────┤
│                                                     │
│  评分公式: score = base × A8 × A9 × A11 × A13 ×    │
│                   A15 × A16                         │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │  实时乘数仪表                                 │    │
│  │  A8(5xx退避):  0.80 ████████░░               │    │
│  │  A9(隔离):     1.00 ██████████               │    │
│  │  A11(动态权重): 1.20 ████████████             │    │
│  │  A13(预热):    0.60 ██████░░░░               │    │
│  │  A15(Trae):    1.10 ███████████              │    │
│  │  A16(Lobster): 1.00 ██████████               │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │  桶/密钥权重分布 (动态柱状图)                  │    │
│  │  Bucket-1: 0.85 █████████                    │    │
│  │  Bucket-2: 0.62 ██████                       │    │
│  │  Bucket-3: 1.00 ██████████                   │    │
│  │  Bucket-4: 0.30 ███                          │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  参数动态调整 │ 历史趋势 │ 告警状态                   │
└─────────────────────────────────────────────────────┘
```

### 10.3 时间段对比

`/gw/admin/dashboard/comparison` 支持不同时间段的指标对比：

| 对比维度 | 说明 |
|----------|------|
| **请求量** | 对比两时段的 RPM/TPM 变化趋势 |
| **错误率** | 对比 4xx/5xx 错误率变化 |
| **延迟** | 对比 P50/P95/P99 延迟分布 |
| **算法效果** | 对比各算法乘数的变化趋势 |

### 10.4 商用检测控制台

商用检测相关端点（对应控制台「商用检测」页）：

| 子功能 | 路径 | 说明 |
|--------|------|------|
| **检测结果** | `GET /gw/admin/commercial-detection` | 商用嫌疑客户端列表与评分 |
| **标记管理** | `PUT /gw/admin/commercial-detection/{client_id}` | 手动调整客户端商用标记 |
| **封禁/解封** | `POST /gw/admin/commercial-detection/{client_id}/block`、`/unblock` | 对嫌疑客户端封禁与解封 |
| **开关与阈值** | `GET /gw/admin/commercial/settings`、`POST /commercial/toggle`、`POST /commercial/threshold` | 检测总开关与判定阈值 |
| **白名单** | `POST`/`DELETE /gw/admin/commercial/whitelist/{client_id}` | 白名单增删（豁免检测） |

### 10.5 慷慨网关状态面板

`/gw/admin/generous/status` 展示慷慨型网关的实时状态：

```
┌──────────────────────────────────────────┐
│         慷慨网关状态 (Generous Status)    │
├──────────────────────────────────────────┤
│                                          │
│  供应商状态:                              │
│  ├─ Supplier A: ACTIVE  水位 65% ██████  │
│  ├─ Supplier B: ACTIVE  水位 40% ████    │
│  └─ Supplier C: DOWN    水位  0% ░░░░    │
│                   ↑ 5分钟后恢复探测        │
│                                          │
│  负载均衡分布:                            │
│  ├─ Supplier A: 58%                      │
│  ├─ Supplier B: 42%                      │
│  └─ Supplier C: 0% (DOWN)               │
│                                          │
│  故障转移记录:                            │
│  ├─ 14:30 Supplier C → 3次错误 → DOWN    │
│  └─ 14:35 恢复探测中...                   │
└──────────────────────────────────────────┘
```

### 10.6 NIM模型目录

`/gw/admin/nim/models` 展示 NVIDIA NIM 可用模型列表：

| 信息 | 说明 |
|------|------|
| 模型列表 | 全部可用模型的名称与 ID |
| 能力标签 | 1M上下文 / 工具调用 / 流式 / 多模态 |
| 状态监控 | 模型可用性实时检测 |
| 调用量统计 | 各模型的请求量与 Token 消耗 |

### 10.7 SQLAdmin 数据库管理

| 路径 | 功能 |
|------|------|
| `/gw/dbadmin` | Gateway 数据库所有表的 CRUD 管理 |

SQLAdmin 提供：
- 表浏览与数据查看
- 记录增删改查
- 关系外键展示
- 数据过滤与搜索
- 分页浏览

### 10.8 认证机制

所有管理入口共用同一组管理员密码环境变量：

```
ACU_ADMIN_PASSWORD (明文，hmac.compare_digest 恒定时间比较)
        │
        ├──► Admin API /gw/admin/login → Admin Token (HMAC-SHA256, 24h)
        │       └─► 控制台 /admin/console 与全部 /gw/admin/* 端点
        └──► SQLAdmin /gw/dbadmin  同一变量认证
```

**未配置时一切登录被拒绝**：`admin_api.py` 模块导入即抛 `RuntimeError` 让进程起不来，SQLAdmin 侧记 `[FATAL]` 并禁用全部登录。配置为空串时校验函数也会先挡一次——`hmac.compare_digest(b"", b"")` 为真，不挡就等于空密码可登录（v10.1 踩过）。

---

## 11. 部署运维指南

### 11.1 环境变量清单

| 变量名 | 必填 | 说明 | 示例 |
|--------|------|------|------|
| `ACU_ADMIN_PASSWORD` | ✅ | 管理员密码明文（控制台与 SQLAdmin 面板共用；缺失即启动报错） | `your-strong-password` |
| `PG_PASSWORD` | ✅ | PostgreSQL 密码（缺失时启动即报错） | `db-password` |
| `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` | ❌ | 数据库连接参数（默认 `localhost:5432/aqua_gateway/aqua`） | `localhost` / `5432` |
| `ADMIN_SESSION_SECRET` | 建议 | SQLAdmin 面板 Session 签名密钥（缺失用临时随机值，重启失效） | `random-secret` |
| `GW_DB_POOL_SIZE` | ❌ | psycopg2 连接池大小（下限 5，默认 30） | `30` |
| `CORS_ALLOWED_ORIGINS` | ❌ | CORS 白名单域名，逗号分隔（默认 `http://localhost:8000`） | `https://api.example.com` |
| `AQUA_TRUST_PROXY_HEADERS` | ❌ | 反代场景信任 X-Forwarded-For（反代部署设 1） | `0` |
| `AQUA_WATCHLIST` | ❌ | 商用检测监控名单（逗号分隔客户端名） | `client-a,client-b` |
| `AQUA_DEBUG_ERRORS` | ❌ | 错误响应调试模式（生产保持 0） | `0` |
| `LOBSTER_MAX_BYTES` | ❌ | 文档解析输入上限（字节，默认 20MB） | `20971520` |

> 完整分组与生成命令见项目根目录 `.env.example`（v12.0 起共 14 项，已随 platform 模块删除 `AQUA_PLATFORM_TOKEN`、`JWT_SECRET_KEY`、`PLATFORM_ENCRYPT_KEY`、`PG_PLATFORM_*`、`PG_GATEWAY_*`、`GW_BASE_URL`、`SESSION_COOKIE_SECURE`、`REGISTRATION_OPEN`、`SMTP_*`）。

> ⚠️ **安全提示**：所有包含密钥/密码的环境变量**切勿**硬编码到代码或配置文件中，应通过安全的密钥管理工具或 `.env` 文件（已加入 `.gitignore`）注入。

### 11.2 启动命令

```bash
cd gateway && uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> **必须单 worker**（不加 `--workers`）：17 算法调度器为进程内状态。
> 建议使用进程管理器（如 systemd、supervisor）管理服务，确保自动重启和日志管理。

#### 生产环境示例（systemd）

```ini
# /etc/systemd/system/aqua-gateway.service
[Unit]
Description=AQUA Gateway
After=network.target postgresql.service

[Service]
Type=simple
User=aqua
WorkingDirectory=/opt/aqua/gateway
EnvironmentFile=/opt/aqua/.env
ExecStart=/opt/aqua/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 11.3 数据库

- **引擎**：PostgreSQL 15+（**唯一支持**的数据库，无 SQLite 运行模式）
- **驱动**：psycopg2（同步 ThreadedConnectionPool）+ asyncpg（异步）
- **初始化**：服务启动时**自动初始化**，创建表结构和默认数据
- **部署前置**：需先创建数据库和用户（见 README 快速部署）

```
Gateway DB: aqua_gateway（PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD）
            v12.0 起为唯一数据库，无跨库访问
```

### 11.4 健康检查

```bash
curl http://localhost:8000/healthz

# 详细字段（需管理员认证）
curl "http://localhost:8000/healthz?verbose=1" -H "Authorization: Bearer <admin_token>"
```

返回示例：

```json
{
  "status": "healthy",
  "version": "8.0",
  "uptime": 86400,
  "buckets": {
    "total": 5,
    "active": 4,
    "isolated": 1
  }
}
```

### 11.5 维护模式

通过 Admin API **热切换**维护模式，无需重启服务：

```bash
# 开启维护模式
curl -X POST http://localhost:8000/gw/admin/maintenance \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "message": "系统维护中，预计30分钟后恢复"}'

# 关闭维护模式
curl -X POST http://localhost:8000/gw/admin/maintenance \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

维护模式下：
- 所有 API 请求返回 **503 Service Unavailable**
- 响应体包含维护公告信息
- Admin API 仍然可用

### 11.6 常用运维操作

| 操作 | 方法 | 说明 |
|------|------|------|
| 查看服务状态 | `curl http://localhost:8000/healthz` | 健康检查端点 |
| 开启维护模式 | `POST /gw/admin/maintenance` | 热切换，无需重启 |
| 查看桶状态 | `GET /gw/admin/buckets` | 各桶 RPM/TPM/健康度 |
| 查看算法状态 | `GET /gw/admin/algorithms/realtime` | 17 算法实时可视化 |
| 时间段对比 | `GET /gw/admin/dashboard/comparison` | 指标对比分析 |
| 查看仪表盘 | `GET /gw/admin/dashboard` | 全局概览与趋势 |
| 商用检测管理 | `/gw/admin/commercial-detection`、`/commercial/*` | 结果/阈值/白名单/封禁 |
| 慷慨网关状态 | `GET /gw/admin/generous/status` | 免费额度水位/供应商 |
| NIM模型目录 | `GET /gw/admin/nim/models` | 可用模型与能力 |
| 解冻桶/密钥 | `POST /gw/admin/buckets/{key_id}/{model}/unfreeze`、`/upstreams/{key_id}/unfreeze` | 手动解除冷却/冻结 |
| 熔断器重置 | `POST /gw/admin/circuit-breakers/reset` | 手动复位熔断状态 |
| 代理池管理 | `/gw/admin/proxies`、`POST /proxies/bulk`、`POST /proxies/{id}/test` | 代理增删改查、批量添加、启停与连通性测试 |
| 清理请求日志 | `DELETE /gw/admin/request-logs/cleanup?days=N` | 手动清理（另有每 6 小时自动任务） |
| 数据库管理 | `/gw/dbadmin` | SQLAdmin CRUD |

### 11.7 日志与监控

- **请求日志**：每个 API 请求记录到数据库，可通过 `/gw/admin/request-logs` 查询；成功日志保留 3 天、错误日志 90 天，调度器每 6 小时自动清理
- **算法指标**：17 算法的实时状态可通过 `/gw/admin/algorithms/realtime` 可视化监控
- **健康度**：全局健康度评分（A10）在仪表盘实时展示
- **告警建议**：当健康度低于阈值时，A14 自愈引擎自动触发恢复动作
- **慷慨网关**：免费额度水位线与供应商状态实时监控
- **商用检测**：6 维度检测结果实时更新
- **审计**：管理端写操作记入 `audit_logs`，可通过 `/gw/admin/audit-logs` 查询

---

## 12. 代理池与出网通道

**职责单一**：`gateway/app/proxy_pool.py` 是「上游密钥 → 出网通道」解析与 httpx 客户端复用的**唯一**归属地。调度器、公开 API 主链路、管理端探活都只通过它取客户端。

### 12.1 数据模型

```
proxies                                upstream_keys（v12.1 迁移新增两列）
├── id / name                          ├── proxy_mode  direct | bind | rotate
├── scheme  socks5|socks5h|http|https  └── proxy_id    proxy_mode='bind' 时指向 proxies.id
├── host / port
├── username              ''=无认证代理
├── password_ciphertext   Fernet(salt=acu-proxy-credential-derivation)
├── status  active | inactive
└── last_check_at / last_check_ok / last_check_msg
```

迁移由 `database.py::_migrate_upstream_keys_proxy(conn)` 完成（先查 `_get_column_names` 再 `ALTER TABLE ADD COLUMN`），旧库升级无需人工干预，默认值 `direct` 保证行为不变。

### 12.2 三种出网模式

| 模式 | 行为 | 异常回退 |
|------|------|----------|
| `direct` | 直连上游（默认） | — |
| `bind` | 绑定池内指定代理 | 该代理被删除/停用 → **回退直连 + WARNING**，不让请求失败 |
| `rotate` | 活跃代理间 round-robin（进程内游标） | 池内无活跃代理 → 回退直连 + WARNING |

删除代理时同步 `UPDATE upstream_keys SET proxy_mode='direct', proxy_id=NULL WHERE proxy_id=%s`，接口返回受影响密钥数，杜绝脏绑定。

### 12.3 快照缓存与客户端复用

```
请求 → scheduler.get_http_pool(key_id)
         └→ proxy_pool.get_client(key_id, stream)
              ├→ resolve_url(key_id)
              │    ├→ _ensure_snapshot()   5s TTL；过期时 to_thread 读 DB
              │    │     ├ 活跃代理：解密密码 → build_proxy_url() → [{id,name,url}]
              │    │     ├ 绑定关系：key_id → (proxy_mode, proxy_id)
              │    │     └ _evict_stale_clients()  关闭已删/改配代理的客户端
              │    └→ _select_url(key_id)   按模式选路，返回 None 表示直连
              └→ get_client_for_url(url, stream)   按 (URL, 是否流式) 复用
         └→ 返回 None 时 scheduler 回落自身直连池
```

- **热路径零 DB 查询**：5 秒 TTL 快照；管理端每次代理/密钥写操作调用 `proxy_pool.invalidate()` 立即失效，配置变更秒级生效
- **客户端工厂唯一**：`build_client(proxy_url, stream)` 同时服务直连池与代理池，保证超时与连接池口径一致——非流式 `Timeout(120, connect=10)`、流式 `Timeout(600, connect=10, read=600)`、`Limits(100/20, keepalive_expiry=60)`、`http2=False`
- **无泄漏**：快照刷新时关闭不再存在于活跃集合中的客户端；`lifespan` 关闭阶段 `proxy_pool.close_all()`
- **单 worker 约束**：轮询游标与客户端缓存均在进程内，与调度器状态同一约束

### 12.4 探活一致性（关键正确性约束）

代理专用密钥若用直连探活，必然探测失败并被自动停用——这是一类自伤故障。因此**全部四条探活路径**都走密钥自己的出网通道：

| 路径 | 位置 | 出网获取方式 |
|------|------|--------------|
| 无效模型清理（异步） | `scheduler.py` | `await self.get_http_pool(active_keys[0]["id"])`，timeout 10s |
| 密钥健康检查（异步） | `scheduler.py` | `await self.get_http_pool(key_id)`，timeout 8s |
| 停用密钥 30 分钟复活探测（同步） | `scheduler.py::_recheck_auto_deactivated` | `httpx.get(..., proxy=proxy_pool.resolve_url_sync(key_id))` |
| 管理端批量探活 | `admin_api.py::/upstreams/health-check` | 逐密钥 `await scheduler.get_http_pool(key_id)` |

### 12.5 凭据安全

- 代理密码使用**第三条** HKDF 派生路径（salt `acu-proxy-credential-derivation`），与上游密钥、下游客户端密钥互不通解，由 `tests/test_gateway_security.py::TestDerivationIsolation` 守卫
- 任何接口都不返回密码密文或明文，列表仅返回 `has_auth` 布尔值
- SQLAdmin `ProxyView` 的 `form_excluded_columns` / `column_details_exclude_list` 均排除 `password_ciphertext`
- `build_proxy_url()` 对用户名/密码做 percent-encoding，防止 `@ : /` 破坏 URL 结构导致凭据落入 host 段
- 连通性测试解密密码仅在内存中构造一次性客户端，访问上游 `/models`；拿到任意 HTTP 状态即视为通道可用（避免把上游 401 误判为代理不通）

### 12.6 批量录入（`POST /gw/admin/proxies/bulk`）

多行文本，一行一个 `scheme://[user:pass@]host:port`，与单个添加 `POST /proxies` **并存**（后者行为不变，由源码契约测试锁定）。

| 环节 | 实现要点 |
|------|---------|
| 行解析 | 纯函数 `parse_bulk_proxies()`（`gateway/app/admin_api.py`），用标准库 `urlsplit`：密码含 `@` 时按**最右侧** `@` 切 userinfo、含 `:` 时按**首个** `:` 分割用户名与密码；空行与 `#` 注释行不进结果但**仍占行号**，行号要对应用户在输入框里看到的行 |
| 编码对称 | 用户名/密码解析后过一遍 `unquote()`，与 `build_proxy_url()` 的 `quote(safe="")` 互逆；单测校验 `解析 → 拼装 → 再解析` 凭据逐字不变 |
| 逐行拒收 | 缺协议前缀 / 协议不在白名单 / 缺地址 / 端口缺失或不在 1-65535（`urlsplit` 放行 `:0`，此处额外对齐 `build_proxy_url` 的下界）/ 地址后带路径参数 fragment（裸尾斜杠容忍）/ IPv6 字面量 / 有密码无用户名。跳过原因**绝不回显原始行**——行里带着密码明文，而原因要进响应体 |
| IPv6 | 明确拒收：`build_proxy_url()` 不补方括号，收下即产生不可用记录（单个添加同受此限，属已知限制而非批量特有） |
| 查重 | 四元组 `(scheme, host, port, username)`，一条 `SELECT` 明文列比对，**不解密**（对照 §9.2 上游密钥必须解密的原因） |
| 命名 | 复用 `gen_bulk_names()`（与上游密钥批量添加同一实现），`{前缀}-{序号}`，默认前缀 `px`，序号从库内同前缀最大值续排并跳过已占用名 |
| 写入 | 有密码的行整批在 `asyncio.to_thread` 内加密（每次都要重跑 HKDF），随后一条多值参数化 INSERT，要么全进要么全不进；成功后 `proxy_pool.invalidate()` 让 5 秒快照立即失效 |
| 审计 | 一代理一行，经 `insert_audit_many()` 一次往返写入，保留 `target_id` 可追溯性 |
| 响应 | 行号 / id / 名称 / 协议 / 地址 / 端口 / 用户名 / `has_auth`，**不含密码**；单次上限 `BULK_MAX_LINES = 200` |

### 12.7 依赖

SOCKS 支持来自 `httpx[socks]`（`socksio`），已声明于 `gateway/requirements.txt`。`httpx` 0.28 使用单数 `proxy=` 参数（`proxies=` 已移除），协议白名单为 `http` / `https` / `socks5` / `socks5h`。

---

> **文档版本**: v12.1 | **最后更新**: 2026-08-29 | **AQUA Gateway**
