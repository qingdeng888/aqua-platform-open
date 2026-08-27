// 职责：应用入口——初始化主题/外壳/复制委托，注册全部路由并启动 SPA

import { Theme } from './theme.js';
import { Auth } from './auth.js';
import { Router } from './router.js';
import { Toast, bindCopyDelegation } from './ui.js';
import { renderNavbarIcons, renderSidebarIcons, bindLayoutEvents } from './layout.js';

import { renderHome } from './pages/home.js';
import { renderLogin, renderRegister, renderResetPassword } from './pages/auth-pages.js';
import { renderModels, renderConsoleModels } from './pages/models.js';
import { renderDocs, renderConsoleDocs } from './pages/docs.js';
import { renderQQGroups } from './pages/community.js';
import { renderSponsor } from './pages/sponsor.js';
import { renderDisclaimer } from './pages/legal.js';
import { renderOverview } from './pages/overview.js';
import { renderKeys } from './pages/keys.js';
import { renderChat } from './pages/chat.js';
import { renderStats } from './pages/stats.js';
import { renderLogs } from './pages/logs.js';
import { renderFeedback } from './pages/feedback.js';
import { renderModelStatus } from './pages/model-status.js';
import { renderSettings } from './pages/settings.js';

/* ===== 路由注册 ===== */
Router.register('/', renderHome);
Router.register('/login', renderLogin);
Router.register('/register', renderRegister);
Router.register('/reset-password', renderResetPassword);
Router.register('/models', renderModels);
Router.register('/docs', renderDocs);
Router.register('/qq-groups', renderQQGroups);
Router.register('/sponsor', renderSponsor);
Router.register('/legal/disclaimer', renderDisclaimer);
Router.register('/console', renderOverview);
Router.register('/console/keys', renderKeys);
Router.register('/console/chat', renderChat);
Router.register('/console/models', renderConsoleModels);
Router.register('/console/stats', renderStats);
Router.register('/console/docs', renderConsoleDocs);
Router.register('/console/settings', renderSettings);
Router.register('/console/logs', renderLogs);
Router.register('/console/feedback', renderFeedback);
Router.register('/console/model-status', renderModelStatus);

/* ===== 全局错误兜底 ===== */
window.addEventListener('unhandledrejection', (e) => {
  console.error('未捕获Promise错误:', e.reason);
  if (e.reason && e.reason.message && !e.reason.message.includes('未登录')) {
    Toast.error(e.reason.message, 4000);
  }
});

/* ===== 启动 ===== */
document.addEventListener('DOMContentLoaded', async () => {
  Theme.init();
  bindCopyDelegation();   // 全局复制事件委托（data-copy / data-copy-target / data-copy-ref）
  bindLayoutEvents();     // 外壳事件（汉堡菜单/主题/登出/QQ入口/遮罩）
  renderNavbarIcons();
  renderSidebarIcons();
  await Auth.fetchUser(); // 探测登录态后刷新导航按钮
  renderNavbarIcons();
  Router.init();
});
