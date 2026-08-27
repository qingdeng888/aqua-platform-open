// 职责：hash SPA 路由——注册表、动态参数(:param)与查询串解析、页面切换过渡、离开清理、异常兜底

import { Toast } from './ui.js';

export const Router = {
  routes: {},
  current: null,
  _cleanup: null,

  /** 注册路由；handler(params, query) 可返回清理函数（清定时器等），切页时自动调用 */
  register(hash, handler) {
    this.routes[hash] = handler;
  },

  go(path) {
    location.hash = '#' + path;
  },

  init() {
    window.addEventListener('hashchange', () => this.handle());
    this.handle();
  },

  async handle() {
    // 移动端路由切换时自动收起侧边栏
    if (window.innerWidth <= 768) {
      const sidebar = document.getElementById('app-sidebar');
      const overlay = document.getElementById('sidebar-overlay');
      if (sidebar) sidebar.classList.remove('mobile-open');
      if (overlay) overlay.classList.remove('active');
    }

    const hash = location.hash.slice(1) || '/';
    const [path, queryStr] = hash.split('?');
    const query = {};
    if (queryStr) {
      queryStr.split('&').forEach((pair) => {
        const [k, v] = pair.split('=');
        query[decodeURIComponent(k)] = decodeURIComponent(v || '');
      });
    }

    // 精确匹配
    let handler = this.routes[path];
    if (handler) {
      await this._invoke(handler, {}, query, path);
      return;
    }

    // 动态匹配（支持 :param）
    for (const route of Object.keys(this.routes)) {
      if (!route.includes(':')) continue;
      const pattern = route.replace(/:[^/]+/g, '([^/]+)');
      const re = new RegExp('^' + pattern + '$');
      const m = path.match(re);
      if (m) {
        const paramNames = (route.match(/:[^/]+/g) || []).map((p) => p.slice(1));
        const params = {};
        paramNames.forEach((n, i) => { params[n] = m[i]; });
        await this._invoke(this.routes[route], params, query, path);
        return;
      }
    }

    // 未知路由 → 首页
    this.current = { path, query, params: {} };
    location.hash = '#/';
  },

  async _invoke(handler, params, query, path) {
    // 离开上一页前执行其清理函数
    if (typeof this._cleanup === 'function') {
      try { this._cleanup(); } catch (_) {}
      this._cleanup = null;
    }

    const content = document.getElementById('content-area');
    this.current = { path, query, params };

    try {
      const cleanup = await handler(params, query);
      if (typeof cleanup === 'function') this._cleanup = cleanup;
    } catch (e) {
      console.error('路由执行错误:', e);
      Toast.error('页面加载失败：' + (e && e.message ? e.message : '未知错误'));
    }

    // 页面切入过渡 + 滚动到顶
    if (content) {
      content.classList.remove('page-enter');
      void content.offsetWidth; // 重置动画
      content.classList.add('page-enter');
      content.scrollTop = 0;
    }
  },
};

window.Router = Router;
