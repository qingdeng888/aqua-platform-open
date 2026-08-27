// 职责：模型状态监控——摘要卡片、降级横幅、按状态分组（正常/警告/异常）的模型健康卡片

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { ensureAuth } from '../auth.js';
import { errorState, Fmt, escapeHtml, escapeAttr } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

export async function renderModelStatus() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/model-status');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">模型状态监控</h1>
        <div class="flex">
          <span class="text-xs text-muted" id="ms-last-update">加载中...</span>
          <button class="btn sm" id="ms-refresh-btn">${Icons.refresh(14)} 刷新</button>
        </div>
      </div>
      <div id="ms-content">
        <div class="loader">
          <div class="loader-spinner">${Icons.spinner(32)}</div>
          <div class="loader-text">加载模型状态数据...</div>
        </div>
      </div>
    </div>
  `;
  document.getElementById('ms-refresh-btn').onclick = () => renderModelStatus();
  await loadModelStatus();
}

async function loadModelStatus() {
  const container = document.getElementById('ms-content');
  try {
    const data = await API.get('/api/user/models/status');
    const rawModels = data.models || [];
    const summary = data.summary || {};
    const lu = document.getElementById('ms-last-update');
    if (lu) lu.textContent = '更新于 ' + new Date().toLocaleTimeString('zh-CN');

    // 降级警告
    let degradedBanner = '';
    if (data.degraded) {
      degradedBanner = `<div class="ms-degraded"><span>${Icons.alert(18)}</span><span>数据源降级：${escapeHtml(data.degraded)}（无实时桶数据）</span></div>`;
    }

    // 排序：状态分组 → 健康分降序 → 延迟升序 → 成功率降序
    const sorted = sortModels(rawModels);

    // 摘要卡片
    const hasBucketData = sorted.some((m) => m.total_buckets > 0);
    const cards = [
      { label: '正常模型', value: summary.normal || 0, cls: 'success' },
      { label: '警告模型', value: summary.warning || 0, cls: 'warning' },
      { label: '异常模型', value: summary.abnormal || 0, cls: 'danger' },
      { label: '1h请求', value: Fmt.number(summary.total_requests_1h || 0), cls: 'accent' },
    ];
    if (hasBucketData) {
      cards.push({ label: '健康密钥', value: sorted.reduce((s, m) => s + (m.healthy_buckets || 0), 0), cls: 'success' });
      cards.push({ label: '冷却密钥', value: sorted.reduce((s, m) => s + (m.cooled_buckets || 0), 0), cls: 'warning' });
    }
    const summaryHtml = `<div class="ms-summary">` + cards.map((c) =>
      `<div class="stat-card ${c.cls}"><div class="stat-label">${c.label}</div><div class="stat-value">${c.value}</div></div>`
    ).join('') + `</div>`;

    // 按状态分组渲染
    const groups = { normal: [], warning: [], abnormal: [] };
    sorted.forEach((m) => { if (groups[m.status]) groups[m.status].push(m); });
    const statusNames = { normal: '正常', warning: '警告', abnormal: '异常' };
    const groupParts = [];
    for (const st of ['normal', 'warning', 'abnormal']) {
      const list = groups[st];
      if (!list || !list.length) continue;
      groupParts.push(`
        <div class="ms-section">
          <div class="ms-section-header">
            <span class="ms-section-title"><span class="status-dot ${st}" style="vertical-align:middle;margin-right:6px"></span>${statusNames[st]}模型</span>
            <span class="ms-section-count">${list.length} 个</span>
          </div>
          <div class="ms-card-list">
            ${list.map((m) => renderModelCard(m)).join('')}
          </div>
        </div>
      `);
    }

    container.innerHTML = degradedBanner + summaryHtml + groupParts.join('');
  } catch (e) {
    container.innerHTML = '';
    container.appendChild(errorState('加载失败: ' + e.message, loadModelStatus, 'alert'));
  }
}

function sortModels(models) {
  const rank = { normal: 0, warning: 1, abnormal: 2 };
  return [...models].sort((a, b) => {
    const ra = rank[a.status] ?? 9, rb = rank[b.status] ?? 9;
    if (ra !== rb) return ra - rb;
    if ((b.health_score || 0) !== (a.health_score || 0)) return (b.health_score || 0) - (a.health_score || 0);
    if ((a.avg_latency_ms || 0) !== (b.avg_latency_ms || 0)) return (a.avg_latency_ms || 0) - (b.avg_latency_ms || 0);
    return (b.success_rate || 0) - (a.success_rate || 0);
  });
}

function renderModelCard(m) {
  const statusCls = m.status === 'normal' ? 'active' : (m.status === 'warning' ? 'warning' : 'cooling');
  const sr = m.success_rate || 0;
  const latency = m.avg_latency_ms || 0;
  const tb = m.total_buckets || 0;
  const hasBuckets = tb > 0;
  const c5xx = m.count_5xx_1h || 0;
  const c429 = m.count_429_1h || 0;

  // 推荐标记：正常 + 健康分>=90 + 延迟<2s + 成功率>=95%
  const isRecommended = m.status === 'normal' && (m.health_score || 0) >= 90 && latency < 2000 && sr >= 95;

  const srColor = sr >= 95 ? 'var(--accent-success)' : sr >= 80 ? 'var(--accent-warning)' : 'var(--accent-danger)';
  const latColor = latency < 3000 ? 'var(--accent-success)' : latency < 10000 ? 'var(--accent-warning)' : 'var(--accent-danger)';
  const latStr = latency >= 1000 ? (latency / 1000).toFixed(1) + 's' : Math.round(latency) + 'ms';

  return `
  <div class="ms-card st-${escapeAttr(m.status || '')}">
    <div class="ms-card-header">
      <div class="ms-card-title-group">
        <div class="ms-card-names">
          <span class="ms-model-name" title="${escapeHtml(m.model)}">${escapeHtml(m.display_name || m.model)}</span>
          <span class="ms-model-id">
            <span class="text" title="${escapeHtml(m.model)}">${escapeHtml(m.model)}</span>
            <button class="btn xs" data-copy="${escapeAttr(m.model)}" data-copy-msg="模型ID已复制" title="复制模型ID">${Icons.copy(10)}</button>
          </span>
        </div>
        ${isRecommended ? '<span class="ms-badge ms-badge-rec">推荐</span>' : ''}
        <span class="tag ${statusCls}" style="font-size:9px;padding:1px 5px">${escapeHtml(m.status_label || m.status || '')}</span>
      </div>
      <div class="ms-health">
        <div class="ms-health-bar"><div class="ms-health-fill ${m.health_score >= 80 ? 's' : m.health_score >= 50 ? 'w' : 'd'}" style="width:${m.health_score || 0}%"></div></div>
        <span class="ms-health-score">${m.health_score || 0}</span>
      </div>
    </div>
    <div class="ms-bucket-row">
      ${hasBuckets ? `<span>密钥 ${m.healthy_buckets || 0}/${tb} 健康</span><span class="sep">|</span>` : ''}
      <span>成功率 <strong style="color:${srColor}">${sr}%</strong></span>
      <span class="sep">|</span>
      <span>延迟 <strong style="color:${latColor}">${latStr}</strong></span>
      <span class="sep">|</span>
      <span>1h ${Fmt.number(m.total_requests_1h || 0)}</span>
    </div>
    <div class="ms-metrics">
      <div class="ms-metric"><span class="ms-metric-label">429</span><span class="ms-metric-val" style="color:${c429 > 0 ? 'var(--accent-warning)' : 'var(--text-muted)'}">${Fmt.number(c429)}</span></div>
      ${c5xx > 0 ? `<div class="ms-metric"><span class="ms-metric-label">5xx</span><span class="ms-metric-val" style="color:var(--accent-danger)">${Fmt.number(c5xx)}</span></div>` : ''}
      <div class="ms-metric"><span class="ms-metric-label">今日Token</span><span class="ms-metric-val" style="color:var(--accent-info)">${Fmt.number(m.today_tokens || 0)}</span></div>
      <div class="ms-metric"><span class="ms-metric-label">活跃</span><span class="ms-metric-val">${m.active_users_1h || 0}</span></div>
    </div>
  </div>`;
}
