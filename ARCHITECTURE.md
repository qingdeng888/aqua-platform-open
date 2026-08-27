# AQUA AI Platform v11.0 — 项目架构文档

> **版本**: v11.0（同步 2026-08 修订）
> **最后更新**: 2026-08-27
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

---

## 1. 项目概述

### 1.1 平台定位

**AQUA AI Platform v10.0** 是一个**开源AI网关平台**，其核心使命是：

> 将多个 NVIDIA NIM 上游密钥**池化**，为终端用户提供**统一的 OpenAI 兼容 API**。

平台通过密钥池化技术，将分散的上游 API 密钥整合为一个高可用的统一入口，降低用户接入门槛，提升密钥利用率，并实现智能负载均衡与故障自愈。

### 1.2 双服务架构

AQUA 采用**双服务**架构，两个独立进程各司其职：

| 服务 | 职责 | 默认端口 | 说明 |
|------|------|----------|------|
| **Platform** | 用户平台 | `8001` | 面向终端用户的 Web 界面：注册、登录、密钥管理、用量查看等 |
| **Gateway** | API 网关 | `8000` | 面向第三方客户端的 OpenAI 兼容 API 入口，负责调度、转发、限流等 |

两服务通过内部平台令牌（`AQUA_PLATFORM_TOKEN`）进行服务间认证，Platform 以客户端身份调用 Gateway 的管理 API 完成密钥发放等操作。

### 1.3 核心使命分解

```
┌──────────────────────────────────────────────────────────────┐
│                        核心使命                               │
├──────────────┬──────────────┬────────────────────────────────┤
│   密钥池化    │   统一API    │         智能调度               │
│  N个上游密钥  │  OpenAI兼容  │     17算法互锁                 │
│  → 1个入口    │  多协议转换   │  故障自愈 + 负载均衡            │
│              │  IDE协议适配  │  慷慨型网关 + 龙虾文档适配       │
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
| **HTTP Client** | httpx | >= 0.28.0 | 异步连接池，替代 requests |
| **Encryption** | cryptography | >= 43.0.0 | Fernet 对称加密 + HKDF 密钥派生 |
| **Retry** | tenacity | >= 8.2.0 | 指数退避重试策略，提升请求可靠性 |
| **JWT** | python-jose | >= 3.3.0 | JWT 标准化令牌签发与验证（HS256） |
| **Password Hash** | bcrypt | >= 4.0.0 | 用户密码哈希，12 rounds |
| **PDF 解析** | pypdf | >= 4.0.0 | PDF 文档文本与表格提取 |
| **DOCX 解析** | python-docx | >= 1.1.0 | Word 文档内容解析 |
| **HTML 解析** | beautifulsoup4 | >= 4.12.0 | HTML 文档结构化提取 |
| **HTML 引擎** | lxml | >= 5.0.0 | 高性能 XML/HTML 解析后端 |
| **Template** | Jinja2 | >= 3.1.0 | HTML 模板渲染 |

### 2.2 技术栈关系图

```
┌────────────────────────────────────────────────────────────────┐
│                      Application Layer                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────┐ │
│  │ FastAPI   │  │ SQLAdmin │  │ Jinja2    │  │ Lobster 文档  │ │
│  │ (路由)    │  │ (管理后台)│  │ (模板)    │  │ 适配器        │ │
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
│  │    cryptography (加密) + python-jose (JWT) + bcrypt   │    │
│  │    Fernet + HKDF-SHA256    HS256    12 rounds         │    │
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
                           ┌──────────────────────────────────────────────────┐
                           │     AQUA AI Platform v10.0       │
                           └──────────────────────────────────────────────────┘

 ┌──────────────┐                                                    ┌──────────────────────┐
 │  用户/浏览器   │─────── HTTP ────────►  Platform (:8001)           │     IDE 工具          │
 └──────────────┘                       ┌────────────────┐           │                      │
                                        │  用户注册/登录   │           │  Cursor / Claude Code│
                                        │  密钥管理       │           │  Cline / Continue    │
                                        │  用量统计       │           │  Cherry Studio       │
                                        │  SQLAdmin      │           │  通用 IDE 插件        │
                                        │  (/platform/   │           └──────────┬───────────┘
                                        │   dbadmin)     │                      │
                                        └───────┬────────┘                      │
                                                │                   OpenAI 协议  │
                                    AQUA_PLATFORM_TOKEN              (主链路直通)  │
                                    (内部服务间认证)                   │
                                                ▼                               │
                                        ┌────────────────┐                         │
                                        │  Gateway (:8000)│ ◄── 多协议 API ────────┘
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
│                      管理后台入口                               │
├───────────────────────────┬──────────────────────────────────┤
│      Gateway Admin        │        Platform Admin             │
│                           │                                   │
│  /gw/admin/*              │   (Platform 内置)                 │
│  ├─ 认证                  │   ├─ 用户管理                     │
│  ├─ 密钥管理              │   ├─ 密钥查看                     │
│  ├─ 客户管理              │   ├─ 用量统计                     │
│  ├─ 桶监控                │   └─ ...                         │
│  ├─ 算法可视化面板         │                                   │
│  ├─ 时间段对比             │                                   │
│  ├─ 商用检测控制           │                                   │
│  ├─ 慷慨网关状态           │                                   │
│  ├─ NIM模型目录           │                                   │
│  ├─ 仪表盘                │                                   │
│  ├─ 日志                  │                                   │
│  ├─ 策略                  │                                   │
│  ├─ 令牌                  │                                   │
│  ├─ 维护模式              │                                   │
│  └─ 调试                  │                                   │
│                           │                                   │
│  /gw/dbadmin              │   /platform/dbadmin               │
│  (SQLAdmin-Gateway)       │   (SQLAdmin-Platform)             │
│  数据库表 CRUD            │   数据库表 CRUD                    │
└───────────────────────────┴──────────────────────────────────┘
        │                               │
        └── 共用 ACU_ADMIN_PASSWORD_HASH / ACU_ADMIN_PASSWORD ──┘
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

AQUA AI Platform 通过 **NVIDIA NIM** (NVIDIA Inference Microservices) 提供大模型推理能力，采用 OpenAI 兼容接口标准接入。

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

慷慨型网关（Generous Gateway）是 AQUA AI Platform 的特色模块，旨在将**多个供应商的免费额度**聚合为统一的免费服务入口，为用户提供零成本 AI 访问能力。

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

龙虾文档适配器（Lobster Document Adapter）是 AQUA AI Platform 的文档处理模块，支持将多种格式的文档解析后注入 LLM 上下文，实现"文档→对话"的无缝衔接。

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
│  上游密钥:               │  JWT Token:                             │
│    Fernet(HKDF-SHA256)  │    python-jose, HS256 (24h有效期)        │
│                         │                                         │
│  客户端密钥:             │  Admin Token:                           │
│    Fernet(HKDF-SHA256)  │    HMAC-SHA256 (24h有效期)               │
│                         │                                         │
│  Platform API密钥:       │  用户密码:                               │
│    Fernet(PLATFORM_     │    bcrypt (12 rounds)                   │
│    ENCRYPT_KEY)         │                                         │
│                         │  Session:                               │
│                         │    httponly cookie, 24h有效期             │
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
 ├── salt: 固定域分隔字符串（上游/客户端各一条派生路径，互不通用）
 │    上游密钥:   acu-upstream-key-derivation
 │    客户端密钥:  acu-client-key-derivation
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

#### Platform API 密钥加密

使用独立的 Fernet 密钥，来自环境变量 `PLATFORM_ENCRYPT_KEY`：

```
PLATFORM_ENCRYPT_KEY (Fernet 格式, 环境变量)
      │
      ▼
 Fernet(PLATFORM_ENCRYPT_KEY).encrypt(api_key)
      │
      ▼
 存储加密后的 API 密钥
```

### 9.3 JWT 标准化认证

采用 **python-jose** 实现 JWT 令牌签发与验证，标准化认证流程：

| 属性 | 说明 |
|------|------|
| **库** | python-jose |
| **算法** | HS256 (HMAC-SHA256) |
| **有效期** | 24 小时 |
| **载荷** | `{sub: user_id, exp: timestamp, iat: timestamp, type: token_type}` |
| **签发** | `jose.jwt.encode(payload, secret, algorithm="HS256")` |
| **验证** | `jose.jwt.decode(token, secret, algorithms=["HS256"])` |

```
用户登录/管理认证
       │
       ▼
  验证密码/凭据
       │
       ▼
  jose.jwt.encode({
    sub: user_id,
    exp: now + 24h,
    iat: now,
    type: "admin" / "user"
  }, JWT_SECRET, algorithm="HS256")
       │
       ▼
  返回 JWT Token
       │
       ▼
  后续请求携带: Authorization: Bearer <jwt_token>
       │
       ▼
  jose.jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
       │
       ├── 有效 → 放行
       └── 过期/无效 → 401 Unauthorized
```

### 9.4 认证与会话

| 机制 | 算法 | 有效期 | 用途 |
|------|------|--------|------|
| **JWT Token** | python-jose, HS256 | 24 小时 | 标准化 API 认证 |
| **Admin Token** | HMAC-SHA256 | 24 小时 | Gateway Admin API 认证 |
| **User Password** | bcrypt (12 rounds) | 永久（直到修改） | Platform 用户登录 |
| **Session** | httponly cookie | 24 小时 | Platform 用户会话 |

### 9.5 网络安全

| 机制 | 配置 | 说明 |
|------|------|------|
| **CORS** | `CORS_ALLOWED_ORIGINS` 环境变量 | 白名单域名，逗号分隔 |
| **IP 限流** | 登录：10次/分钟 | 防暴力破解 |
| **IP 限流** | 管理：60次/分钟 | 防管理接口滥用 |

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

- ✅ 所有密钥存储前加密，永不明文持久化
- ✅ 管理员密码不硬编码，通过环境变量注入
- ✅ httponly cookie 防止 XSS 窃取
- ✅ CORS 白名单替代通配符
- ✅ IP 限流防暴力破解
- ✅ bcrypt 12 rounds 慢哈希防彩虹表
- ✅ JWT (python-jose, HS256) 标准化令牌
- ✅ Fernet + HKDF 工业级对称加密
- ✅ 6 维度商用检测防止免费资源滥用

---

## 10. 管理后台

### 10.1 Gateway Admin API (`/gw/admin/*`)

| 端点分类 | 路径前缀 | 功能 |
|----------|----------|------|
| **认证** | `/gw/admin/auth` | 管理员登录、JWT Token 签发、会话管理 |
| **密钥管理** | `/gw/admin/keys` | 上游密钥 CRUD、启用/禁用、批量操作 |
| **客户管理** | `/gw/admin/clients` | 客户 CRUD、配额设置、状态管理 |
| **桶监控** | `/gw/admin/buckets` | 桶状态查看、RPM/TPM 统计、密钥分布 |
| **算法可视化面板** | `/gw/admin/algorithms/realtime` | 17 算法实时状态可视化、参数动态调整、效果实时展示 |
| **时间段对比** | `/gw/admin/dashboard/comparison` | 不同时间段指标对比分析（请求量/错误率/延迟） |
| **商用检测控制** | `/gw/admin/commercial/*` | 商用检测规则配置、检测结果查看、阈值调整、标记管理 |
| **慷慨网关状态** | `/gw/admin/generous/status` | 免费额度水位线、供应商状态、负载均衡分布、故障转移记录 |
| **NIM模型目录** | `/gw/admin/nim/models` | NVIDIA NIM 可用模型列表、模型能力查询、模型状态监控 |
| **仪表盘** | `/gw/admin/dashboard` | 全局概览、请求量/错误率/延迟趋势图 |
| **日志** | `/gw/admin/logs` | 请求日志查询、过滤、导出 |
| **策略** | `/gw/admin/policies` | 限流策略、调度策略、冷却策略配置 |
| **令牌** | `/gw/admin/tokens` | 平台令牌管理、权限控制 |
| **维护模式** | `/gw/admin/maintenance` | 维护模式热切换、公告设置 |
| **调试** | `/gw/admin/debug` | 请求模拟、算法单步调试、配置检查 |

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

`/gw/admin/commercial/*` 提供完整的商用检测管理：

| 子功能 | 路径 | 说明 |
|--------|------|------|
| **规则配置** | `/gw/admin/commercial/rules` | 6 维度检测规则阈值调整 |
| **检测结果** | `/gw/admin/commercial/detections` | 已检测到的商用嫌疑记录 |
| **标记管理** | `/gw/admin/commercial/marks` | 手动标记/解除商用标记 |
| **统计概览** | `/gw/admin/commercial/stats` | 商用检测统计报表 |

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

| 服务 | 路径 | 功能 |
|------|------|------|
| **Gateway** | `/gw/dbadmin` | Gateway 数据库所有表的 CRUD 管理 |
| **Platform** | `/platform/dbadmin` | Platform 数据库所有表的 CRUD 管理 |

SQLAdmin 提供：
- 表浏览与数据查看
- 记录增删改查
- 关系外键展示
- 数据过滤与搜索
- 分页浏览

### 10.8 认证机制

所有管理后台共用同一组管理员密码环境变量：

```
ACU_ADMIN_PASSWORD_HASH (bcrypt 哈希，优先) / ACU_ADMIN_PASSWORD (明文回退，恒定时间比较)
        │
        ├──► Gateway Admin API  登录（要求必须配置 HASH）→ Admin Token (HMAC-SHA256, 24h)
        ├──► Gateway SQLAdmin   同一密码认证（未配置 HASH 时回退校验明文变量）
        └── Platform SQLAdmin  同一密码认证（同上回退逻辑）
```

---

## 11. 部署运维指南

### 11.1 环境变量清单

| 变量名 | 必填 | 说明 | 示例 |
|--------|------|------|------|
| `ACU_ADMIN_PASSWORD_HASH` | ✅ | 管理员密码 bcrypt 哈希（优先；Gateway Admin API 必需） | `bcrypt-hash-of-password` |
| `ACU_ADMIN_PASSWORD` | 回退 | 管理员密码明文（未配置 HASH 时 SQLAdmin 恒定时间比较回退） | `your-strong-password` |
| `PG_PASSWORD` | ✅ | Gateway PostgreSQL 密码（缺失时 Gateway 启动即报错） | `db-password` |
| `PG_PLATFORM_PASSWORD` | ✅ | Platform PostgreSQL 密码 | `db-password` |
| `PG_GATEWAY_PASSWORD` | ✅ | Platform 跨库访问 Gateway 库的密码（控制台用量统计） | `db-password` |
| `PLATFORM_ENCRYPT_KEY` | ✅ | Platform 用户 API Key 加密 Key，Fernet 格式 | `gAAAAABf...` (Fernet key) |
| `AQUA_PLATFORM_TOKEN` | ✅ | Platform → Gateway 平台令牌，两侧必须一致 | `platform-secret-token` |
| `JWT_SECRET_KEY` | ✅ | Platform 用户 JWT 签名密钥（HS256） | `random-secret` |
| `ADMIN_SESSION_SECRET` | 建议 | SQLAdmin 面板 Session 密钥（两服务共用；缺失用临时值） | `random-secret` |
| `PLATFORM_ADMIN_SESSION_SECRET` | 建议 | Platform 管理会话令牌密钥（缺失拒绝签发） | `random-secret` |
| `CORS_ALLOWED_ORIGINS` | ❌ | CORS 白名单域名，逗号分隔 | `https://aqua.example.com,https://admin.aqua.example.com` |
| `GW_BASE_URL` | ❌ | Platform 调用 Gateway 的地址 | `http://127.0.0.1:8000` |
| `SESSION_COOKIE_SECURE` | ❌ | 登录 Cookie 仅 HTTPS（默认 1，生产必须） | `1` |
| `REGISTRATION_OPEN` | ❌ | 是否开放注册（默认 1） | `1` |
| `AQUA_TRUST_PROXY_HEADERS` | ❌ | 反代场景信任 X-Forwarded-For（反代部署设 1） | `0` |
| `AQUA_WATCHLIST` | ❌ | 商用检测监控名单（逗号分隔客户端名） | `client-a,client-b` |
| `AQUA_DEBUG_ERRORS` | ❌ | 错误响应调试模式（生产保持 0） | `0` |
| `SMTP_HOST/PORT/USER/PASSWORD` | ❌ | SMTP 邮件服务（验证码/通知） | `smtp.qq.com` / `465` |

> 完整分组与生成命令见项目根目录 `.env.example`。Gateway/Platform 各自的 PG 连接组（`PG_HOST/PORT/DB/USER`、`PG_PLATFORM_*`、`PG_GATEWAY_*`）亦在 `.env.example` 中逐项列出。

> ⚠️ **安全提示**：所有包含密钥/密码的环境变量**切勿**硬编码到代码或配置文件中，应通过安全的密钥管理工具或 `.env` 文件（已加入 `.gitignore`）注入。

### 11.2 启动命令

#### Gateway 服务

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

#### Platform 服务

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

> 建议使用进程管理器（如 systemd、supervisor）管理两个服务，确保自动重启和日志管理。

#### 生产环境示例（systemd）

```ini
# /etc/systemd/system/aqua-gateway.service
[Unit]
Description=AQUA Gateway
After=network.target

[Service]
Type=simple
User=aqua
WorkingDirectory=/opt/aqua-platform/gateway
EnvironmentFile=/opt/aqua-platform/.env
ExecStart=/opt/aqua-platform/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/aqua-platform.service
[Unit]
Description=AQUA Platform
After=network.target

[Service]
Type=simple
User=aqua
WorkingDirectory=/opt/aqua-platform/platform
EnvironmentFile=/opt/aqua-platform/.env
ExecStart=/opt/aqua-platform/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
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
Gateway DB:  aqua_gateway（PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD）
Platform DB: aqua_platform（PG_PLATFORM_*）
跨库只读:    Gateway 库（PG_GATEWAY_*，供 Platform 控制台统计）
```

### 11.4 健康检查

```bash
# Gateway 健康检查
curl http://localhost:8000/healthz

# Platform 健康检查
curl http://localhost:8001/healthz
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
| 开启维护模式 | `/gw/admin/maintenance` API | 热切换，无需重启 |
| 查看桶状态 | `/gw/admin/buckets` | 各桶 RPM/TPM/健康度 |
| 查看算法状态 | `/gw/admin/algorithms/realtime` | 17 算法实时可视化 |
| 时间段对比 | `/gw/admin/dashboard/comparison` | 指标对比分析 |
| 查看仪表盘 | `/gw/admin/dashboard` | 全局概览与趋势 |
| 商用检测管理 | `/gw/admin/commercial/*` | 规则/结果/标记 |
| 慷慨网关状态 | `/gw/admin/generous/status` | 免费额度水位/供应商 |
| NIM模型目录 | `/gw/admin/nim/models` | 可用模型与能力 |
| 强制冷却重置 | `/gw/admin/debug` | 调试接口重置冷却状态 |
| 数据库管理 | `/gw/dbadmin` 或 `/platform/dbadmin` | SQLAdmin CRUD |

### 11.7 日志与监控

- **请求日志**：每个 API 请求记录到 Gateway 日志，可通过 `/gw/admin/logs` 查询
- **算法指标**：17 算法的实时状态可通过 `/gw/admin/algorithms/realtime` 可视化监控
- **健康度**：全局健康度评分（A10）在仪表盘实时展示
- **告警建议**：当健康度低于阈值时，A14 自愈引擎自动触发恢复动作
- **慷慨网关**：免费额度水位线与供应商状态实时监控
- **商用检测**：6 维度检测结果实时更新

---

> **文档版本**: v11.0 | **最后更新**: 2026-08-27 | **AQUA AI Platform**
