// 职责：模型列表页（公开版带刷新按钮 + 控制台版）——搜索/分类过滤、按厂商分组、国产优先、复制模型ID

import { Icons } from '../icons.js';
import { API } from '../api.js';
import { Toast, Loader, emptyState, errorState, escapeHtml, escapeAttr, debounce } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';
import { ensureAuth } from '../auth.js';

// 厂商中文名映射
const PUBLISHER_NAMES = {
  'deepseek-ai': 'DeepSeek', 'z-ai': '智谱AI', 'qwen': '千问Qwen', 'moonshotai': '月之暗面Kimi',
  'minimaxai': 'MiniMax', 'stepfun-ai': '阶跃星辰', 'openai': 'OpenAI', 'meta': 'Meta',
  'mistralai': 'Mistral AI', 'nvidia': 'NVIDIA', 'google': 'Google', 'microsoft': 'Microsoft',
  'ibm': 'IBM', 'bytedance': '字节跳动', 'baai': 'BAAI', 'nv-mistralai': 'NVIDIA-Mistral',
  '01-ai': '零一万物', 'abacusai': 'Abacus.AI', 'writer': 'Writer', 'databricks': 'Databricks',
  'bigcode': 'BigCode', 'ai21labs': 'AI21 Labs', 'aisingapore': 'AI Singapore',
  'adept': 'Adept', 'poolside': 'Poolside', 'sarvamai': 'Sarvam AI',
  'snowflake': 'Snowflake', 'thinkingmachines': 'Thinking Machines',
  'upstage': 'Upstage', 'zyphra': 'Zyphra',
};

// 国产模型排前面
const DOMESTIC_PUBLISHERS = ['deepseek-ai', 'z-ai', 'qwen', 'moonshotai', 'minimaxai', 'stepfun-ai', 'bytedance', '01-ai'];

/** 模型名称映射缓存（统计页等展示友好名用） */
let modelNamesCache = [];

export function getModelNames() { return modelNamesCache; }

/* ===== 公开版（无侧边栏，带刷新按钮） ===== */
export async function renderModels() {
  setSidebarVisible(false);
  highlightNav('/models');
  _renderModelsPage(false);
}

/* ===== 控制台版（有侧边栏） ===== */
export async function renderConsoleModels() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/models');
  _renderModelsPage(true);
}

async function _renderModelsPage(inConsole) {
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">模型列表</h1>
        <div class="toolbar">
          <div class="search-box">
            <span class="search-icon">${Icons.search(16)}</span>
            <input type="text" id="model-search" class="input" placeholder="搜索模型名称或ID...">
          </div>
          <select id="model-filter" class="select" style="width:auto;min-width:120px">
            <option value="all">全部模型</option>
            <option value="chat">对话模型</option>
            <option value="vision">视觉模型</option>
            <option value="embed">嵌入模型</option>
            <option value="code">代码模型</option>
            <option value="safety">安全模型</option>
          </select>
          ${inConsole ? '' : '<button class="btn sm" id="models-refresh-btn" title="刷新模型列表">' + Icons.refresh(14) + ' 刷新</button>'}
        </div>
      </div>
      <div id="models-list"></div>
    </div>
  `;
  Loader.show('#models-list', '加载模型列表中');

  const refreshBtn = document.getElementById('models-refresh-btn');
  if (refreshBtn) {
    refreshBtn.onclick = async () => {
      refreshBtn.disabled = true;
      refreshBtn.innerHTML = Icons.spinner(14) + ' 刷新中';
      try {
        const models = await API.get('/api/chat/models?_=' + Date.now());
        modelNamesCache = models || [];
        window._modelNames = modelNamesCache;
        renderModelsList('models-list', models || []);
        Toast.success('模型列表已更新');
        bindFilters(models);
      } catch (err) {
        Toast.error('刷新失败：' + err.message);
      }
      refreshBtn.disabled = false;
      refreshBtn.innerHTML = Icons.refresh(14) + ' 刷新';
    };
  }

  try {
    const models = await API.get('/api/chat/models');
    modelNamesCache = models || [];
    window._modelNames = modelNamesCache;
    renderModelsList('models-list', models || []);
    bindFilters(models);
  } catch (err) {
    const el = document.getElementById('models-list');
    el.innerHTML = '';
    el.appendChild(errorState('加载失败：' + err.message, () => _renderModelsPage(inConsole)));
  }
}

function bindFilters(models) {
  const searchInput = document.getElementById('model-search');
  const filterSelect = document.getElementById('model-filter');
  if (!searchInput || !filterSelect) return;
  const applyFilters = () => {
    const keyword = searchInput.value.toLowerCase();
    const filter = filterSelect.value;
    const filtered = (models || []).filter((m) => {
      const matchKeyword = !keyword || m.id.toLowerCase().includes(keyword) || (m.display_name || '').toLowerCase().includes(keyword);
      const caps = m.capabilities || [];
      let matchFilter = true;
      if (filter === 'chat') matchFilter = caps.includes('推理') && !caps.includes('嵌入') && !caps.includes('安全');
      else if (filter === 'vision') matchFilter = caps.includes('视觉');
      else if (filter === 'embed') matchFilter = caps.includes('嵌入');
      else if (filter === 'code') matchFilter = caps.includes('代码');
      else if (filter === 'safety') matchFilter = caps.includes('安全');
      return matchKeyword && matchFilter;
    });
    renderModelsList('models-list', filtered);
  };
  searchInput.addEventListener('input', debounce(applyFilters, 200));
  filterSelect.addEventListener('change', applyFilters);
}

export function renderModelsList(containerId, models) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!models.length) {
    container.innerHTML = emptyState('未找到匹配的模型', 'models');
    return;
  }

  // 按厂商分组
  const groups = {};
  models.forEach((m) => {
    const owner = m.owned_by || (m.id.includes('/') ? m.id.split('/')[0] : '其他');
    if (!groups[owner]) groups[owner] = [];
    groups[owner].push(m);
  });

  // 国产优先，其次按模型数量
  const sortedOwners = Object.keys(groups).sort((a, b) => {
    const aDom = DOMESTIC_PUBLISHERS.includes(a) ? 0 : 1;
    const bDom = DOMESTIC_PUBLISHERS.includes(b) ? 0 : 1;
    if (aDom !== bDom) return aDom - bDom;
    return groups[b].length - groups[a].length;
  });

  let html = `<div class="models-summary">共 ${models.length} 个模型，${Object.keys(groups).length} 个厂商</div>`;
  for (const owner of sortedOwners) {
    const list = groups[owner];
    const displayName = PUBLISHER_NAMES[owner] || owner;
    html += `
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">${escapeHtml(displayName)}</h3>
          <span class="muted">${list.length} 个模型</span>
        </div>
        <div class="grid grid-3">
          ${list.map((m) => renderModelCard(m)).join('')}
        </div>
      </div>
    `;
  }
  container.innerHTML = html;
}

function renderModelCard(m) {
  const caps = m.capabilities || [];
  const isDeprecated = caps.includes('已弃用');
  const capTags = caps.filter((c) => c !== '已弃用');
  const ctxLen = m.context_length;
  const ctxStr = ctxLen ? (ctxLen >= 1000000 ? (ctxLen / 1000000) + 'M' : (ctxLen >= 1000 ? (ctxLen / 1000) + 'K' : ctxLen)) : '';
  return `
  <div class="model-card${isDeprecated ? ' deprecated' : ''}">
    <div class="model-card-header">
      <div class="model-card-names">
        <span class="model-card-name" title="${escapeHtml(m.display_name || m.id)}">${escapeHtml(m.display_name || m.id.split('/').pop())}</span>
        <span class="model-card-id" title="${escapeHtml(m.id)}">${escapeHtml(m.id)}</span>
      </div>
      <button class="btn xs" data-copy="${escapeAttr(m.id)}" data-copy-msg="模型ID已复制" title="复制模型ID">${Icons.copy(12)}</button>
      ${isDeprecated ? '<span class="tag cooling">已弃用</span>' : ''}
    </div>
    <div class="model-caps">
      ${capTags.map((c) => {
        let cls = 'info';
        if (c === '视觉') cls = 'warning';
        else if (c === '嵌入') cls = 'success';
        else if (c === '工具调用') cls = 'accent';
        else if (c === '1M上下文') cls = 'info';
        else if (c === '安全') cls = 'danger';
        else if (c === '代码') cls = 'success';
        else if (c === '推理') cls = 'accent';
        return `<span class="tag ${cls}">${escapeHtml(c)}</span>`;
      }).join('')}
      ${ctxStr ? `<span class="tag info">↑${escapeHtml(String(ctxStr))}</span>` : ''}
    </div>
  </div>
  `;
}
