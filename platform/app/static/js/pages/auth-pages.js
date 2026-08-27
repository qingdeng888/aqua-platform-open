// 职责：登录 / 注册（邮箱验证码+行为检测） / 重置密码，以及免责协议确认弹层

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { Auth } from '../auth.js';
import { Router } from '../router.js';
import { Toast, Modal } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

/* ===== 登录页 ===== */
export async function renderLogin() {
  setSidebarVisible(false);
  highlightNav('');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="auth-page">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo">${Icons.logo(40)}</div>
          <h1>欢迎回来</h1>
          <p>登录到 AQUA AI平台</p>
        </div>
        <form id="login-form" class="auth-form">
          <div class="form-group">
            <label class="form-label" for="login-username">用户名 / 邮箱</label>
            <input type="text" id="login-username" name="username" class="input" placeholder="输入用户名或邮箱" required autocomplete="username">
          </div>
          <div class="form-group">
            <label class="form-label" for="login-password">密码</label>
            <div class="input-wrap">
              <input type="password" name="password" id="login-password" class="input" placeholder="输入密码" required autocomplete="current-password">
              <button type="button" class="input-suffix" data-toggle="login-password" aria-label="显示/隐藏密码">${Icons.eye(16)}</button>
            </div>
          </div>
          <button type="submit" class="btn primary block">${Icons.arrowRight(18)} 登录</button>
        </form>
        <div class="auth-footer">
          <a href="#/reset-password">忘记密码？</a>
          <span>·</span>
          <a href="#/register">没有账号？立即注册</a>
          <span>·</span>
          <a href="#/qq-groups" class="link-qq">💬 加入交流群</a>
        </div>
      </div>
    </div>
  `;

  document.getElementById('login-form').onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector('button[type=submit]');
    const data = new FormData(form);
    const payload = { username: data.get('username'), password: data.get('password') };
    btn.disabled = true;
    btn.innerHTML = Icons.spinner(18) + ' 登录中...';
    try {
      await API.post('/api/auth/login', payload);
      Toast.success('登录成功');
      await Auth.fetchUser();
      // 免责协议确认检查（本地标记，与后端协议更新配套）
      if (!localStorage.getItem('disclaimer_ack_20260722')) {
        showDisclaimerNotice();
      }
      Router.go('/console');
    } catch (err) {
      Toast.error(err.message);
      btn.disabled = false;
      btn.innerHTML = Icons.arrowRight(18) + ' 登录';
    }
  };
  bindPasswordToggles(content);
}

/* ===== 注册页 ===== */
export async function renderRegister() {
  setSidebarVisible(false);
  highlightNav('');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="auth-page">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo">${Icons.logo(40)}</div>
          <h1>创建账号</h1>
          <p>注册即可免费使用66+实测可用模型</p>
        </div>
        <form id="register-form" class="auth-form">
          <div class="form-group">
            <label class="form-label" for="reg-username">用户名</label>
            <input type="text" id="reg-username" name="username" class="input" placeholder="设置用户名" required minlength="3" maxlength="32" autocomplete="username">
          </div>
          <div class="form-group">
            <label class="form-label" for="reg-email">邮箱</label>
            <input type="email" id="reg-email" name="email" class="input" placeholder="输入邮箱地址" required autocomplete="email">
          </div>
          <div class="form-group">
            <label class="form-label" for="reg-code">验证码</label>
            <div class="input-group">
              <input type="text" id="reg-code" name="code" class="input" placeholder="输入6位验证码" required maxlength="6" autocomplete="one-time-code">
              <button type="button" class="btn" id="send-code-btn">发送验证码</button>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label" for="reg-password">密码</label>
            <div class="input-wrap">
              <input type="password" name="password" id="reg-password" class="input" placeholder="设置密码" required minlength="8" autocomplete="new-password">
              <button type="button" class="input-suffix" data-toggle="reg-password" aria-label="显示/隐藏密码">${Icons.eye(16)}</button>
            </div>
          </div>
          <div class="form-group">
            <label class="checkbox-label">
              <input type="checkbox" id="agree-disclaimer" required>
              <span>我已阅读并同意 <a href="#/legal/disclaimer" target="_blank" style="color:var(--accent-primary);text-decoration:underline">《AQUA平台免责协议》</a></span>
            </label>
          </div>
          <button type="submit" class="btn primary block">${Icons.arrowRight(18)} 注册</button>
        </form>
        <div class="auth-footer">
          <a href="#/login">已有账号？返回登录</a>
          <span>·</span>
          <a href="#/qq-groups" class="link-qq">💬 加入交流群</a>
        </div>
      </div>
    </div>
  `;

  // 发送验证码（60 秒倒计时，与重置页共用逻辑）；离开页面时停止倒计时
  const stopCountdown = bindSendCodeButton('send-code-btn', () => document.getElementById('reg-email').value.trim(), 'register');

  // 行为检测：收集交互数据替代人机验证（提交时补填停留时长）
  const bd = collectBehaviorData();

  document.getElementById('register-form').onsubmit = async (e) => {
    e.preventDefault();
    const agreeCheck = document.getElementById('agree-disclaimer');
    if (agreeCheck && !agreeCheck.checked) {
      Toast.warn('请先阅读并同意免责协议');
      return;
    }
    const form = e.target;
    const btn = form.querySelector('button[type=submit]');
    const data = new FormData(form);
    bd.time_on_page_ms = Date.now() - bd._start; // 修复：原实现提交前无法填充停留时长（恒为0）
    const { _start, ...behavior } = bd;
    const payload = {
      username: data.get('username'),
      email: data.get('email'),
      code: data.get('code'),
      password: data.get('password'),
      behavior,
    };
    btn.disabled = true;
    btn.innerHTML = Icons.spinner(18) + ' 注册中...';
    try {
      await API.post('/api/auth/register', payload);
      Toast.success('注册成功，已自动登录');
      await Auth.fetchUser();
      updateUserInfo();
      Router.go('/console');
    } catch (err) {
      Toast.error(err.message);
      btn.disabled = false;
      btn.innerHTML = Icons.arrowRight(18) + ' 注册';
    }
  };
  bindPasswordToggles(content);
  return stopCountdown; // 路由离开时清理倒计时
}

/* ===== 密码重置页 ===== */
export async function renderResetPassword() {
  setSidebarVisible(false);
  highlightNav('');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="auth-page">
      <div class="auth-card">
        <div class="auth-header">
          <div class="auth-logo">${Icons.logo(40)}</div>
          <h1>重置密码</h1>
          <p>输入邮箱获取验证码以重置密码</p>
        </div>
        <form id="reset-form" class="auth-form">
          <div class="form-group">
            <label class="form-label" for="reset-email">邮箱</label>
            <input type="email" id="reset-email" name="email" class="input" placeholder="注册时使用的邮箱" required>
          </div>
          <div class="form-group">
            <label class="form-label" for="reset-code">验证码</label>
            <div class="input-group">
              <input type="text" id="reset-code" name="code" class="input" placeholder="6位验证码" required maxlength="6">
              <button type="button" class="btn" id="reset-send-code">发送验证码</button>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label" for="reset-new-password">新密码</label>
            <input type="password" id="reset-new-password" name="new_password" class="input" placeholder="设置新密码" required minlength="8" autocomplete="new-password">
          </div>
          <button type="submit" class="btn primary block">${Icons.check(18)} 重置密码</button>
        </form>
        <div class="auth-footer">
          <a href="#/login">返回登录</a>
          <span>·</span>
          <a href="#/qq-groups" class="link-qq">💬 加入交流群</a>
        </div>
      </div>
    </div>
  `;

  const stopCountdown = bindSendCodeButton('reset-send-code', () => document.getElementById('reset-email').value.trim(), 'reset_password');

  document.getElementById('reset-form').onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector('button[type=submit]');
    const data = new FormData(form);
    const payload = {
      email: data.get('email'),
      code: data.get('code'),
      new_password: data.get('new_password'),
    };
    btn.disabled = true;
    btn.innerHTML = Icons.spinner(18) + ' 处理中...';
    try {
      await API.post('/api/auth/reset-password', payload);
      Toast.success('密码已重置，请使用新密码登录');
      Router.go('/login');
    } catch (err) {
      Toast.error(err.message);
      btn.disabled = false;
      btn.innerHTML = Icons.check(18) + ' 重置密码';
    }
  };
  return stopCountdown;
}

/* ===== 免责协议确认弹层（登录后触发一次） ===== */
export function showDisclaimerNotice() {
  Modal.show({
    title: '📜 免责协议更新通知',
    body: `
      <div class="disclaimer-modal-body">
        AQUA平台已更新<a href="#/legal/disclaimer">《免责协议》</a>，
        请花时间阅读了解你的权利和义务。继续使用本平台即表示你接受更新后的协议。
      </div>
    `,
    footer: '<button class="btn primary block" id="disclaimer-confirm-btn">我已阅读并了解</button>',
    onShow: (body, footer) => {
      footer.querySelector('#disclaimer-confirm-btn').onclick = confirmDisclaimer;
    },
  });
}

function confirmDisclaimer() {
  localStorage.setItem('disclaimer_ack_20260722', '1');
  Modal.close();
  Toast.success('感谢确认');
}

/* ===== 页内辅助 ===== */
function bindPasswordToggles(root) {
  root.querySelectorAll('[data-toggle]').forEach((btn) => {
    btn.onclick = () => {
      const input = document.getElementById(btn.dataset.toggle);
      if (!input) return;
      if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = Icons.eyeOff(16);
      } else {
        input.type = 'password';
        btn.innerHTML = Icons.eye(16);
      }
    };
  });
}

/** 发送验证码按钮：60 秒倒计时；返回停止函数供路由清理 */
function bindSendCodeButton(btnId, getEmail, purpose) {
  const sendBtn = document.getElementById(btnId);
  if (!sendBtn) return () => {};
  let countdown = 0;
  let timer = null;
  sendBtn.onclick = async () => {
    const email = getEmail();
    if (!email) { Toast.warn('请先填写邮箱'); return; }
    if (countdown > 0) return;
    sendBtn.disabled = true;
    sendBtn.textContent = '发送中...';
    try {
      await API.post('/api/auth/send-code', { email, purpose });
      Toast.success('验证码已发送至邮箱');
      countdown = 60;
      timer = setInterval(() => {
        countdown--;
        sendBtn.textContent = countdown + 's';
        if (countdown <= 0) {
          clearInterval(timer);
          timer = null;
          sendBtn.disabled = false;
          sendBtn.textContent = '发送验证码';
        }
      }, 1000);
    } catch (err) {
      Toast.error(err.message);
      sendBtn.disabled = false;
      sendBtn.textContent = '发送验证码';
    }
  };
  return () => { if (timer) clearInterval(timer); };
}

/** 行为检测数据收集（注册反滥用，与后端 behavior 模块配套） */
function collectBehaviorData() {
  const bd = {
    mouse_moves: 0, clicks: 0, scrolls: 0, keyboard_events: 0,
    touch_events: 0, page_visibility: 0,
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    time_on_page_ms: 0,
    _start: Date.now(),
  };
  const opts = { passive: true };
  document.addEventListener('mousemove', () => { bd.mouse_moves++; }, opts);
  document.addEventListener('click', () => { bd.clicks++; }, opts);
  document.addEventListener('scroll', () => { bd.scrolls++; }, opts);
  document.addEventListener('keydown', () => { bd.keyboard_events++; }, opts);
  document.addEventListener('touchstart', () => { bd.touch_events++; }, opts);
  document.addEventListener('visibilitychange', () => { bd.page_visibility++; });
  return bd;
}
