# AQUA AI Platform (Open Source Bundle)

AQUA AI Platform **v10.0** 开源源码包 —— 基于 **NVIDIA NIM 密钥池化** 的 AI 网关平台。

> 本仓库仅包含项目源码压缩包 `aqua-platform-open.zip`，**不含任何凭据/密钥**。上游 API Key 由部署者自行配置到 `.env`。

## 核心特性

- **密钥池化**：将多个 NVIDIA NIM 上游密钥整合为统一 OpenAI 兼容入口（`/v1/chat/completions`、`/v1/embeddings`、`/v1/models`）
- **16 算法互锁调度**：RPM 滑动窗口、软繁忙检测、自适应阈值、冷却、隔离、健康度评分、冷密钥渐进预热、自愈引擎等协同选择最优密钥
- **多协议转换**：自动识别 Cursor / Claude Code / Cline / Continue / Cherry Studio，在 Anthropic / Gemini / Ollama / OpenAI 协议间互转
- **模型 ID 智能映射**：400+ 别名，6 级匹配策略，非标准 ID 自动映射到正确模型
- **商用检测**：11 维行为识别（间隔分布、语义相似度、IP 分布、蒸馏行为、浏览器指纹等）
- **慷慨型网关**：多供应商免费额度池 + 负载均衡 + 故障自动转移
- **安全体系**：Fernet + HKDF 加密、JWT、bcrypt、ALTCHA PoW 验证码、IP 监控、软限流

## 架构

双服务：
- **Gateway (:8000)** — OpenAI 兼容 API 网关，负责调度/转发/限流/协议转换
- **Platform (:8001)** — 用户平台，注册登录、密钥管理、用量统计

## 依赖

- Python 3.13+
- PostgreSQL 15+（生产推荐）或 SQLite（开发）
- Node.js 18+（仅 ALTCHA 组件构建需要）
- NVIDIA NIM API Key（从 [build.nvidia.com](https://build.nvidia.com) 获取）

部署见压缩包内 `README.md`。
