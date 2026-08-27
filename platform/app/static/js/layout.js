// 职责：应用外壳——导航栏/侧边栏图标与用户信息、侧边栏显隐与移动端抽屉、菜单高亮、全局事件委托（登出/主题/QQ入口）

import { Icons } from './icons.js';
import { Auth } from './auth.js';
import { Router } from './router.js';
import { Theme } from './theme.js';

/** 顶部导航：Logo + 登录态按钮区 */
export function renderNavbarIcons() {
  const brand = document.getElementById('brand-logo-icon');
  if (brand) brand.innerHTML = Icons.logo(24);
  const auth = Auth.getUser();
  const authArea = document.getElementById('auth-area');
  if (!authArea) return;
  if (auth) {
    authArea.innerHTML = '<a href="#/console" class="btn primary sm">控制台</a>';
  } else {
    authArea.innerHTML = `
      <div class="auth-links">
        <a href="#/login" class="btn ghost sm">登录</a>
        <a href="#/register" class="btn primary sm">注册</a>
      </div>
    `;
  }
}

/** 侧边栏菜单图标（feedback/model-status 为字面 SVG） */
export function renderSidebarIcons() {
  const set = (id, html) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  };
  set('ico-overview', Icons.overview(18));
  set('ico-keys', Icons.key(18));
  set('ico-chat', Icons.chat(18));
  set('ico-models', Icons.models(18));
  set('ico-stats', Icons.stats(18));
  set('ico-logs', Icons.terminal(18));
  set('ico-feedback', Icons.messageAlt(18));
  set('ico-model-status', Icons.activity(18));
  set('ico-docs', Icons.docs(18));
  set('ico-settings', Icons.settings(18));
  set('ico-logout', Icons.logout(18));
  set('ico-qq', Icons.globe(18));
}

/** 侧边栏用户信息（仅登录后） */
export function updateUserInfo() {
  const user = Auth.getUser();
  if (!user) return;
  const name = document.getElementById('sidebar-username');
  const email = document.getElementById('sidebar-useremail');
  const avatar = document.getElementById('sidebar-avatar');
  if (name) name.textContent = user.display_name || user.username || '用户';
  if (email) email.textContent = user.email || '-';
  if (avatar) avatar.innerHTML = Icons.user(24);
}

/** 控制台页显示侧边栏，公开页隐藏 */
export function setSidebarVisible(visible) {
  const sidebar = document.getElementById('app-sidebar');
  const layout = document.getElementById('main-layout');
  if (!sidebar || !layout) return;
  sidebar.style.display = visible ? 'flex' : 'none';
  layout.classList.toggle('with-sidebar', visible);
}

/** 高亮当前菜单（侧边栏 + 顶部导航） */
export function highlightNav(route) {
  document.querySelectorAll('.sidebar-nav .nav-item').forEach((item) => {
    item.classList.toggle('active', item.dataset.route === route);
  });
  document.querySelectorAll('.navbar-links a').forEach((item) => {
    item.classList.toggle('active', item.dataset.route === route);
  });
}

/** 移动端抽屉开关 */
export function toggleMobileSidebar() {
  if (window.innerWidth > 768) return;
  document.getElementById('app-sidebar')?.classList.toggle('mobile-open');
  document.getElementById('sidebar-overlay')?.classList.toggle('active');
}

export function closeMobileSidebar() {
  document.getElementById('app-sidebar')?.classList.remove('mobile-open');
  document.getElementById('sidebar-overlay')?.classList.remove('active');
}

/** 外壳全局事件委托：汉堡菜单/主题/登出/QQ入口/遮罩 */
export function bindLayoutEvents() {
  const hamburger = document.getElementById('hamburger-btn');
  if (hamburger) hamburger.onclick = toggleMobileSidebar;

  const overlay = document.getElementById('sidebar-overlay');
  if (overlay) overlay.onclick = closeMobileSidebar;

  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) themeBtn.onclick = () => Theme.toggle();

  const logoutBtn = document.getElementById('sidebar-logout');
  if (logoutBtn) logoutBtn.onclick = () => Auth.logout();

  const qqLink = document.getElementById('sidebar-qq-link');
  if (qqLink) {
    qqLink.onclick = (e) => {
      e.preventDefault();
      closeMobileSidebar();
      Router.go('/qq-groups');
    };
  }

  // 浮动QQ按钮：移动端点击后收起侧边栏（链接本身仍由 href 驱动）
  const floatBtn = document.getElementById('floating-qq-btn');
  if (floatBtn) floatBtn.onclick = closeMobileSidebar;
}
