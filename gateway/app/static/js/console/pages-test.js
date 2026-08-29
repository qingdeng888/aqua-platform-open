/* AQUA Gateway 管理控制台 — pages-test.js
 * 模型测试页(mtest)：实时模型列表 + 搜索/全选 + 并发批量连通性测试。
 *
 * 两条通道：
 *  - upstream：调 /gw/admin/model-test/probe，由后端持上游密钥直连 NVIDIA，
 *              浏览器不接触上游密钥；
 *  - gateway ：浏览器持「内置自测密钥」直接 POST /v1/chat/completions，
 *              走完整中转链路（下游认证 → 调度 → 转发 → 落日志）。
 *              该密钥只存在于本模块闭包，不写 localStorage。
 *
 * 批量编排在前端：并发池 + AbortController 中止 + 逐行就地更新，
 * 因此运行期间不整页重渲染（切页即中止）。
 * 所有动态数据经 esc()/textContent 输出。
 */
(function () {
'use strict';

var GW = window.GW, api = GW.api, badge = GW.badge;

// 页面状态（单页局部状态，切页后由 GW.R.mtest 重新初始化）
var TS = {
  models: [],        // [{id, display_name, capabilities, context_length}]
  selected: {},      // id -> true
  results: {},       // id -> {state, ok, status_code, latency_ms, reply, error}
  rows: {},          // id -> <tr>，逐行就地更新
  order: [],         // 本轮测试的模型顺序
  selfKey: '',       // 内置自测密钥明文（仅闭包持有）
  keyPrefix: '',
  running: false,
  abort: null,
  defaultPrompt: '',
};

/* ================= 回复/错误提取（与后端 extract_reply/extract_error 同语义） ================= */
function pickReply(data) {
  if (!data || !data.choices || !data.choices.length) return '';
  var first = data.choices[0] || {};
  var msg = first.message || {};
  var cands = [msg.content, msg.reasoning_content, first.text];
  for (var i = 0; i < cands.length; i++) {
    if (typeof cands[i] === 'string' && cands[i].trim()) return cands[i].trim().slice(0, 300);
  }
  return '';
}

function pickErr(data) {
  if (!data) return '';
  var e = data.error;
  if (e && typeof e === 'object') {
    var m = e.message || e.detail || e.type;
    if (typeof m === 'string' && m.trim()) return m.trim().slice(0, 300);
  }
  if (typeof e === 'string' && e.trim()) return e.trim().slice(0, 300);
  var fields = ['detail', 'message', 'title'];
  for (var i = 0; i < fields.length; i++) {
    var v = data[fields[i]];
    if (typeof v === 'string' && v.trim()) return v.trim().slice(0, 300);
  }
  return '';
}
/* ================= 页面渲染 ================= */
GW.R.mtest = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions(
    '<button class="btn btn-sm" data-act="mtest-refresh">&#128260; 刷新模型</button>' +
    '<button class="btn btn-sm" data-act="mtest-show-key">&#128273; 自测密钥</button>' +
    '<button class="btn btn-sm btn-warning" data-act="mtest-rotate-key">轮换密钥</button>'
  );

  // 进入页面即视为新一轮：清空上轮状态（切页已中止的请求不再回写）
  TS.selected = {}; TS.results = {}; TS.rows = {}; TS.order = [];
  TS.running = false; TS.abort = null;

  try {
    var d = await api('/model-test/models');
    TS.models = (d && d.models) || [];
    TS.defaultPrompt = (d && d.default_prompt) || '';
    c.innerHTML = '';
    c.appendChild(buildControls(d));
    renderModelList('');
    var res = document.createElement('div');
    res.id = 'mtResultWrap';
    res.className = 'mt-12';
    c.appendChild(res);
    bindControls();
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

// 控制区：通道 / 提示词 / 参数 / 搜索 / 模型清单 / 运行按钮（静态 HTML，无数据内插）
function buildControls(d) {
  var wrap = document.createElement('div');
  wrap.className = 'card';
  wrap.innerHTML =
    '<div class="card-title">测试配置</div>' +
    '<div class="mt-grid">' +
      '<div class="form-group"><label>测试通道</label><select id="mtChannel">' +
        '<option value="upstream">直连 NVIDIA 上游（后端代请求）</option>' +
        '<option value="gateway">走本网关中转（内置自测密钥）</option>' +
      '</select></div>' +
      '<div class="form-group"><label>max_tokens（1-512）</label>' +
        '<input type="number" id="mtMaxTokens" value="256" min="1" max="512" step="1"></div>' +
      '<div class="form-group"><label>并发数（1-16）</label>' +
        '<input type="number" id="mtConc" value="3" min="1" max="16" step="1"></div>' +
    '</div>' +
    '<div class="form-group"><label>测试提示词（留空回落默认）</label>' +
      '<textarea id="mtPrompt" rows="2"></textarea></div>' +
    '<div class="filter-bar">' +
      '<input type="text" id="mtSearch" placeholder="搜索模型 ID / 名称…" style="min-width:220px">' +
      '<button class="btn btn-sm" id="mtSelAll">全选</button>' +
      '<button class="btn btn-sm" id="mtSelVisible">选中匹配项</button>' +
      '<button class="btn btn-sm" id="mtSelNone">清空</button>' +
      '<span class="text-sm text-dim" id="mtSelCount"></span>' +
    '</div>' +
    '<div class="mt-models" id="mtModelList"></div>' +
    '<div class="flex gap-8 items-center mt-12">' +
      '<button class="btn btn-primary btn-sm" id="mtRun">开始测试</button>' +
      '<button class="btn btn-sm btn-danger" id="mtStop" disabled>中止</button>' +
      '<span class="text-sm text-dim" id="mtRunInfo"></span>' +
    '</div>';
  // 提示词经 .value 赋值，不进 HTML
  var ta = wrap.querySelector('#mtPrompt');
  ta.value = TS.defaultPrompt;
  var info = wrap.querySelector('#mtRunInfo');
  info.textContent = '共 ' + ((d && d.count) || 0) + ' 个可用模型' +
    (d && d.from_cache ? '（60 秒缓存，可点右上角刷新回源）' : '（刚从上游回源）');
  return wrap;
}
/* ================= 模型清单（搜索 + 勾选） ================= */
function matchModels(kw) {
  kw = (kw || '').trim().toLowerCase();
  if (!kw) return TS.models;
  return TS.models.filter(function (m) {
    return String(m.id).toLowerCase().indexOf(kw) !== -1 ||
      String(m.display_name || '').toLowerCase().indexOf(kw) !== -1;
  });
}

function renderModelList(kw) {
  var box = GW.$('mtModelList');
  if (!box) return;
  var list = matchModels(kw);
  box.textContent = '';
  if (!list.length) {
    var none = document.createElement('div');
    none.className = 'text-sm text-dim';
    none.textContent = '没有匹配的模型';
    box.appendChild(none);
    updateSelCount();
    return;
  }
  list.forEach(function (m) {
    var row = document.createElement('label');
    row.className = 'mt-model';
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.mid = m.id;
    cb.checked = !!TS.selected[m.id];
    var name = document.createElement('span');
    name.className = 'mono';
    name.textContent = m.id;
    var cap = document.createElement('span');
    cap.className = 'cap';
    var caps = (m.capabilities || []).join('/');
    cap.textContent = (m.display_name && m.display_name !== m.id ? m.display_name : '') +
      (caps ? ' · ' + caps : '');
    row.appendChild(cb); row.appendChild(name); row.appendChild(cap);
    box.appendChild(row);
  });
  updateSelCount();
}

function updateSelCount() {
  var el = GW.$('mtSelCount');
  if (!el) return;
  var n = Object.keys(TS.selected).length;
  el.textContent = '已选 ' + n + ' / 共 ' + TS.models.length + ' 个';
}

function setSelection(ids, on) {
  ids.forEach(function (id) {
    if (on) TS.selected[id] = true; else delete TS.selected[id];
  });
  renderModelList(GW.$('mtSearch') ? GW.$('mtSearch').value : '');
}

function bindControls() {
  var search = GW.$('mtSearch');
  search.addEventListener('input', function () { renderModelList(search.value); });
  GW.$('mtModelList').addEventListener('change', function (e) {
    var cb = e.target;
    if (!cb || !cb.dataset || !cb.dataset.mid) return;
    if (cb.checked) TS.selected[cb.dataset.mid] = true; else delete TS.selected[cb.dataset.mid];
    updateSelCount();
  });
  GW.$('mtSelAll').addEventListener('click', function () {
    setSelection(TS.models.map(function (m) { return m.id; }), true);
  });
  GW.$('mtSelVisible').addEventListener('click', function () {
    setSelection(matchModels(search.value).map(function (m) { return m.id; }), true);
  });
  GW.$('mtSelNone').addEventListener('click', function () { setSelection(Object.keys(TS.selected), false); });
  GW.$('mtRun').addEventListener('click', runTests);
  GW.$('mtStop').addEventListener('click', function () {
    if (TS.abort) { TS.abort.abort(); GW.toast('已请求中止，等待在途请求收尾', 'info'); }
  });
}
/* ================= 结果表（就地更新，不整页重渲染） ================= */
function buildResultTable(ids, channel) {
  var wrap = GW.$('mtResultWrap');
  wrap.textContent = '';
  TS.rows = {};

  var stats = document.createElement('div');
  stats.id = 'mtStats';
  wrap.appendChild(stats);

  var title = document.createElement('div');
  title.className = 'section-title';
  title.textContent = '测试结果 · ' + (channel === 'gateway' ? '走本网关中转' : '直连 NVIDIA 上游');
  wrap.appendChild(title);

  var box = document.createElement('div');
  box.className = 'table-wrap card';
  var table = document.createElement('table');
  table.innerHTML = '<thead><tr><th>模型</th><th>结果</th><th>HTTP</th><th>耗时</th>' +
    '<th>回复摘要</th><th>错误</th></tr></thead>';
  var tbody = document.createElement('tbody');
  ids.forEach(function (id) {
    var tr = document.createElement('tr');
    var tdModel = document.createElement('td');
    tdModel.className = 'mono text-sm';
    tdModel.textContent = id;
    tr.appendChild(tdModel);
    ['state', 'code', 'ms', 'reply', 'err'].forEach(function (k) {
      var td = document.createElement('td');
      td.dataset.col = k;
      if (k === 'reply' || k === 'err') td.className = 'text-sm wrap-cell';
      td.textContent = k === 'state' ? '排队中' : '-';
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
    TS.rows[id] = tr;
  });
  table.appendChild(tbody);
  box.appendChild(table);
  wrap.appendChild(box);
  updateStats();
}

function col(tr, k) { return tr.querySelector('[data-col="' + k + '"]'); }

// state: pending / running / ok / fail / abort
function setRow(id, state, r) {
  TS.results[id] = Object.assign({ state: state }, r || {});
  var tr = TS.rows[id];
  if (!tr) return;
  var label = { running: '测试中…', ok: '通过', fail: '失败', abort: '已中止' }[state] || '排队中';
  var color = { ok: 'green', fail: 'red', abort: 'gray', running: 'blue' }[state] || 'gray';
  col(tr, 'state').innerHTML = badge(label, color);
  col(tr, 'code').textContent = (r && r.status_code) ? String(r.status_code) : (state === 'running' ? '…' : '-');
  col(tr, 'ms').textContent = (r && r.latency_ms != null) ? GW.fmtLatency(r.latency_ms) : '-';
  col(tr, 'reply').textContent = (r && r.reply) ? r.reply : '-';
  col(tr, 'err').textContent = (r && r.error) ? r.error : '-';
  if (r && r.error) col(tr, 'err').classList.add('text-danger');
  updateStats();
}

function updateStats() {
  var box = GW.$('mtStats');
  if (!box) return;
  var total = TS.order.length, ok = 0, fail = 0, done = 0, sum = 0, cnt = 0;
  TS.order.forEach(function (id) {
    var r = TS.results[id] || {};
    if (r.state === 'ok') { ok++; done++; } else if (r.state === 'fail' || r.state === 'abort') { fail++; done++; }
    if (r.state === 'ok' && r.latency_ms != null) { sum += r.latency_ms; cnt++; }
  });
  box.textContent = '';
  box.appendChild(GW.statGrid([
    { label: '待测模型', value: total },
    { label: '已完成', value: done, sub: total ? Math.round(done / total * 100) + '%' : '' },
    { label: '通过', value: ok },
    { label: '失败', value: fail },
    { label: '平均延迟', value: cnt ? GW.fmtLatency(Math.round(sum / cnt)) : '-', sub: '仅统计通过项' },
  ]));
}
/* ================= 执行（并发池 + 可中止） ================= */
function clampInt(v, lo, hi, dft) {
  var n = parseInt(v, 10);
  if (isNaN(n)) return dft;
  return Math.max(lo, Math.min(n, hi));
}

async function runTests() {
  if (TS.running) return;
  var ids = TS.models.map(function (m) { return m.id; }).filter(function (id) { return TS.selected[id]; });
  if (!ids.length) { GW.toast('请先勾选要测试的模型', 'error'); return; }

  var channel = GW.$('mtChannel').value;
  var prompt = GW.$('mtPrompt').value;
  var maxTokens = clampInt(GW.$('mtMaxTokens').value, 1, 512, 256);
  var conc = clampInt(GW.$('mtConc').value, 1, 16, 3);

  // gateway 通道需要内置自测密钥：首次运行时惰性索取，仅存闭包
  if (channel === 'gateway' && !TS.selfKey) {
    try {
      var k = await api('/model-test/selftest-key');
      TS.selfKey = k.key; TS.keyPrefix = k.key_prefix;
    } catch (e) { GW.toast('获取自测密钥失败: ' + e.message, 'error'); return; }
  }

  TS.running = true;
  TS.abort = new AbortController();
  TS.order = ids;
  TS.results = {};
  buildResultTable(ids, channel);
  GW.$('mtRun').disabled = true;
  GW.$('mtStop').disabled = false;
  GW.$('mtRunInfo').textContent = '正在测试 ' + ids.length + ' 个模型，并发 ' + conc +
    (channel === 'gateway' ? '（自测密钥 ' + TS.keyPrefix + '）' : '');

  var queue = ids.slice();
  var signal = TS.abort.signal;
  var worker = async function () {
    while (queue.length) {
      // 页面已被切走（core.render 清空了 content）→ 主动收手，别继续打上游
      if (!GW.$('mtResultWrap')) { TS.abort.abort(); return; }
      if (signal.aborted) {
        queue.splice(0).forEach(function (id) { setRow(id, 'abort', { error: '已中止' }); });
        return;
      }
      var id = queue.shift();
      setRow(id, 'running');
      var r;
      try {
        r = channel === 'gateway'
          ? await probeGateway(id, prompt, maxTokens, signal)
          : await probeUpstream(id, prompt, maxTokens, signal);
      } catch (e) {
        if (e && e.name === 'AbortError') { setRow(id, 'abort', { error: '已中止' }); continue; }
        r = { ok: false, status_code: 0, latency_ms: null, reply: '', error: e.message || '请求失败' };
      }
      setRow(id, r.ok ? 'ok' : 'fail', r);
    }
  };
  var pool = [];
  for (var i = 0; i < Math.min(conc, ids.length); i++) pool.push(worker());
  await Promise.all(pool);

  TS.running = false; TS.abort = null;
  var el = GW.$('mtRun');
  if (el) {
    el.disabled = false;
    GW.$('mtStop').disabled = true;
    var okCnt = ids.filter(function (id) { return (TS.results[id] || {}).state === 'ok'; }).length;
    GW.$('mtRunInfo').textContent = '本轮完成：' + okCnt + '/' + ids.length + ' 通过';
    GW.toast('测试完成：' + okCnt + '/' + ids.length + ' 通过', okCnt === ids.length ? 'success' : 'info');
  }
}

// 通道一：后端代请求直连上游（结构化结果由后端给出）
async function probeUpstream(model, prompt, maxTokens, signal) {
  var d = await api('/model-test/probe', {
    method: 'POST', signal: signal,
    body: JSON.stringify({ model: model, prompt: prompt, max_tokens: maxTokens }),
  });
  return {
    ok: !!d.ok, status_code: d.status_code, latency_ms: d.latency_ms,
    reply: d.reply || '', error: d.error || '',
  };
}

// 通道二：浏览器持自测密钥直接打本网关，走完整中转链路（会落请求日志）
async function probeGateway(model, prompt, maxTokens, signal) {
  var body = {
    model: model,
    messages: [{ role: 'user', content: (prompt || '').trim() || TS.defaultPrompt }],
    max_tokens: maxTokens, temperature: 0, stream: false,
  };
  var t0 = Date.now();
  var resp = await fetch('/v1/chat/completions', {
    method: 'POST', signal: signal,
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TS.selfKey },
    body: JSON.stringify(body),
  });
  var data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  var reply = pickReply(data);
  var ok = resp.status === 200 && !!reply;
  return {
    ok: ok, status_code: resp.status, latency_ms: Date.now() - t0, reply: reply,
    error: ok ? '' : (pickErr(data) || 'HTTP ' + resp.status),
  };
}

/* ================= 页面动作 ================= */
GW.actions['mtest-refresh'] = async function () {
  if (TS.running) { GW.toast('测试进行中，请先中止', 'error'); return; }
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  try {
    var d = await api('/model-test/models?refresh=1');
    GW.toast('已回源刷新，' + ((d && d.count) || 0) + ' 个可用模型', 'success');
  } catch (e) {
    GW.toast('刷新失败: ' + e.message, 'error');
  }
  GW.R.mtest();
};

GW.actions['mtest-show-key'] = async function () {
  try {
    var d = await api('/model-test/selftest-key');
    TS.selfKey = d.key; TS.keyPrefix = d.key_prefix;
    GW.secretModal({
      title: '内置自测密钥',
      note: '归属客户 ' + d.client_name + '，' + (d.created ? '刚随机生成并落库' : '复用已有密钥') +
        '。「走本网关中转」通道用它请求 /v1/chat/completions。',
      secret: d.key,
      warn: '它与普通下游密钥完全等价（计入调度、限流与请求日志），仅供控制台自测，请勿外发；疑似泄露请点「轮换密钥」。',
    });
  } catch (e) { GW.toast(e.message, 'error'); }
};

GW.actions['mtest-rotate-key'] = function () {
  GW.confirmModal({
    title: '轮换内置自测密钥',
    body: '旧密钥立即失效（已下发给他处的副本会认证失败），并随机生成一把新的。确定继续？',
    danger: true,
    confirmText: '确定轮换',
    onConfirm: async function () {
      var d = await api('/model-test/selftest-key/rotate', { method: 'POST' });
      TS.selfKey = d.key; TS.keyPrefix = d.key_prefix;
      GW.secretModal({
        title: '新的内置自测密钥',
        note: '旧密钥已删除，新密钥已生效。',
        secret: d.key,
        warn: '仅供控制台自测，请勿外发。',
      });
    },
  });
};

})();
