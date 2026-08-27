// 职责：控制台概览——统计卡片、7日趋势SVG图、状态码分布、最近请求、今日排行榜、30秒自动刷新

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { ensureAuth } from '../auth.js';
import { Toast, Loader, emptyState, errorState, Fmt, escapeHtml, escapeAttr, Charts } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';
import { renderLeaderboardInto } from './leaderboard.js';

let refreshTimer = null;

export async function renderOverview() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console');
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }

  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">概览</h1>
        <div class="flex">
          <span class="muted" id="overview-last-refresh"></span>
          <button class="btn sm" id="overview-refresh-btn">${Icons.refresh(14)} 刷新</button>
        </div>
      </div>

      <div class="qq-banner">
        <div class="qq-banner-info">
          <span class="qq-banner-icon">💬</span>
          <div>
            <div class="qq-banner-title">加入 AQUA 交流群，获取技术支持与最新动态</div>
            <div class="qq-banner-sub">一群已满 · 二群已满 · 推荐三群：<span class="qq-num">1073851223</span></div>
          </div>
        </div>
        <a href="https://qm.qq.com/q/jGnpIQWY5c" target="_blank" rel="noopener noreferrer" class="btn-qq-join">加入三群 →</a>
      </div>

      <div class="service-notice">
        <span class="service-notice-icon">⚠️</span>
        <div>
          <strong style="font-size:14px">服务声明：</strong>
          本平台为<strong>完全免费</strong>的开源AI服务，不作出任何服务等级保证，不保证服务稳定性、可用性或响应速度。
          请合理使用平台资源，避免滥用行为。对于违反相关规定的用户，平台有权限制或终止服务。
          同意<a href="#/legal/disclaimer" target="_blank" style="text-decoration:underline">《AQUA 平台免责协议》</a>后继续使用。
        </div>
      </div>

      <div id="overview-stats" class="grid grid-5"></div>
      <div class="grid grid-2" style="margin-top:16px">
        <div class="card">
          <div class="card-header"><h3 class="card-title">近7天请求趋势</h3></div>
          <div id="overview-trend" style="min-height:240px"></div>
        </div>
        <div class="card">
          <div class="card-header"><h3 class="card-title">状态码分布</h3></div>
          <div id="overview-status"></div>
        </div>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-header">
          <h3 class="card-title">最近请求</h3>
          <a href="#/console/logs" class="btn sm ghost">查看全部</a>
        </div>
        <div id="overview-recent"></div>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-header">
          <h3 class="card-title"><span style="color:var(--accent-warning)">🏆</span> 今日全平台排行榜</h3>
          <span class="muted" id="leaderboard-summary"></span>
        </div>
        <div id="leaderboard-container"></div>
      </div>
    </div>
  `;

  document.getElementById('overview-refresh-btn').onclick = () => renderOverview();

  Loader.show('#overview-stats', '加载统计中');
  Loader.show('#overview-trend', '加载趋势中');
  Loader.show('#overview-status', '加载状态中');
  Loader.show('#overview-recent', '加载日志中');
  Loader.show('#leaderboard-container', '加载排行榜中');

  try {
    const [stats, ccs, lb] = await Promise.all([
      API.get('/api/user/stats'),
      API.get('/api/user/concurrency-stats').catch(() => null),
      API.get('/api/user/leaderboard?limit=20').catch(() => null),
    ]);
    const o = stats.overview || {};
    updateRefreshTime();

    renderStatCards('overview-stats', stats, o, ccs);
    renderTrend('overview-trend', stats.trend_7d || []);

    // 状态码分布（取最近100条日志推断）+ 最近10条请求
    const recentLogs = await API.get('/api/user/request-logs?page=1&page_size=100');
    renderStatusDistribution('overview-status', recentLogs.data || []);
    renderRecentLogs('overview-recent', (recentLogs.data || []).slice(0, 10));

    // 排行榜
    if (lb && lb.leaderboard && lb.leaderboard.length > 0) {
      renderLeaderboardInto('leaderboard-container', lb.leaderboard);
      const summary = document.getElementById('leaderboard-summary');
      if (summary) {
        summary.textContent = `今日活跃 ${lb.total?.active_users || 0} 人 · 总计 ${Fmt.number(lb.total?.total_requests || 0)} 请求 · ${Fmt.full(lb.total?.total_tokens || 0)} Tokens`;
      }
    } else {
      const el = document.getElementById('leaderboard-container');
      if (el) el.innerHTML = '<div class="empty-state" style="padding:24px"><div class="empty-text">暂无排行数据</div></div>';
    }

    // 无数据时显示新手引导
    const hasData = (o.total_requests || 0) > 0 || (recentLogs.data || []).length > 0;
    if (!hasData) {
      const recent = document.getElementById('overview-recent');
      if (recent && recent.parentElement) {
        const guide = document.createElement('div');
        guide.className = 'card guide-card';
        guide.style.marginTop = '16px';
        const inner = document.createElement('div');
        inner.className = 'guide-card-inner';
        inner.innerHTML = `
          ${Icons.bolt(20)}
          <div>
            <div class="guide-card-title">欢迎使用 AQUA AI平台</div>
            <div class="muted">当前还没有请求记录。前往 <a href="#/console/chat">AI对话</a> 体验，或在 <a href="#/console/keys">密钥管理</a> 创建API密钥接入你的应用。</div>
          </div>
        `;
        guide.appendChild(inner);
        recent.parentElement.appendChild(guide);
      }
    }

    // 30秒自动刷新统计（返回清理函数，路由切换时释放——修复原实现定时器泄漏）
    refreshTimer = setInterval(async () => {
      try {
        const [s, ccs2] = await Promise.all([
          API.get('/api/user/stats'),
          API.get('/api/user/concurrency-stats').catch(() => null),
        ]);
        if (!document.getElementById('overview-stats')) return; // 页面已切走
        renderStatCards('overview-stats', s, s.overview || {}, ccs2);
        renderTrend('overview-trend', s.trend_7d || []);
        updateRefreshTime();
      } catch (e) { /* 静默失败，下轮重试 */ }
    }, 30000);
  } catch (err) {
    Toast.error('加载概览失败：' + err.message);
    const el = document.getElementById('overview-stats');
    if (el) el.innerHTML = '';
    el.appendChild(errorState('加载失败，请重试', () => renderOverview()));
  }

  return () => { if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; } };
}

function updateRefreshTime() {
  const el = document.getElementById('overview-last-refresh');
  if (!el) return;
  const n = new Date();
  const p = (x) => String(x).padStart(2, '0');
  el.textContent = `最后更新 ${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`;
}

function renderStatCards(containerId, stats, o, ccs) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `
    <div class="stat-card accent">
      <div class="stat-label">今日请求数</div>
      <div class="stat-value">${Fmt.full(o.total_requests || 0)}</div>
      <div class="stat-trend flat">${Icons.clock(12)} 今日</div>
    </div>
    <div class="stat-card success">
      <div class="stat-label">今日Token消耗</div>
      <div class="stat-value" title="${escapeAttr(Fmt.full(o.total_tokens || 0))}">${Fmt.number(o.total_tokens || 0)}</div>
      <div class="stat-trend flat">输入 ${Fmt.number(o.prompt_tokens || 0)} · 输出 ${Fmt.number(o.completion_tokens || 0)}</div>
    </div>
    <div class="stat-card warning">
      <div class="stat-label">今日平均延迟</div>
      <div class="stat-value">${Fmt.latency(o.avg_latency || 0)}</div>
      <div class="stat-trend flat">${Icons.clock(12)} 毫秒</div>
    </div>
    <div class="stat-card info">
      <div class="stat-label">并发状态</div>
      <div class="stat-value" style="font-size:18px">${ccs ? escapeHtml(ccs.current + '/' + (ccs.limit_label || ccs.limit)) : '...'}</div>
      <div class="stat-trend flat">${ccs && ccs.tag ? escapeHtml(ccs.tag) : '峰值 ' + (ccs?.peak || 0)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">7天请求总数</div>
      <div class="stat-value">${Fmt.full((stats.trend_7d || []).reduce((acc, d) => acc + (d.request_count || 0), 0))}</div>
      <div class="stat-trend flat">近7天累计</div>
    </div>
  `;
}

function renderTrend(containerId, data) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!data || !data.length) {
    el.innerHTML = emptyState('暂无趋势数据');
    return;
  }
  el.innerHTML = Charts.sparkline(data.map((d) => ({
    label: (d.date || '').slice(5),
    value: d.request_count || 0,
    tip: `${d.date}: ${d.request_count}次 / ${Fmt.full(d.token_count || 0)} tokens`,
  })));
}

function renderStatusDistribution(containerId, logs) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!logs.length) {
    el.innerHTML = emptyState('暂无请求数据');
    return;
  }
  const groups = {};
  logs.forEach((l) => {
    const s = l.status || 'unknown';
    groups[s] = (groups[s] || 0) + 1;
  });
  const total = logs.length;
  el.innerHTML = `
    <div class="dist-list">
      ${Object.entries(groups).map(([s, n]) => {
        const pct = (n / total * 100).toFixed(1);
        const stInfo = Fmt.status(s);
        return `
          <div class="dist-item">
            <div class="dist-label">
              <span class="status-dot ${stInfo.cls}"></span>
              <span>${stInfo.text}</span>
              <span class="muted">${n}次 (${pct}%)</span>
            </div>
            <div class="progress-bar">
              <div class="progress-fill ${stInfo.cls}" style="width:${pct}%"></div>
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function renderRecentLogs(containerId, logs) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!logs.length) {
    el.innerHTML = emptyState('暂无请求记录');
    return;
  }
  el.innerHTML = `
    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th>模型</th>
            <th>状态</th>
            <th class="numeric">Token(输入/输出/总计)</th>
            <th class="numeric">延迟</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          ${logs.map((l) => {
            const st = Fmt.status(l.status);
            const latColor = Fmt.latencyColor(l.latency_ms);
            return `<tr>
              <td><code>${escapeHtml(l.model || '-')}</code></td>
              <td><span class="tag ${st.cls}"><span class="status-dot ${st.cls}"></span>${st.text}</span></td>
              <td class="numeric">
                <span class="token-cell">
                  <span class="token-in">${Fmt.number(l.prompt_tokens || 0)}</span> /
                  <span class="token-out">${Fmt.number(l.completion_tokens || 0)}</span> /
                  <span class="token-total">${Fmt.number(l.total_tokens || 0)}</span>
                </span>
              </td>
              <td class="numeric" style="color:${latColor}">${Fmt.latency(l.latency_ms)}</td>
              <td class="muted">${Fmt.timeAgo(l.created_at)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;
}
