// 职责：请求日志页——分页表格、列排序、流式/状态/Token展示、详情弹窗

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { ensureAuth } from '../auth.js';
import { Toast, Loader, emptyState, errorState, Modal, Fmt, escapeHtml, renderPagination, applySort } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

const state = { page: 1, pageSize: 20, total: 0, data: [], sort: { field: 'created_at', order: 'desc' } };

export async function renderLogs() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/logs');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">请求日志</h1>
        <button class="btn sm" id="logs-refresh-btn">${Icons.refresh(14)} 刷新</button>
      </div>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">日志列表</h3>
          <span class="muted" id="logs-total">-</span>
        </div>
        <div id="logs-table"></div>
        <div id="logs-pagination"></div>
      </div>
    </div>
  `;
  document.getElementById('logs-refresh-btn').onclick = () => renderLogs();
  await loadLogs();
}

async function loadLogs() {
  const container = document.getElementById('logs-table');
  if (!container) return;
  Loader.skeleton(container, 6);
  try {
    const result = await API.get(`/api/user/request-logs?page=${state.page}&page_size=${state.pageSize}`);
    state.data = result.data || [];
    state.total = result.total || 0;
    const totalEl = document.getElementById('logs-total');
    if (totalEl) totalEl.textContent = `共 ${Fmt.full(state.total)} 条`;
    renderLogsTable();
    renderPagination(document.getElementById('logs-pagination'), state.total, state.page, state.pageSize, (p) => {
      state.page = p;
      loadLogs();
    });
  } catch (err) {
    container.innerHTML = '';
    container.appendChild(errorState('加载失败：' + err.message, loadLogs));
  }
}

function renderLogsTable() {
  const container = document.getElementById('logs-table');
  if (!container) return;
  const data = applySort(state.data, state.sort.field, state.sort.order);
  if (!data.length) {
    container.innerHTML = emptyState('暂无请求日志');
    return;
  }
  container.innerHTML = `
    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th data-sort="model">模型</th>
            <th data-sort="is_stream">类型</th>
            <th data-sort="status">状态</th>
            <th data-sort="prompt_tokens" class="numeric">输入Token</th>
            <th data-sort="completion_tokens" class="numeric">输出Token</th>
            <th data-sort="total_tokens" class="numeric">总计Token</th>
            <th data-sort="latency_ms" class="numeric">延迟</th>
            <th data-sort="created_at">时间</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          ${data.map((l) => {
            const st = Fmt.status(l.status);
            const latColor = Fmt.latencyColor(l.latency_ms);
            return `<tr>
              <td><code>${escapeHtml(l.model || '-')}</code></td>
              <td>${l.is_stream ? '<span class="tag info">流式</span>' : '<span class="tag">非流式</span>'}</td>
              <td><span class="tag ${st.cls}"><span class="status-dot ${st.cls}"></span>${st.text}</span></td>
              <td class="numeric token-in">${Fmt.number(l.prompt_tokens || 0)}</td>
              <td class="numeric token-out">${Fmt.number(l.completion_tokens || 0)}</td>
              <td class="numeric token-total" title="${escapeHtml(Fmt.full(l.total_tokens || 0))}">${Fmt.number(l.total_tokens || 0)}</td>
              <td class="numeric" style="color:${latColor}">${Fmt.latency(l.latency_ms)}</td>
              <td class="muted">${escapeHtml(Fmt.time(l.created_at))}</td>
              <td><button class="btn xs" data-log-idx="${state.data.indexOf(l)}" title="查看详情">${Icons.eye(12)}</button></td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;

  // 列排序
  container.querySelectorAll('th[data-sort]').forEach((th) => {
    th.classList.toggle('sort-asc', state.sort.field === th.dataset.sort && state.sort.order === 'asc');
    th.classList.toggle('sort-desc', state.sort.field === th.dataset.sort && state.sort.order === 'desc');
    th.onclick = () => {
      const field = th.dataset.sort;
      if (state.sort.field === field) {
        state.sort.order = state.sort.order === 'asc' ? 'desc' : 'asc';
      } else {
        state.sort = { field, order: 'asc' };
      }
      renderLogsTable();
    };
  });

  // 详情弹窗（事件委托）
  container.querySelectorAll('[data-log-idx]').forEach((btn) => {
    btn.onclick = () => showLogDetail(parseInt(btn.dataset.logIdx, 10));
  });
}

function showLogDetail(idx) {
  const l = state.data[idx];
  if (!l) return;
  Modal.show({
    title: '请求详情',
    body: `
      <div class="log-detail">
        <div class="detail-row"><span class="detail-label">模型</span><code>${escapeHtml(l.model || '-')}</code></div>
        <div class="detail-row"><span class="detail-label">类型</span>${l.is_stream ? '流式' : '非流式'}</div>
        <div class="detail-row"><span class="detail-label">状态</span>${escapeHtml(String(l.status || '-'))}</div>
        <div class="detail-row"><span class="detail-label">输入Token</span>${Fmt.full(l.prompt_tokens || 0)}</div>
        <div class="detail-row"><span class="detail-label">输出Token</span>${Fmt.full(l.completion_tokens || 0)}</div>
        <div class="detail-row"><span class="detail-label">总计Token</span>${Fmt.full(l.total_tokens || 0)}</div>
        <div class="detail-row"><span class="detail-label">延迟</span>${Fmt.latency(l.latency_ms)}</div>
        <div class="detail-row"><span class="detail-label">时间</span>${escapeHtml(Fmt.time(l.created_at))}</div>
        ${l.error_msg ? `<div class="detail-row"><span class="detail-label">错误信息</span><pre class="error-pre">${escapeHtml(l.error_msg)}</pre></div>` : ''}
      </div>
    `,
    footer: '<button class="btn primary" id="log-detail-close">关闭</button>',
    onShow: (body, footer) => {
      footer.querySelector('#log-detail-close').onclick = () => Modal.close();
    },
  });
}
