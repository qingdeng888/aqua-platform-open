// 职责：今日全平台排行榜表格渲染（概览页与统计面板共用）

import { Fmt, escapeHtml } from '../ui.js';

export function renderLeaderboardInto(containerId, data) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!data || !data.length) {
    container.innerHTML = '<div class="empty-state" style="padding:24px"><div class="empty-text">暂无排行数据</div></div>';
    return;
  }
  const medals = ['🥇', '🥈', '🥉'];
  container.innerHTML = `
    <div class="table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th class="numeric" style="width:40px">#</th>
            <th>用户</th>
            <th class="numeric">请求数</th>
            <th class="numeric">Token总量</th>
            <th class="numeric">输入Token</th>
            <th class="numeric">输出Token</th>
            <th class="numeric">平均延迟</th>
            <th class="numeric">模型数</th>
            <th class="numeric">成功率</th>
            <th class="numeric">流式</th>
          </tr>
        </thead>
        <tbody>
          ${data.map((u, i) => {
            const rank = i + 1;
            const medal = rank <= 3 ? medals[rank - 1] : '';
            const successRate = u.total_requests > 0 ? ((u.success_count / u.total_requests) * 100).toFixed(1) : '0.0';
            const rateColor = successRate >= 95 ? 'var(--accent-success)' : successRate >= 80 ? 'var(--accent-warning)' : 'var(--accent-danger)';
            return `<tr>
              <td class="numeric" style="font-size:16px;font-weight:700">${medal || rank}</td>
              <td><strong>${escapeHtml(u.client_name || '未知用户')}</strong></td>
              <td class="numeric"><strong>${Fmt.number(u.total_requests)}</strong></td>
              <td class="numeric">${Fmt.full(u.total_tokens)}</td>
              <td class="numeric">${Fmt.full(u.prompt_tokens)}</td>
              <td class="numeric">${Fmt.full(u.completion_tokens)}</td>
              <td class="numeric">${Fmt.latency(u.avg_latency)}</td>
              <td class="numeric">${u.model_count}</td>
              <td class="numeric"><span style="color:${rateColor}">${successRate}%</span></td>
              <td class="numeric">${u.stream_count}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;
}
