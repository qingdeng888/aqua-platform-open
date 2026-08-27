// 职责：登录态管理（session 内用户缓存、探测 /api/user/profile、登出）与受保护页守卫

import { API } from './api.js';
import { Router } from './router.js';

export const Auth = {
  _user: null,
  async fetchUser() {
    try {
      // silent：未登录时不触发 401 跳转（公开页同样可访问）
      const data = await API.get('/api/user/profile', { silent: true });
      this._user = data;
      return data;
    } catch (e) {
      this._user = null;
      return null;
    }
  },
  getUser() { return this._user; },
  isLoggedIn() { return !!this._user; },
  clear() { this._user = null; },
  async logout() {
    try { await API.post('/api/auth/logout'); } catch (_) {}
    this.clear();
    location.hash = '#/';
  },
};

/** 受保护页守卫：未登录则先探测一次，仍失败跳登录页 */
export async function ensureAuth() {
  if (!Auth.isLoggedIn()) {
    const user = await Auth.fetchUser();
    if (!user) {
      Router.go('/login');
      return false;
    }
  }
  return true;
}

window.Auth = Auth;
