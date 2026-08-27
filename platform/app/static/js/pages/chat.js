// 职责：AI对话页——历史会话、模型选择、联网/推理开关、SSE流式输出（光标动画+停止按钮）、会话保存

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { ensureAuth } from '../auth.js';
import { Toast, Modal, Loader, emptyState, escapeHtml, escapeAttr, safeUrl } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

const state = {
  history: [],
  currentHistoryId: null,
  messages: [],
  model: 'deepseek-ai/deepseek-v4-flash',
  models: [],
  streaming: false,
  abortController: null,
  webSearch: false,
  showReasoning: true,
};

export async function renderChat() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/chat');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="chat-page">
      <div class="chat-sidebar">
        <div class="chat-sidebar-header">
          <h3>对话历史</h3>
          <button class="btn primary sm" id="chat-new-btn">${Icons.plus(14)} 新建</button>
        </div>
        <div class="chat-history-list" id="chat-history-list"></div>
      </div>
      <div class="chat-main">
        <div class="chat-header">
          <div class="chat-model-select">
            <label for="chat-model">模型</label>
            <select id="chat-model" class="select"></select>
          </div>
          <div class="chat-actions">
            <button class="btn sm ${state.webSearch ? 'primary' : ''}" id="web-search-btn">
              ${Icons.globe(14)} 联网 ${state.webSearch ? '开' : '关'}
            </button>
            <button class="btn sm ${state.showReasoning ? 'primary' : ''}" id="reasoning-btn">
              ${Icons.brain(14)} 推理 ${state.showReasoning ? '开' : '关'}
            </button>
          </div>
        </div>
        <div class="chat-messages" id="chat-messages"></div>
        <div class="chat-input-area">
          <div class="chat-input-wrap">
            <textarea id="chat-input" class="chat-input" placeholder="输入消息... (Enter发送 / Ctrl+Enter换行)" rows="1"></textarea>
            <button class="btn primary" id="chat-send">${Icons.send(16)} 发送</button>
            <button class="btn danger" id="chat-stop" style="display:none">${Icons.stop(16)} 停止</button>
          </div>
          <div class="chat-hints">Enter发送 | Ctrl+Enter换行 | 拖拽上传</div>
          <div class="chat-hints chat-hint-warn">
            <span class="warn-icon">⚠️</span>
            本服务为免费开源性质，不保证稳定性与响应速度，请合理使用
          </div>
        </div>
      </div>
    </div>
  `;

  renderChatEmpty();

  // 模型选择器（过滤非对话类模型）
  try {
    const models = await API.get('/api/chat/models');
    state.models = models || [];
    const sel = document.getElementById('chat-model');
    const chatModels = (models || []).filter((m) => {
      const caps = m.capabilities || [];
      return !caps.includes('嵌入') && !caps.includes('安全') && !caps.includes('OCR') && !caps.includes('翻译') && !caps.includes('语音');
    });
    sel.innerHTML = chatModels.map((m) => {
      const caps = m.capabilities || [];
      const capStr = caps.length ? ' [' + caps.join('/') + ']' : '';
      const label = (m.display_name || m.id) + capStr;
      return `<option value="${escapeAttr(m.id)}" ${m.id === state.model ? 'selected' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
    sel.onchange = () => { state.model = sel.value; };
  } catch (err) {
    Toast.error('加载模型失败：' + err.message);
  }

  // 历史列表
  await loadChatHistory();

  // 顶部操作
  document.getElementById('chat-new-btn').onclick = newChat;
  document.getElementById('web-search-btn').onclick = toggleWebSearch;
  document.getElementById('reasoning-btn').onclick = toggleReasoning;
  document.getElementById('chat-send').onclick = sendMessage;
  document.getElementById('chat-stop').onclick = stopGeneration;

  // 输入框：自适应高度 + Enter 发送
  const input = document.getElementById('chat-input');
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // 推理块折叠（事件委托，替代内联 onclick）
  document.getElementById('chat-messages').addEventListener('click', (e) => {
    const header = e.target.closest('.reasoning-header');
    if (header) header.parentElement.classList.toggle('collapsed');
  });
}

/* ===== 历史会话 ===== */
async function loadChatHistory() {
  const container = document.getElementById('chat-history-list');
  if (!container) return;
  Loader.show(container, '加载中');
  try {
    const list = await API.get('/api/chat/history');
    state.history = list || [];
    if (!list.length) {
      container.innerHTML = emptyState('暂无对话历史', 'chat');
      return;
    }
    container.innerHTML = list.map((h) => `
      <div class="chat-history-item ${h.id === state.currentHistoryId ? 'active' : ''}" data-history-id="${escapeAttr(h.id)}" role="button" tabindex="0">
        <div class="chat-history-title">${escapeHtml(h.title || '未命名对话')}</div>
        <div class="chat-history-meta">
          <span>${escapeHtml(h.model || '')}</span>
          <span>${escapeHtml(fmtTimeAgo(h.updated_at))}</span>
        </div>
        <button class="chat-history-del" data-del-id="${escapeAttr(h.id)}" title="删除对话">${Icons.trash(12)}</button>
      </div>
    `).join('');

    container.querySelectorAll('[data-history-id]').forEach((item) => {
      item.onclick = (e) => {
        if (e.target.closest('[data-del-id]')) return; // 删除按钮已单独处理
        loadChat(item.dataset.historyId);
      };
    });
    container.querySelectorAll('[data-del-id]').forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        deleteChat(btn.dataset.delId);
      };
    });
  } catch (err) {
    container.innerHTML = emptyState('加载失败', 'error');
  }
}

function fmtTimeAgo(dateStr) {
  if (!dateStr) return '-';
  try {
    const d = new Date(dateStr);
    const diff = (Date.now() - d) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    return d.toLocaleDateString('zh-CN');
  } catch (_) { return '-'; }
}

function newChat() {
  state.currentHistoryId = null;
  state.messages = [];
  renderChatEmpty();
  loadChatHistory();
}

async function loadChat(historyId) {
  try {
    const data = await API.get('/api/chat/history/' + historyId);
    state.currentHistoryId = historyId;
    state.messages = data.messages || [];
    state.model = data.model || state.model;
    const sel = document.getElementById('chat-model');
    if (sel) sel.value = state.model;
    renderChatMessages();
    loadChatHistory();
  } catch (err) {
    Toast.error('加载对话失败：' + err.message);
  }
}

async function deleteChat(historyId) {
  const ok = await Modal.confirm({
    title: '删除对话',
    body: '<p>确定删除此对话历史？</p>',
    danger: true,
    okText: '删除',
  });
  if (!ok) return;
  try {
    await API.delete('/api/chat/history/' + historyId);
    Toast.success('已删除');
    if (state.currentHistoryId === historyId) newChat();
    else loadChatHistory();
  } catch (err) {
    Toast.error(err.message);
  }
}

/* ===== 消息渲染 ===== */
function renderChatEmpty() {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  container.innerHTML = `
    <div class="chat-empty">
      <div class="chat-empty-icon">${Icons.chat(48)}</div>
      <h3>开始新对话</h3>
      <p>选择模型，输入消息开始与AI对话</p>
    </div>
  `;
}

function renderChatMessages() {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  if (!state.messages.length) {
    renderChatEmpty();
    return;
  }
  container.innerHTML = state.messages.map((m) => renderMessage(m)).join('');
  container.scrollTop = container.scrollHeight;
}

function renderMessage(m, streaming = false) {
  if (m.role === 'user') {
    return `<div class="message user">
      <div class="message-avatar user">${Icons.user(20)}</div>
      <div class="message-content">
        <div class="message-role">我</div>
        <div class="message-text">${escapeHtml(m.content)}</div>
      </div>
    </div>`;
  }
  const reasoning = m.reasoning_content || m.reasoning || '';
  const content = m.content || '';
  let html = `<div class="message assistant">
    <div class="message-avatar assistant">${Icons.cpu(20)}</div>
    <div class="message-content">`;
  if (reasoning && state.showReasoning) {
    html += `
      <div class="reasoning-block${streaming && content ? ' collapsed' : ''}">
        <div class="reasoning-header" role="button" tabindex="0">
          ${Icons.brain(14)} <span>推理过程</span>
          <span class="reasoning-count">${reasoning.length} 字</span>
          <span class="reasoning-toggle">${Icons.chevronDown(14)}</span>
        </div>
        <div class="reasoning-content">${escapeHtml(reasoning)}</div>
      </div>
    `;
  }
  if (m.search_results && m.search_results.length) {
    html += '<div class="search-results">';
    m.search_results.forEach((r) => {
      html += `<div class="search-result-item">
        <a href="${escapeAttr(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer" class="search-result-title">${Icons.link(12)} ${escapeHtml(r.title)}</a>
        <div class="search-result-snippet">${escapeHtml(r.snippet || r.content || '')}</div>
      </div>`;
    });
    html += '</div>';
  }
  // 流式输出时在文本尾部追加光标
  const cursor = streaming ? '<span class="stream-cursor"></span>' : '';
  html += `<div class="message-text">${escapeHtml(content)}${cursor}</div>`;
  html += '</div></div>';
  return html;
}

/* ===== 开关 ===== */
function toggleWebSearch() {
  state.webSearch = !state.webSearch;
  const btn = document.getElementById('web-search-btn');
  btn.classList.toggle('primary', state.webSearch);
  btn.innerHTML = `${Icons.globe(14)} 联网 ${state.webSearch ? '开' : '关'}`;
}

function toggleReasoning() {
  state.showReasoning = !state.showReasoning;
  const btn = document.getElementById('reasoning-btn');
  btn.classList.toggle('primary', state.showReasoning);
  btn.innerHTML = `${Icons.brain(14)} 推理 ${state.showReasoning ? '开' : '关'}`;
  renderChatMessages();
}

/* ===== 发送与流式接收 ===== */
async function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text || state.streaming) return;
  input.value = '';
  input.style.height = 'auto';

  state.messages.push({ role: 'user', content: text });
  const placeholder = { role: 'assistant', content: '', reasoning: '', streaming: true };
  state.messages.push(placeholder);
  renderChatMessages();

  setStreamingUI(true);
  state.abortController = new AbortController();

  // 流式期间只重渲染"正在输出"的那一条消息（避免整页 innerHTML 逐 token 重建）
  const container = document.getElementById('chat-messages');
  let lastNode = container ? container.lastElementChild : null;
  const updateStreamingNode = () => {
    if (!container) return;
    const tpl = document.createElement('template');
    tpl.innerHTML = renderMessage(placeholder, true).trim();
    const fresh = tpl.content.firstElementChild;
    if (lastNode && lastNode.isConnected) container.replaceChild(fresh, lastNode);
    else container.appendChild(fresh);
    lastNode = fresh;
    // 用户未上滚时自动跟随
    if (container.scrollHeight - container.scrollTop - container.clientHeight < 160) {
      container.scrollTop = container.scrollHeight;
    }
  };

  try {
    const payload = {
      model: state.model,
      messages: state.messages.filter((m) => !m.streaming).map((m) => ({ role: m.role, content: m.content })),
      stream: true,
    };
    if (state.webSearch) payload.web_search = true;

    const resp = await fetch('/api/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(payload),
      signal: state.abortController.signal,
    });
    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(errText || '请求失败');
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const dataStr = line.slice(6).trim();
        if (dataStr === '[DONE]') continue;
        try {
          const chunk = JSON.parse(dataStr);
          // 联网搜索结果
          if (chunk.search_results) {
            placeholder.search_results = chunk.search_results;
            updateStreamingNode();
            continue;
          }
          const delta = chunk.choices?.[0]?.delta;
          if (delta) {
            if (delta.reasoning_content) placeholder.reasoning += delta.reasoning_content;
            if (delta.content) placeholder.content += delta.content;
            updateStreamingNode();
          }
          if (chunk.usage) placeholder.usage = chunk.usage;
        } catch (_) {}
      }
    }

    // 成功完成后保存/更新会话历史（与原实现一致：失败不落库）
    placeholder.streaming = false;
    renderChatMessages();
    try {
      const title = text.slice(0, 30);
      const messagesToSave = state.messages.filter((m) => !m.streaming).map((m) => ({
        role: m.role,
        content: m.content,
        reasoning: m.reasoning || '',
      }));
      if (state.currentHistoryId) {
        await API.put('/api/chat/history/' + state.currentHistoryId, {
          title, model: state.model, messages: messagesToSave,
        });
      } else {
        const result = await API.post('/api/chat/history', {
          title, model: state.model, messages: messagesToSave,
        });
        state.currentHistoryId = result.id;
      }
      loadChatHistory();
    } catch (_) {}
  } catch (err) {
    placeholder.streaming = false;
    if (err && err.name === 'AbortError') {
      placeholder.content += '\n\n[已停止生成]';
    } else {
      placeholder.content += '\n\n[请求失败：' + (err.message || '未知错误') + ']';
    }
    renderChatMessages();
  } finally {
    state.streaming = false;
    state.abortController = null;
    setStreamingUI(false);
  }
}

function setStreamingUI(on) {
  state.streaming = on;
  const send = document.getElementById('chat-send');
  const stop = document.getElementById('chat-stop');
  if (send) send.style.display = on ? 'none' : 'inline-flex';
  if (stop) stop.style.display = on ? 'inline-flex' : 'none';
}

function stopGeneration() {
  if (state.abortController) state.abortController.abort();
}
