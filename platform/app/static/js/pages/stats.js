// 职责：统计面板——总览卡片、7日趋势SVG图、模型使用分布、详细模型统计表、今日排行榜

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { ensureAuth } from '../auth.js';
import { Toast, Loader, emptyState, errorState, Fmt, escapeHtml, escapeAttr, Charts } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';
import { renderLeaderboardInto } from './leaderboard.js';
import { getModelNames } from './models.js';

export async function renderStats() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/stats');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">统计面板</h1>
        <button class="btn sm" id="stats-refresh-btn">${Icons.refresh(14)} 刷新</button>
      </div>
      <div id="stats-overview" class="grid grid-5"></div>
      <div class="grid grid-2" style="margin-top:16px">
        <div class="card">
          <div class="card-header"><h3 class="card-title">7天请求趋势</h3></div>
          <div id="stats-trend" style="min-height:260px"></div>
        </div>
        <div class="card">
          <div class="card-header"><h3 class="card-title">模型使用分布</h3></div>
          <div id="stats-models"></div>
        </div>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-header"><h3 class="card-title">详细模型统计</h3></div>
        <div id="stats-model-table"></div>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-header">
          <h3 class="card-title"><span style="color:var(--accent-warning)">🏆</span> 今日全平台排行榜</h3>
          <span class="muted" id="stats-leaderboard-summary"></span>
        </div>
        <div id="stats-leaderboard"></div>
      </div>
    </div>
  `;
  document.getElementById('stats-refresh-btn').onclick = () => renderStats();

  Loader.show('#stats-overview', '加载统计中');
  Loader.show('#stats-trend', '加载趋势中');
  Loader.show('#stats-models', '加载分布中');
  Loader.show('#stats-model-table', '加载表格中');
  Loader.show('#stats-leaderboard', '加载排行榜中');

  try {
    const [stats, ccs] = await Promise.all([
      API.get('/api/user/stats'),
      API.get('/api/user/concurrency-stats').catch(() => null),
    ]);
    const o = stats.overview || {};
    const hasData = (o.total_requests || 0) > 0 || (stats.trend_7d || []).length > 0;

    const elOverview = document.getElementById('stats-overview');
    elOverview.innerHTML = `
      <div class="stat-card accent">
        <div class="stat-label">今日请求</div>
        <div class="stat-value">${Fmt.full(o.total_requests || 0)}</div>
      </div>
      <div class="stat-card success">
        <div class="stat-label">今日Token</div>
        <div class="stat-value" title="${escapeAttr(Fmt.full(o.total_tokens || 0))}">${Fmt.number(o.total_tokens || 0)}</div>
      </div>
      <div class="stat-card warning">
        <div class="stat-label">输入Token</div>
        <div class="stat-value">${Fmt.number(o.prompt_tokens || 0)}</div>
      </div>
      <div class="stat-card info">
        <div class="stat-label">输出Token</div>
        <div class="stat-value">${Fmt.number(o.completion_tokens || 0)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">并发状态</div>
        <div class="stat-value" style="font-size:18px">${ccs ? escapeHtml(ccs.current + '/' + (ccs.limit_label || ccs.limit)) : '...'}</div>
        <div class="stat-trend flat">${ccs && ccs.tag ? escapeHtml(ccs.tag) : ccs ? '峰值 ' + (ccs.peak || 0) : ''}</div>
      </div>
    `;

    // 7日趋势（手写 SVG sparkline）
    renderTrendChart('stats-trend', stats.trend_7d || []);
    renderModelDistribution('stats-models', stats.model_distribution || []);
    renderModelTable('stats-model-table', stats.model_distribution || []);

    // 无数据时友好提示
    if (!hasData) {
      const tableEl = document.getElementById('stats-model-table');
      if (tableEl && tableEl.parentElement) {
        const hint = document.createElement('div');
        hint.className = 'card guide-card guide-card-info';
        hint.style.marginTop = '16px';
        const inner = document.createElement('div');
        inner.className = 'guide-card-inner';
        inner.innerHTML = `
          ${Icons.info(20)}
          <div>
            <div class="guide-card-title">暂无统计数据</div>
            <div class="muted">前往 <a href="#/console/chat">AI对话</a> 发起一次对话，统计数据将在请求后自动生成。</div>
          </div>
        `;
        hint.appendChild(inner);
        tableEl.parentElement.appendChild(hint);
      }
    }

    // 排行榜
    try {
      const lb = await API.get('/api/user/leaderboard?limit=20');
      if (lb && lb.leaderboard && lb.leaderboard.length > 0) {
        renderLeaderboardInto('stats-leaderboard', lb.leaderboard);
        const summary = document.getElementById('stats-leaderboard-summary');
        if (summary) {
          summary.textContent = `今日活跃 ${lb.total?.active_users || 0} 人 · 总计 ${Fmt.number(lb.total?.total_requests || 0)} 请求 · ${Fmt.full(lb.total?.total_tokens || 0)} Tokens`;
        }
      } else {
        const el = document.getElementById('stats-leaderboard');
        if (el) el.innerHTML = '<div class="empty-state" style="padding:24px"><div class="empty-text">暂无排行数据</div></div>';
      }
    } catch (e) {
      const el = document.getElementById('stats-leaderboard');
      if (el) el.innerHTML = '<div class="empty-state" style="padding:24px"><div class="empty-text">排行榜加载失败</div></div>';
    }
  } catch (err) {
    Toast.error('加载统计失败：' + err.message);
    const el = document.getElementById('stats-overview');
    el.innerHTML = '';
    el.appendChild(errorState('加载失败，请重试', () => renderStats()));
  }
}

function renderTrendChart(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!data || !data.length) {
    container.innerHTML = emptyState('暂无趋势数据');
    return;
  }
  container.innerHTML = Charts.sparkline(data.map((d) => ({
    label: (d.date || '').slice(5),
    value: d.request_count || 0,
    tip: `${d.date}: ${d.request_count}次 / ${Fmt.full(d.token_count || 0)} tokens`,
  })));
}

function renderModelDistribution(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!data.length) {
    container.innerHTML = emptyState('暂无模型使用数据');
    return;
  }
  const total = data.reduce((s, d) => s + (d.request_count || 0), 0) || 1;
  const colors = ['#00d4ff', '#448aff', '#00e676', '#ffab00', '#ff5252', '#ff4081', '#9c27b0', '#3f51b5'];
  const names = {};
  getModelNames().forEach((m) => { names[m.id] = m.display_name || m.id; });
  container.innerHTML = `
    <div class="dist-list">
      ${data.slice(0, 8).map((d, i) => {
        const pct = ((d.request_count || 0) / total * 100).toFixed(1);
        const display = d.display_name || names[d.model] || d.model;
        return `
          <div class="dist-item">
            <div class="dist-label">
              <span class="legend-dot" style="background:${colors[i % colors.length]}"></span>
              <span style="font-size:11px">${escapeHtml(display)}</span>
              <span class="muted">${pct}%</span>
            </div>
            <div class="progress-bar">
              <div class="progress-fill" style="width:${pct}%;background:${colors[i % colors.length]}"></div>
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function renderModelTable(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!data.length) {
    container.innerHTML = emptyState('暂无模型使用记录');
    return;
  }
  const names = {};
  getModelNames().forEach((m) => { names[m.id] = m.display_name || m.id; });
  const total = data.reduce((s, d) => s + (d.request_count || 0), 0);
  container.innerHTML = `
    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th>模型</th>
            <th class="numeric">请求数</th>
            <th class="numeric">Token消耗</th>
            <th>占比</th>
          </tr>
        </thead>
        <tbody>
          ${data.map((m) => {
            const dname = m.display_name || names[m.model] || m.model;
            return `<tr>
              <td><span title="${escapeHtml(m.model)}">${escapeHtml(dname)}</span><br><span class="text-xs text-muted">${escapeHtml(m.model)}</span></td>
              <td class="numeric">${Fmt.full(m.request_count || 0)}</td>
              <td class="numeric">${Fmt.full(m.token_count || 0)}</td>
              <td>
                <div class="progress-bar">
                  <div class="progress-fill" style="width:${Fmt.percent(m.request_count, total).replace('%', '')}%"></div>
                  <span>${Fmt.percent(m.request_count, total)}</span>
                </div>
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;
}
