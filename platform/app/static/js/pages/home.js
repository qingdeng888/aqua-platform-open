// 职责：首页——Hero/能力卡片/CTA/页脚，实时拉取 /api/public/stats 更新模型与算法数

import { Icons } from '../icons.js';
import { setSidebarVisible, highlightNav } from '../layout.js';

export async function renderHome() {
  setSidebarVisible(false);
  highlightNav('/');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="home-page">
      <section class="hero">
        <div class="hero-bg"></div>
        <div class="hero-content">
          <div class="hero-badge" id="hero-badge">免费 · 开源 · 66+ 可用模型</div>
          <h1 class="hero-title">AQUA AI平台</h1>
          <p class="hero-subtitle">聚合 DeepSeek、GLM、Kimi、千问、NVIDIA等主流大模型<br>OpenAI兼容接口，IDE即插即用</p>
          <div class="hero-actions">
            <a href="#/register" class="btn primary lg">${Icons.plus(18)} 立即开始</a>
            <a href="#/docs" class="btn lg">${Icons.docs(18)} 查看文档</a>
            <a href="#/qq-groups" class="btn lg btn-qq">💬 加入QQ交流群</a>
          </div>
          <div class="hero-stats">
            <div class="hero-stat"><span class="num" id="stat-models">66+</span><span class="lbl">可用模型</span></div>
            <div class="hero-stat"><span class="num" id="stat-algos">17</span><span class="lbl">调度算法</span></div>
            <div class="hero-stat"><span class="num">OpenAI</span><span class="lbl">兼容协议</span></div>
          </div>
        </div>
      </section>

      <section class="features">
        <div class="section-header">
          <h2>核心能力</h2>
          <p>面向开发者的免费AI基础设施</p>
        </div>
        <div class="grid grid-3">
          <div class="feature-card">
            <div class="feature-icon accent">${Icons.bolt(28)}</div>
            <h3>高速推理</h3>
            <p>17算法互锁调度系统，159个NVIDIA NIM密钥智能轮换，50+实测可用模型，避免429限流，毫秒级响应</p>
          </div>
          <div class="feature-card">
            <div class="feature-icon success">${Icons.cpu(28)}</div>
            <h3>多模型支持</h3>
            <p>66+实测可用模型，覆盖推理、视觉、对话、安全等场景，按厂商分组展示</p>
          </div>
          <div class="feature-card">
            <div class="feature-icon info">${Icons.shield(28)}</div>
            <h3>稳定可靠</h3>
            <p>分桶滑动窗口计数、自适应冷却、软繁忙标记，保障高并发下持续可用</p>
          </div>
          <div class="feature-card">
            <div class="feature-icon warning">${Icons.brain(28)}</div>
            <h3>推理过程</h3>
            <p>支持DeepSeek-R1、GLM等推理模型，流式展示推理过程，可折叠展开</p>
          </div>
          <div class="feature-card">
            <div class="feature-icon info">${Icons.globe(28)}</div>
            <h3>联网搜索</h3>
            <p>内置三级回退搜索引擎（DuckDuckGo/Bing），让AI获取实时互联网信息</p>
          </div>
          <div class="feature-card">
            <div class="feature-icon accent">${Icons.terminal(28)}</div>
            <h3>IDE兼容</h3>
            <p>OpenAI SDK兼容，直接对接 CC Switch、Cursor、Copilot 等开发工具</p>
          </div>
        </div>
      </section>

      <section class="cta">
        <div class="cta-card">
          <h2>立即开始你的AI之旅</h2>
          <p>免费注册，即刻获得5个API密钥，每密钥无配额限制，畅享66+实测可用模型</p>
          <a href="#/register" class="btn primary lg">${Icons.arrowRight(18)} 立即注册</a>
        </div>
      </section>

      <footer class="footer">
        <div class="footer-inner">
          <div class="footer-brand">
            <span>${Icons.logo(20)}</span>
            <span>AQUA AI平台</span>
          </div>
          <div class="footer-links">
            <a href="#/models">模型列表</a>
            <a href="#/docs">API文档</a>
            <a href="#/sponsor">赞助</a>
            <a href="#/legal/disclaimer">免责协议</a>
            <a href="#/login">登录</a>
            <a href="#/register">注册</a>
            <a href="#/qq-groups" data-route="/qq-groups">QQ群</a>
          </div>
          <div class="footer-copy">© 2026 AQUA Platform v11.0 · 开源项目</div>
        </div>
      </footer>
    </div>
  `;

  // 异步更新统计（/api/public/stats 新契约仅含 models/algorithms 等公共字段）
  fetch('/api/public/stats')
    .then((r) => (r.ok ? r.json() : {}))
    .then((data) => {
      if (data && data.models) {
        const badge = document.getElementById('hero-badge');
        const statEl = document.getElementById('stat-models');
        if (badge) badge.textContent = `免费 · 开源 · ${data.models}+ 可用模型`;
        if (statEl) statEl.textContent = data.models + '+';
      }
      if (data && data.algorithms) {
        const el = document.getElementById('stat-algos');
        if (el) el.textContent = String(data.algorithms);
      }
    })
    .catch(() => {});
}
