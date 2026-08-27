// 职责：设置页——个人资料（显示名保存）、账户身份与权限展示、账户操作、主题切换

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { Auth, ensureAuth } from '../auth.js';
import { Toast, escapeHtml, escapeAttr } from '../ui.js';
import { Theme } from '../theme.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

export async function renderSettings() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/settings');
  const user = Auth.getUser();
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">设置</h1>
      </div>
      <div class="card">
        <div class="card-header"><h3 class="card-title">个人资料</h3></div>
        <form id="settings-form" style="max-width:480px">
          <div class="form-group">
            <label class="form-label">用户名</label>
            <input type="text" class="input" value="${escapeAttr(user.username)}" disabled>
          </div>
          <div class="form-group">
            <label class="form-label">邮箱</label>
            <input type="email" class="input" value="${escapeAttr(user.email)}" disabled>
          </div>
          <div class="form-group">
            <label class="form-label" for="display-name-input">显示名</label>
            <input type="text" id="display-name-input" name="display_name" class="input" value="${escapeAttr(user.display_name || '')}" placeholder="设置显示名" maxlength="64">
          </div>
          <button type="submit" class="btn primary">${Icons.check(16)} 保存</button>
        </form>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">账户身份与权限</h3></div>
        <div class="settings-special-info">
          <div class="form-group">
            <label class="form-label">用户身份</label>
            <div class="settings-inline">
              <span class="tag ${user.user_type === 'old' ? 'tag-old' : 'tag-new'}">${user.user_type === 'old' ? '老用户' : '新用户'}</span>
              ${user.special_tag ? `<span class="tag tag-special">${escapeHtml(user.special_tag)}</span>` : ''}
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">并发配置</label>
            <div class="settings-inline" style="gap:16px">
              <span class="settings-concurrency">${escapeHtml(String(user.concurrency_limit || 5))} 并发</span>
              <span class="text-muted">当前活跃：<strong style="color:var(--accent-primary)">${user.concurrency_current || 0}</strong></span>
            </div>
          </div>
          ${user.special_reason ? `
          <div class="form-group">
            <label class="form-label">特殊说明</label>
            <div class="settings-note">${escapeHtml(user.special_reason)}</div>
          </div>` : ''}
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">账户操作</h3></div>
        <div class="settings-actions">
          <button class="btn" id="settings-logout-btn">${Icons.logout(16)} 退出登录</button>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">主题</h3></div>
        <div class="theme-settings">
          <button class="btn" id="theme-dark-btn">${Icons.moon(16)} 深色主题</button>
          <button class="btn" id="theme-light-btn">${Icons.sun(16)} 浅色主题</button>
        </div>
      </div>
    </div>
  `;

  document.getElementById('settings-form').onsubmit = async (e) => {
    e.preventDefault();
    const data = new FormData(e.target);
    try {
      await API.put('/api/user/settings', { display_name: data.get('display_name') });
      Toast.success('设置已保存');
      await Auth.fetchUser();
      updateUserInfo();
    } catch (err) {
      Toast.error(err.message);
    }
  };

  document.getElementById('settings-logout-btn').onclick = () => Auth.logout();
  document.getElementById('theme-dark-btn').onclick = () => Theme.set('dark');
  document.getElementById('theme-light-btn').onclick = () => Theme.set('light');
}
