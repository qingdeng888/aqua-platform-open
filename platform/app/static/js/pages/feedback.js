// 职责：问题反馈页——分类/标题/描述提交（5000字计数）、我的反馈列表（状态与管理员回复）

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { ensureAuth } from '../auth.js';
import { Toast, Loader, emptyState, errorState, escapeHtml } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

export async function renderFeedback() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/feedback');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">问题反馈</h1>
        <div class="muted">遇到问题？告诉我们，我们会尽快处理</div>
      </div>
      <div class="card" style="max-width:680px">
        <div class="card-header"><h3 class="card-title">提交反馈</h3></div>
        <form id="feedback-form">
          <div class="form-group">
            <label class="form-label" for="feedback-category">分类</label>
            <select id="feedback-category" name="category" class="select" style="width:auto;min-width:140px">
              <option value="功能异常">功能异常</option>
              <option value="使用建议">使用建议</option>
              <option value="模型问题">模型问题</option>
              <option value="账户问题">账户问题</option>
              <option value="其他">其他</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label" for="feedback-title">标题 <span class="text-danger">*</span></label>
            <input type="text" id="feedback-title" name="title" class="input" placeholder="简短描述您的问题" maxlength="200" required>
          </div>
          <div class="form-group">
            <label class="form-label" for="feedback-content">详细描述 <span class="text-danger">*</span></label>
            <textarea id="feedback-content" name="content" class="input" rows="5" placeholder="请详细描述您遇到的问题或建议..." maxlength="5000" required style="resize:vertical;min-height:100px"></textarea>
            <div class="text-xs text-muted char-count"><span id="feedback-chars">0</span>/5000</div>
          </div>
          <button type="submit" class="btn primary" id="feedback-submit-btn">发送反馈</button>
        </form>
      </div>
      <div class="card" style="max-width:680px">
        <div class="card-header">
          <h3 class="card-title">我的反馈</h3>
          <span id="feedback-count" class="text-muted" style="font-size:12px"></span>
        </div>
        <div id="feedback-list"></div>
      </div>
    </div>
  `;

  const ta = content.querySelector('textarea[name="content"]');
  ta.oninput = () => {
    const counter = document.getElementById('feedback-chars');
    if (counter) counter.textContent = String(ta.value.length);
  };

  document.getElementById('feedback-form').onsubmit = async (e) => {
    e.preventDefault();
    const btn = document.getElementById('feedback-submit-btn');
    btn.disabled = true;
    btn.textContent = '提交中...';
    const fd = new FormData(e.target);
    try {
      await API.post('/api/user/feedback', { category: fd.get('category'), title: fd.get('title'), content: fd.get('content') });
      Toast.success('反馈已提交，感谢您的支持！');
      e.target.reset();
      const counter = document.getElementById('feedback-chars');
      if (counter) counter.textContent = '0';
      loadFeedbackList();
    } catch (err) {
      Toast.error(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '发送反馈';
    }
  };

  loadFeedbackList();
}

async function loadFeedbackList() {
  const el = document.getElementById('feedback-list');
  if (!el) return;
  Loader.show(el, '加载中');
  try {
    const data = await API.get('/api/user/feedback?page=1&page_size=20');
    const countEl = document.getElementById('feedback-count');
    if (countEl) countEl.textContent = '共 ' + (data.total || 0) + ' 条';
    if (!data.data || data.data.length === 0) {
      el.innerHTML = emptyState('暂无反馈记录', 'alert');
      return;
    }
    const statusLabels = { pending: '待处理', processing: '处理中', resolved: '已解决', closed: '已关闭' };
    const statusColors = { pending: 'var(--accent-warning)', processing: 'var(--accent-info)', resolved: 'var(--accent-success)', closed: 'var(--text-muted)' };
    el.innerHTML = data.data.map((f) => {
      const st = statusLabels[f.status] || f.status;
      const sc = statusColors[f.status] || 'var(--text-muted)';
      const time = (f.created_at || '').slice(0, 19).replace('T', ' ');
      return `
        <div class="feedback-item">
          <div class="flex-between mb-2">
            <strong class="feedback-item-title">${escapeHtml(f.title)}</strong>
            <span class="tag" style="color:${sc};font-size:10px">${escapeHtml(st)}</span>
          </div>
          <div class="feedback-item-content">${escapeHtml((f.content || '').slice(0, 200))}${f.content && f.content.length > 200 ? '...' : ''}</div>
          ${f.reply ? `<div class="feedback-reply"><strong>回复：</strong>${escapeHtml(f.reply)}</div>` : ''}
          <div class="feedback-time">${escapeHtml(time)}</div>
        </div>
      `;
    }).join('');
  } catch (err) {
    el.innerHTML = '';
    el.appendChild(errorState('加载失败：' + err.message, loadFeedbackList));
  }
}
