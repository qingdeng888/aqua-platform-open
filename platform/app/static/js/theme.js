// 职责：深色/浅色主题切换与持久化（localStorage aqua_theme）

import { Icons } from './icons.js';

export const Theme = {
  _key: 'aqua_theme',
  get() {
    return localStorage.getItem(this._key) || 'dark';
  },
  set(theme) {
    localStorage.setItem(this._key, theme);
    document.documentElement.setAttribute('data-theme', theme);
    this._updateToggle();
  },
  toggle() {
    this.set(this.get() === 'dark' ? 'light' : 'dark');
  },
  _updateToggle() {
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.innerHTML = this.get() === 'dark' ? Icons.sun(18) : Icons.moon(18);
    }
  },
  init() {
    document.documentElement.setAttribute('data-theme', this.get());
    this._updateToggle();
  },
};

window.Theme = Theme;
