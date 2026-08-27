// 职责：统一 fetch 封装——JSON/FormData 序列化、错误信息归一化、401 会话过期跳转

export const API = {
  /**
   * 底层请求
   * @param {string} method HTTP 方法
   * @param {string} url 请求地址
   * @param {object|FormData} [data] 请求体（自动 JSON 化）
   * @param {object} [options] 额外 fetch 选项；silent=true 时不触发 401 跳转
   */
  async _request(method, url, data, options = {}) {
    const opts = {
      method,
      headers: {},
      credentials: 'same-origin',
      ...options,
    };
    if (data !== undefined && !(data instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(data);
    } else if (data instanceof FormData) {
      opts.body = data;
    }

    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      throw new Error('网络错误，请检查网络连接');
    }

    if (resp.status === 401) {
      // silent 选项用于初始登录态探测，不触发重定向
      if (!options.silent) {
        const hash = location.hash || '';
        if (!hash.startsWith('#/login') && !hash.startsWith('#/register')) {
          // 仅受保护页面（/console 开头）自动跳转登录，公开页不跳转
          if (hash.startsWith('#/console')) {
            location.hash = '#/login';
          }
        }
      }
      throw new Error('未登录或登录已过期');
    }

    let body = null;
    const text = await resp.text();
    if (text) {
      try { body = JSON.parse(text); } catch (_) { body = text; }
    }

    if (!resp.ok) {
      let msg = '请求失败';
      if (body && typeof body === 'object') {
        if (body.detail) {
          if (typeof body.detail === 'string') msg = body.detail;
          else if (body.detail.message) msg = body.detail.message;
          else msg = JSON.stringify(body.detail);
        } else if (body.error) {
          if (typeof body.error === 'string') msg = body.error;
          else if (body.error.message) msg = body.error.message;
        } else if (body.message) {
          msg = body.message;
        }
      } else if (typeof body === 'string') {
        msg = body;
      }
      throw new Error(msg);
    }
    return body;
  },

  get(url, opts) { return this._request('GET', url, undefined, opts); },
  post(url, data, opts) { return this._request('POST', url, data, opts); },
  put(url, data, opts) { return this._request('PUT', url, data, opts); },
  delete(url, opts) { return this._request('DELETE', url, undefined, opts); },
};

window.API = API;
