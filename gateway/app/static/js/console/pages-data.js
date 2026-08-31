/* AQUA Gateway 管理控制台 — pages-data.js
 * 数据面页面：仪表盘(dash) / 上游密钥(keys) / 模型管理(models) / 代理池(proxies) /
 *            下游客户(clients) / 桶监控(buckets)
 * 所有动态数据经 esc()/textContent 输出；行操作按钮走 data-act + cache 索引委托。
 */
(function () {
'use strict';

var GW = window.GW, api = GW.api, esc = GW.esc, badge = GW.badge;

/* ================================================================
 *  仪表盘 — GET /gw/admin/dashboard
 * ================================================================ */
GW.R.dash = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-sm" data-act="reload-page">&#128260; 刷新</button>');
  try {
    var d = await api('/dashboard');
    c.innerHTML = '';
    var t = d.today || {};
    var a = d.active || {};
    var ss = d.scheduler || {};

    c.appendChild(GW.statGrid([
      { label: '今日请求', value: GW.fmtNum(t.total_requests) },
      { label: '成功率', value: (t.success_rate ?? '-') + '%' },
      { label: '平均延迟', value: GW.fmtLatency(t.avg_latency_ms) },
      { label: '活跃密钥', value: GW.fmtNum(a.upstream_keys) },
      { label: '活跃客户', value: GW.fmtNum(a.clients) },
      { label: 'Token 用量', value: t.total_tokens != null ? (t.total_tokens / 1e6).toFixed(1) + 'M' : '-' },
      { label: '429 次数', value: GW.fmtNum(t.count_429) },
      { label: '5xx 次数', value: GW.fmtNum(t.count_5xx) },
    ]));

    // 7 天请求趋势
    var tr = d.trend_7d || [];
    if (tr.length) {
      var sec = document.createElement('div');
      sec.className = 'card';
      var st = document.createElement('div'); st.className = 'card-title'; st.textContent = '7 天请求趋势';
      sec.appendChild(st);
      var bars = document.createElement('div');
      bars.className = 'chart-bars';
      var maxV = Math.max.apply(null, tr.map(function (i) { return i.requests || 0; }).concat([1]));
      tr.forEach(function (it) {
        var v = it.requests || 0;
        var bar = document.createElement('div');
        bar.className = 'chart-bar';
        bar.style.height = Math.max(4, (v / maxV) * 100) + 'px';
        var val = document.createElement('div'); val.className = 'chart-bar-val'; val.textContent = v;
        var lab = document.createElement('div'); lab.className = 'chart-bar-label'; lab.textContent = String(it.date || '').slice(5);
        bar.appendChild(val); bar.appendChild(lab);
        bars.appendChild(bar);
      });
      sec.appendChild(bars);
      c.appendChild(sec);
    }

    // Top 模型分布（今日）
    var md = d.model_distribution || [];
    if (md.length) {
      var sec2 = document.createElement('div');
      sec2.className = 'card';
      var st2 = document.createElement('div'); st2.className = 'card-title'; st2.textContent = 'Top 模型分布';
      sec2.appendChild(st2);
      var wrap = document.createElement('div'); wrap.className = 'table-wrap';
      var html = '<table><thead><tr><th>模型</th><th>显示名</th><th>请求数</th></tr></thead><tbody>';
      md.forEach(function (m) {
        html += '<tr><td class="mono text-sm">' + esc(m.model || '-') + '</td><td class="text-sm">' + esc(m.display_name || '-') + '</td><td>' + GW.fmtNum(m.count) + '</td></tr>';
      });
      html += '</tbody></table>';
      wrap.innerHTML = html;
      sec2.appendChild(wrap);
      c.appendChild(sec2);
    }

    // 调度器全局状态
    var sec3 = document.createElement('div');
    sec3.className = 'card';
    var st3 = document.createElement('div'); st3.className = 'card-title'; st3.textContent = '调度器状态';
    sec3.appendChild(st3);
    var grid = document.createElement('div'); grid.className = 'stat-grid';
    [
      { label: '健康密钥数', value: GW.fmtNum(ss.healthy_key_count) },
      { label: '降级模式', value: { html: ss.degraded_mode ? badge('降级中', 'red') : badge('正常', 'green') } },
      { label: '在途请求', value: GW.fmtNum(ss.inflight_requests) },
      { label: '当前 QPS', value: ss.current_qps != null ? ss.current_qps : '-' },
      { label: '内存压力', value: ss.memory_pressure != null ? (ss.memory_pressure * 100).toFixed(0) + '%' : '-' },
    ].forEach(function (it) {
      var card = document.createElement('div'); card.className = 'stat-card';
      var lab = document.createElement('div'); lab.className = 'stat-label'; lab.textContent = it.label;
      var val = document.createElement('div'); val.className = 'stat-value';
      if (it.value && it.value.html) val.innerHTML = it.value.html; else val.textContent = String(it.value);
      card.appendChild(lab); card.appendChild(val); grid.appendChild(card);
    });
    sec3.appendChild(grid);
    c.appendChild(sec3);
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

/* ================================================================
 *  上游密钥 — GET/POST /upstreams, POST /upstreams/bulk,
 *             PUT/DELETE /upstreams/{id},
 *             POST /upstreams/{id}/unfreeze, GET /upstreams/{id}/reveal
 * ================================================================ */
GW.R.keys = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions(
    '<button class="btn btn-primary btn-sm" data-act="upstream-create">+ 创建密钥</button>' +
    '<button class="btn btn-sm" data-act="upstream-bulk-create">&#128203; 批量添加</button>'
  );
  try {
    var list = await api('/upstreams');
    GW.cache.keys = list;
    c.innerHTML = '';
    if (!list.length) { c.innerHTML = GW.emptyState('暂无上游密钥'); return; }
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    var html = '<table><thead><tr><th>名称</th><th>密钥前缀</th><th>Provider</th><th>出网</th><th>权重</th><th>当前RPM</th><th>成功率</th><th>429</th><th>5xx</th><th>健康分</th><th>状态</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (k, i) {
      var frozen = (k.cooled_buckets ?? 0) > 0 || (k.cooldown_remaining ?? 0) > 0 || (k.isolated_buckets ?? 0) > 0;
      var st = frozen ? badge('冷却中', 'yellow') : badge('正常', 'green');
      html += '<tr>' +
        '<td class="wrap-cell"><strong>' + esc(k.name || '-') + '</strong></td>' +
        '<td class="mono text-sm">' + esc(k.key_prefix || '-') + '</td>' +
        '<td>' + esc(k.provider || '-') + '</td>' +
        '<td>' + GW.proxyModeBadge(k.proxy_mode, k.proxy_name) + '</td>' +
        '<td>' + GW.fmtNum(k.weight) + '</td>' +
        '<td>' + GW.fmtNum(k.current_rpm) + '</td>' +
        '<td>' + (k.success_rate ?? '-') + '%</td>' +
        '<td>' + GW.fmtNum(k.total_429) + '</td>' +
        '<td>' + GW.fmtNum(k.total_5xx) + '</td>' +
        '<td>' + (k.avg_health ?? '-') + '</td>' +
        '<td>' + st + '</td>' +
        '<td><div class="cell-actions">' +
        '<button class="btn btn-sm" data-act="upstream-reveal" data-id="' + esc(k.id) + '">明文</button>' +
        '<button class="btn btn-sm" data-act="upstream-edit" data-idx="' + i + '">编辑</button>' +
        (frozen ? '<button class="btn btn-sm btn-warning" data-act="upstream-unfreeze" data-id="' + esc(k.id) + '">解冻</button>' : '') +
        '<button class="btn btn-sm btn-danger" data-act="upstream-delete" data-id="' + esc(k.id) + '" data-idx="' + i + '">删除</button>' +
        '</div></td></tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    c.appendChild(wrap);
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

// 出网模式徽标：direct=直连 / bind=绑定代理 / rotate=代理池轮询
GW.proxyModeBadge = function (mode, proxyName) {
  if (mode === 'bind') return badge('代理: ' + (proxyName || '已失效'), proxyName ? 'blue' : 'red');
  if (mode === 'rotate') return badge('轮询', 'cyan');
  return badge('直连', 'gray');
};

// 代理模式下拉选项（供上游密钥创建/编辑弹窗复用）
function proxyModeOptions(cur) {
  return [
    { value: 'direct', label: '直连（不经代理）', selected: (cur || 'direct') === 'direct' },
    { value: 'bind', label: '绑定指定代理', selected: cur === 'bind' },
    { value: 'rotate', label: '代理池轮询', selected: cur === 'rotate' },
  ];
}

// 活跃代理下拉选项；无可用代理时返回空数组
function proxySelectOptions(list, curId) {
  return (list || []).filter(function (p) { return p.status === 'active'; }).map(function (p) {
    return {
      value: p.id,
      label: p.name + ' (' + p.scheme + '://' + p.host + ':' + p.port + (p.has_auth ? ' 认证' : '') + ')',
      selected: p.id === curId,
    };
  });
}

// 读取代理池列表（失败不阻塞密钥表单，退化为仅直连可选）
async function loadProxyOptions() {
  try {
    var d = await api('/proxies');
    return (d && d.proxies) || [];
  } catch (e) {
    GW.toast('代理池读取失败，本次仅可选直连: ' + e.message, 'error');
    return [];
  }
}

// 表单值 → 出网字段（bind 必须选中具体代理）
function buildProxyPayload(v) {
  var mode = v.proxy_mode || 'direct';
  if (mode === 'bind') {
    if (!v.proxy_id) throw new Error('绑定模式必须选择一个代理，请先在「代理池」添加代理');
    return { proxy_mode: 'bind', proxy_id: v.proxy_id };
  }
  return { proxy_mode: mode, proxy_id: null };
}

// 密钥公共参数字段（单个添加与批量添加共用，避免两处默认值漂移）
function keyParamFields(opts) {
  return [
    { id: 'provider', label: 'Provider', value: 'nvidia' },
    { id: 'weight', label: '权重', type: 'number', value: 1 },
    { id: 'rpm_limit', label: 'RPM 限制', type: 'number', value: 40 },
    { id: 'switch_threshold', label: '切换阈值', type: 'number', value: 38 },
    { id: 'proxy_mode', label: '出网方式', type: 'select', options: proxyModeOptions('direct') },
    { id: 'proxy_id', label: '绑定代理（仅绑定模式生效）', type: 'select',
      options: [{ value: '', label: opts.length ? '— 请选择 —' : '— 代理池为空 —', selected: true }].concat(opts) },
  ];
}

// 表单值 → 公共参数（默认值与后端 Pydantic 默认值保持一致）
function keyParamPayload(v) {
  return {
    provider: v.provider || 'nvidia',
    weight: parseInt(v.weight, 10) || 1,
    rpm_limit: parseInt(v.rpm_limit, 10) || 40,
    switch_threshold: parseInt(v.switch_threshold, 10) || 38,
  };
}

GW.actions['upstream-create'] = async function () {
  var proxies = await loadProxyOptions();
  var opts = proxySelectOptions(proxies, '');
  GW.formModal({
    title: '创建上游密钥',
    hint: opts.length ? '出网方式选「绑定指定代理」时须同时选择代理。' : '代理池当前无活跃代理，出网方式请选「直连」。',
    fields: [
      { id: 'name', label: '名称', placeholder: '名称' },
      { id: 'api_key', label: 'API Key', placeholder: 'API 密钥' },
    ].concat(keyParamFields(opts)),
    submitText: '创建',
    onSubmit: async function (v) {
      if (!v.name || !v.api_key) throw new Error('名称和 API Key 不能为空');
      var body = { name: v.name, api_key: v.api_key };
      Object.assign(body, keyParamPayload(v), buildProxyPayload(v));
      await api('/upstreams', { method: 'POST', body: JSON.stringify(body) });
      GW.toast('创建成功', 'success');
      GW.R.keys();
    },
  });
};

// 批量添加：每行一个密钥，名称由后端按「前缀-序号」自动生成；单个添加路径保持不变
GW.actions['upstream-bulk-create'] = async function () {
  var proxies = await loadProxyOptions();
  var opts = proxySelectOptions(proxies, '');
  GW.formModal({
    title: '批量添加上游密钥',
    hint: '每行一个密钥，空行与 # 开头的注释行忽略；名称自动生成，序号从库内同前缀最大值续排。'
      + '与库内已有密钥或本批内重复的行会被跳过，单次上限 200 行。',
    fields: [
      { id: 'api_keys', label: '密钥列表（每行一个）', type: 'textarea', rows: 10,
        placeholder: 'nvapi-xxxxxxxxxxxx\nnvapi-yyyyyyyyyyyy\n# 以 # 开头的行会被忽略' },
      { id: 'name_prefix', label: '名称前缀（生成 前缀-01、前缀-02…）', value: 'nv' },
    ].concat(keyParamFields(opts)),
    submitText: '批量创建',
    onSubmit: async function (v) {
      if (!v.api_keys) throw new Error('请粘贴至少一行密钥');
      var body = { api_keys: v.api_keys, name_prefix: v.name_prefix || 'nv' };
      Object.assign(body, keyParamPayload(v), buildProxyPayload(v));
      var r = await api('/upstreams/bulk', { method: 'POST', body: JSON.stringify(body) });
      GW.toast(r.message || '批量创建完成', (r.created_count || 0) > 0 ? 'success' : 'error');
      GW.R.keys();
      if ((r.skipped_count || 0) > 0) {
        // 有跳过行时接管弹窗展示逐行结果，让管理员知道漏了哪几行、为什么
        showBulkResult(r);
        return false;
      }
    },
  });
};

// 批量导入结果面板：逐行成功/跳过原因，全部经 textContent 写入（响应不含密钥/密码明文）
// fmtCreated 由调用方给出「已创建」行的文案，上游密钥与代理池共用本面板
function showBulkResult(r, fmtCreated) {
  var box = GW.$('modalContent');
  box.textContent = '';
  var head = document.createElement('div'); head.className = 'modal-header';
  var h3 = document.createElement('h3'); h3.textContent = '批量添加结果';
  var x = document.createElement('button'); x.className = 'modal-close'; x.textContent = '×'; x.dataset.act = 'dismiss-modal';
  head.appendChild(h3); head.appendChild(x);

  var body = document.createElement('div'); body.className = 'modal-body';
  var sum = document.createElement('p'); sum.className = 'mb-8';
  sum.textContent = '成功 ' + (r.created_count || 0) + ' 个，跳过 ' + (r.skipped_count || 0) + ' 个。';
  body.appendChild(sum);

  var section = function (title, lines) {
    if (!lines.length) return;
    var t = document.createElement('div'); t.className = 'card-title mt-12'; t.textContent = title;
    body.appendChild(t);
    lines.forEach(function (text) {
      var row = document.createElement('div'); row.className = 'text-sm'; row.textContent = text;
      body.appendChild(row);
    });
  };
  section('已创建 ' + (r.created_count || 0) + ' 个', (r.created || []).map(fmtCreated || function (it) {
    return '第 ' + it.line + ' 行 → ' + it.name + '（' + it.key_prefix + '）';
  }));
  section('已跳过 ' + (r.skipped_count || 0) + ' 个', (r.skipped || []).map(function (it) {
    return '第 ' + it.line + ' 行：' + it.reason;
  }));

  var foot = document.createElement('div'); foot.className = 'modal-footer';
  var close = document.createElement('button'); close.className = 'btn btn-primary'; close.textContent = '知道了';
  close.dataset.act = 'dismiss-modal';
  foot.appendChild(close);

  box.appendChild(head); box.appendChild(body); box.appendChild(foot);
  GW.$('modalOverlay').classList.add('show');
}

GW.actions['upstream-edit'] = async function (ds) {
  var k = (GW.cache.keys || [])[Number(ds.idx)] || {};
  var proxies = await loadProxyOptions();
  var opts = proxySelectOptions(proxies, k.proxy_id || '');
  // 后端 PUT 契约接受 name/weight/rpm_limit/switch_threshold/status/proxy_mode/proxy_id
  GW.formModal({
    title: '编辑上游密钥',
    hint: 'API Key 与 Provider 创建后不可修改；如需更换请删除后重建。',
    fields: [
      { id: 'name', label: '名称', value: k.name || '' },
      { id: 'weight', label: '权重', type: 'number', value: k.weight ?? 1 },
      { id: 'rpm_limit', label: 'RPM 限制', type: 'number', value: k.rpm_limit ?? k.rpm ?? 40 },
      { id: 'switch_threshold', label: '切换阈值', type: 'number', value: k.switch_threshold ?? 38 },
      { id: 'proxy_mode', label: '出网方式', type: 'select', options: proxyModeOptions(k.proxy_mode) },
      { id: 'proxy_id', label: '绑定代理（仅绑定模式生效）', type: 'select',
        options: [{ value: '', label: opts.length ? '— 请选择 —' : '— 代理池为空 —', selected: !k.proxy_id }].concat(opts) },
    ],
    submitText: '保存',
    onSubmit: async function (v) {
      var body = {
        name: v.name,
        weight: parseInt(v.weight, 10) || 1,
        rpm_limit: parseInt(v.rpm_limit, 10) || 40,
        switch_threshold: parseInt(v.switch_threshold, 10) || 38,
      };
      Object.assign(body, buildProxyPayload(v));
      await api('/upstreams/' + encodeURIComponent(k.id), { method: 'PUT', body: JSON.stringify(body) });
      GW.toast('修改成功', 'success');
      GW.R.keys();
    },
  });
};

// 明文仅在管理员主动点击后请求并展示
GW.actions['upstream-reveal'] = async function (ds) {
  try {
    var r = await api('/upstreams/' + encodeURIComponent(ds.id) + '/reveal');
    var key = r.key || r.api_key || r.apiKey || '';
    GW.secretModal({
      title: '上游密钥明文',
      note: '名称: ' + (r.name || '-') + '　前缀: ' + (r.prefix || '-'),
      secret: key,
      warn: '该操作已写入审计日志，请妥善保管此密钥。',
    });
  } catch (e) { GW.toast(e.message, 'error'); }
};

GW.actions['upstream-unfreeze'] = function (ds) {
  GW.confirmModal({
    title: '确认解冻',
    body: '确定要解冻此密钥的全部冷却桶吗？',
    confirmText: '解冻',
    onConfirm: async function () {
      try {
        var r = await api('/upstreams/' + encodeURIComponent(ds.id) + '/unfreeze', { method: 'POST' });
        GW.toast(r.message || '解冻成功', 'success');
        GW.R.keys();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

GW.actions['upstream-delete'] = function (ds) {
  var k = (GW.cache.keys || [])[Number(ds.idx)] || {};
  GW.confirmModal({
    title: '确认删除',
    body: '确定要删除上游密钥「' + (k.name || ds.id) + '」吗？此操作不可撤销。',
    danger: true,
    onConfirm: async function () {
      try {
        await api('/upstreams/' + encodeURIComponent(ds.id), { method: 'DELETE' });
        GW.toast('删除成功', 'success');
        GW.R.keys();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

/* ================================================================
 *  模型管理 — GET/POST/DELETE /models,
 *             PUT /models/visibility, PUT /models/block-setting,
 *             PUT/DELETE /models/alias
 *  列表 = 上游实时全量 + 覆盖层（隐藏 / 手动补录）+ 别名层（改名）。
 *  搜索在前端即时过滤：不发请求、不重建输入框（焦点与光标位置不丢），
 *  同一个搜索词同时过滤模型表与别名表。
 * ================================================================ */
var mdlSearch = '';   // 搜索词跨重渲染保持

GW.R.models = async function (refresh) {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions(
    '<button class="btn btn-primary btn-sm" data-act="model-add">+ 手动添加模型</button>' +
    '<button class="btn btn-sm" data-act="model-refresh">&#128260; 回源刷新</button>'
  );
  try {
    var d = await api('/models' + (refresh ? '?refresh=1' : ''));
    var rows = d.models || [];
    rows.forEach(function (r, i) { r._i = i; });   // 行动作按原始下标取数，过滤不影响
    GW.cache.models = rows;
    var aliases = d.aliases || [];
    aliases.forEach(function (r, i) { r._i = i; });
    GW.cache.modelAliases = aliases;

    c.innerHTML = '';
    c.appendChild(GW.statGrid([
      { label: '上游实时模型', value: GW.fmtNum(d.upstream_count) },
      { label: '对外可见', value: GW.fmtNum(d.visible_count) },
      { label: '已隐藏', value: GW.fmtNum(d.hidden_count) },
      { label: '手动补录', value: GW.fmtNum(d.manual_count) },
      { label: '已设别名', value: GW.fmtNum(d.alias_count) },
    ]));
    c.appendChild(buildBlockToggle(!!d.block_calls));
    c.appendChild(buildModelFilterBar());
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    wrap.id = 'mdlTableWrap';
    c.appendChild(wrap);
    c.appendChild(buildAliasCard());
    paintModelTable();
    paintAliasTable();
    var inp = GW.$('mdlSearch');
    inp.addEventListener('input', function () {
      mdlSearch = this.value;
      paintModelTable();
      paintAliasTable();
    });
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

/* 开关卡片：隐藏的模型是否同时禁止调用（关=仅列表不显示；开=调用返回 400） */
function buildBlockToggle(on) {
  var card = document.createElement('div');
  card.className = 'card mb-12';
  var head = document.createElement('div');
  head.className = 'flex items-center justify-between';
  var t = document.createElement('span');
  t.className = 'card-title'; t.style.margin = '0';
  t.textContent = '隐藏的模型同时禁止调用';
  var sw = document.createElement('label');
  sw.className = 'switch';
  var cb = document.createElement('input');
  cb.type = 'checkbox'; cb.checked = on; cb.id = 'mdlBlockToggle';
  var track = document.createElement('span'); track.className = 'track';
  sw.appendChild(cb); sw.appendChild(track);
  head.appendChild(t); head.appendChild(sw);
  card.appendChild(head);
  var p = document.createElement('p');
  p.className = 'text-xs text-sec mt-8';
  p.textContent = '关：隐藏的模型只是不在 /v1/models 列出，下游指名调用仍照旧转发。'
    + '开：隐藏的模型被调用时返回 400 model_disabled。';
  card.appendChild(p);
  cb.addEventListener('change', async function () {
    var want = this.checked;
    this.disabled = true;
    try {
      var r = await api('/models/block-setting', {
        method: 'PUT', body: JSON.stringify({ block_calls: want }),
      });
      GW.toast(r.message || '已保存', 'success');
    } catch (e) {
      this.checked = !want;      // 保存失败则回弹，避免界面与后端不一致
      GW.toast(e.message, 'error');
    } finally {
      this.disabled = false;
    }
  });
  return card;
}

/* 筛选栏：搜索框（即时过滤）+ 对当前筛选结果批量隐藏/显示 */
function buildModelFilterBar() {
  var bar = document.createElement('div');
  bar.className = 'filter-bar';
  bar.innerHTML =
    '<input type="text" id="mdlSearch" placeholder="搜索模型 ID / 备注 / 别名…" style="min-width:260px">' +
    '<button class="btn btn-sm" data-act="model-bulk-hide">隐藏筛选结果</button>' +
    '<button class="btn btn-sm" data-act="model-bulk-show">显示筛选结果</button>' +
    '<span class="text-sm text-dim" id="mdlCount"></span>';
  bar.querySelector('#mdlSearch').value = mdlSearch;   // 值经 .value 赋值，不进属性内插
  return bar;
}

// 当前搜索词命中的行（模型 ID、备注或挂在该模型上的别名，子串、不区分大小写）
function matchedModels() {
  var kw = (mdlSearch || '').trim().toLowerCase();
  var rows = GW.cache.models || [];
  if (!kw) return rows;
  return rows.filter(function (r) {
    return String(r.model_id).toLowerCase().indexOf(kw) !== -1 ||
      String(r.remark || '').toLowerCase().indexOf(kw) !== -1 ||
      (r.aliases || []).some(function (a) {
        return String(a).toLowerCase().indexOf(kw) !== -1;
      });
  });
}

/* 只重画表格与计数，搜索框本身不动（焦点不丢） */
function paintModelTable() {
  var wrap = GW.$('mdlTableWrap');
  if (!wrap) return;
  var list = matchedModels();
  var cnt = GW.$('mdlCount');
  if (cnt) cnt.textContent = '匹配 ' + list.length + ' / 共 ' + (GW.cache.models || []).length + ' 个';
  if (!list.length) {
    wrap.innerHTML = '';
    var none = document.createElement('div');
    none.className = 'text-sm text-dim';
    none.style.padding = '12px';
    none.textContent = '没有匹配的模型';
    wrap.appendChild(none);
    return;
  }
  var html = '<table><thead><tr><th>模型 ID</th><th>来源</th><th>状态</th><th>别名</th>' +
    '<th>备注</th><th>更新时间</th><th>操作</th></tr></thead><tbody>';
  list.forEach(function (r) {
    // badge() 内部已 esc()，别名文本不再二次转义
    var al = (r.aliases || []).map(function (a) { return badge(a, 'blue'); }).join(' ');
    html += '<tr>' +
      '<td class="wrap-cell mono text-sm">' + esc(r.model_id) + '</td>' +
      '<td>' + (r.manual ? badge('手动补录', 'blue') : badge('上游', 'gray')) + '</td>' +
      '<td>' + (r.hidden ? badge('已隐藏', 'yellow') : badge('可见', 'green')) + '</td>' +
      '<td class="wrap-cell text-sm">' + (al || '<span class="text-dim">-</span>') + '</td>' +
      '<td class="wrap-cell text-sm">' + esc(r.remark || '-') + '</td>' +
      '<td class="text-sm">' + esc(GW.fmtTime(r.updated_at)) + '</td>' +
      '<td><div class="cell-actions">' +
      '<button class="btn btn-sm" data-act="model-toggle" data-idx="' + r._i + '">' +
        (r.hidden ? '显示' : '隐藏') + '</button>' +
      '<button class="btn btn-sm" data-act="model-alias" data-idx="' + r._i + '">+ 别名</button>' +
      (r.manual ? '<button class="btn btn-sm btn-danger" data-act="model-delete" data-idx="' +
        r._i + '">删除</button>' : '') +
      '</div></td></tr>';
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

// 单个模型隐藏/显示（批量端点传一个 ID）
GW.actions['model-toggle'] = async function (ds) {
  var r = (GW.cache.models || [])[parseInt(ds.idx, 10)];
  if (!r) return;
  try {
    var d = await api('/models/visibility', {
      method: 'PUT',
      body: JSON.stringify({ model_ids: [r.model_id], hidden: !r.hidden }),
    });
    GW.toast(d.message || '已保存', 'success');
    GW.R.models();
  } catch (e) { GW.toast(e.message, 'error'); }
};

// 对当前筛选结果批量隐藏/显示：83 个模型逐个点太慢，按搜索结果整批处理
function bulkVisibility(hidden) {
  var list = matchedModels().filter(function (r) { return !!r.hidden !== hidden; });
  if (!list.length) { GW.toast(hidden ? '筛选结果均已隐藏' : '筛选结果均已可见', 'info'); return; }
  var ids = list.map(function (r) { return r.model_id; });
  GW.confirmModal({
    title: hidden ? '批量隐藏模型' : '批量显示模型',
    body: (hidden ? '将隐藏 ' : '将显示 ') + ids.length + ' 个模型（当前搜索结果）。'
      + (hidden ? '隐藏后不再出现在 /v1/models；是否连调用一起禁掉取决于页面上方的开关。' : ''),
    confirmText: hidden ? '确定隐藏' : '确定显示',
    danger: hidden,
    onConfirm: async function () {
      var d = await api('/models/visibility', {
        method: 'PUT', body: JSON.stringify({ model_ids: ids, hidden: hidden }),
      });
      GW.toast(d.message || '已保存', 'success');
      GW.R.models();
    },
  });
}
GW.actions['model-bulk-hide'] = function () { bulkVisibility(true); };
GW.actions['model-bulk-show'] = function () { bulkVisibility(false); };

// 手动补录：上游 /models 尚未收录但确实可调用的模型
GW.actions['model-add'] = function () {
  GW.formModal({
    title: '手动添加模型',
    hint: '用于上游已上线但 /models 尚未收录的模型 ID。仅允许字母、数字与 . _ : / - 。'
      + '若该 ID 后来出现在上游列表中，此处的补录项会自动让位给上游条目。',
    fields: [
      { id: 'model_id', label: '模型 ID', placeholder: 'publisher/model-name' },
      { id: 'remark', label: '备注（可选）', placeholder: '例如：上游新上线，未进 /models' },
    ],
    submitText: '添加',
    onSubmit: async function (v) {
      if (!v.model_id) throw new Error('模型 ID 不能为空');
      var d = await api('/models', {
        method: 'POST', body: JSON.stringify({ model_id: v.model_id, remark: v.remark || '' }),
      });
      GW.toast(d.message || '添加成功', 'success');
      GW.R.models();
    },
  });
};

// 删除手动补录项（上游自带模型不可删除，只能隐藏）
GW.actions['model-delete'] = function (ds) {
  var r = (GW.cache.models || [])[parseInt(ds.idx, 10)];
  if (!r) return;
  GW.confirmModal({
    title: '删除手动补录模型',
    body: '确定删除手动补录的模型 ' + r.model_id + ' 吗？删除后它不再出现在模型列表中。',
    danger: true,
    onConfirm: async function () {
      var d = await api('/models?model_id=' + encodeURIComponent(r.model_id), { method: 'DELETE' });
      GW.toast(d.message || '删除成功', 'success');
      GW.R.models();
    },
  });
};

GW.actions['model-refresh'] = function () { GW.R.models(true); };

/* ---------------- 模型别名 / 映射 ----------------
 * 别名 = 对外暴露的名字，目标模型 = 上游真实 ID。下游拿到的列表里是别名，
 * 用别名调用会在校验之前被解析回真名；真名一律照旧放行。
 * 「保留原名」= 列表里别名与真名并存；「回写响应」= 响应体的 model 字段也换成别名。
 */
function buildAliasCard() {
  var card = document.createElement('div');
  card.className = 'card mt-12';
  var t = document.createElement('div');
  t.className = 'card-title';
  t.textContent = '模型别名 / 映射';
  card.appendChild(t);
  var p = document.createElement('p');
  p.className = 'text-xs text-sec mb-12';
  p.textContent = '把上游模型改名对外，例如 moonshotai/kimi-k3 → nv/kimi-k3。'
    + '别名与真名撞名会被拒绝；目标模型被隐藏时，其别名也一并不出现在 /v1/models。';
  card.appendChild(p);
  var wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  wrap.id = 'aliasTableWrap';
  card.appendChild(wrap);
  return card;
}

// 别名表复用同一个搜索词（别名 / 目标模型 / 备注）
function matchedAliases() {
  var kw = (mdlSearch || '').trim().toLowerCase();
  var rows = GW.cache.modelAliases || [];
  if (!kw) return rows;
  return rows.filter(function (r) {
    return String(r.alias).toLowerCase().indexOf(kw) !== -1 ||
      String(r.target_model || '').toLowerCase().indexOf(kw) !== -1 ||
      String(r.remark || '').toLowerCase().indexOf(kw) !== -1;
  });
}

function paintAliasTable() {
  var wrap = GW.$('aliasTableWrap');
  if (!wrap) return;
  var list = matchedAliases();
  if (!list.length) {
    wrap.innerHTML = '';
    var none = document.createElement('div');
    none.className = 'text-sm text-dim';
    none.style.padding = '12px';
    none.textContent = (GW.cache.modelAliases || []).length
      ? '没有匹配的别名' : '尚未配置别名。在上方模型表格点「+ 别名」即可为该模型取一个对外名字。';
    wrap.appendChild(none);
    return;
  }
  var html = '<table><thead><tr><th>别名</th><th>目标模型</th><th>显示名</th>' +
    '<th>保留原名</th><th>回写响应</th><th>备注</th><th>操作</th></tr></thead><tbody>';
  list.forEach(function (r) {
    html += '<tr>' +
      '<td class="wrap-cell mono text-sm">' + esc(r.alias) + '</td>' +
      '<td class="wrap-cell mono text-sm">' + esc(r.target_model) + ' ' +
        (r.target_missing ? badge('目标已不存在', 'red') : '') + '</td>' +
      '<td class="wrap-cell text-sm">' + esc(r.display_name || '-') + '</td>' +
      '<td>' + (r.keep_original ? badge('并存', 'blue') : badge('替换', 'gray')) + '</td>' +
      '<td>' + (r.force_mapping ? badge('是', 'green') : badge('否', 'gray')) + '</td>' +
      '<td class="wrap-cell text-sm">' + esc(r.remark || '-') + '</td>' +
      '<td><div class="cell-actions">' +
      '<button class="btn btn-sm" data-act="alias-edit" data-idx="' + r._i + '">编辑</button>' +
      '<button class="btn btn-sm btn-danger" data-act="alias-delete" data-idx="' + r._i +
        '">删除</button>' +
      '</div></td></tr>';
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

/* 新增/编辑共用一个弹窗：布尔用 select 表达（formModal 已支持，不必为 checkbox 改 core.js）。
 * 目标模型在新增时由行决定（不用手输，撞不到拼错），编辑时按原值只读展示。 */
function aliasModal(target, cur) {
  var it = cur || {};
  GW.formModal({
    title: cur ? '编辑别名' : '为模型添加别名',
    hint: '目标模型：' + target + '。别名仅允许字母、数字与 . _ : / - ，'
      + '且不能与任何真实模型 ID 撞名。',
    fields: [
      { id: 'alias', label: '别名（对外名字）', value: it.alias || '',
        placeholder: 'nv/kimi-k3' },
      { id: 'display_name', label: '显示名（可选）', value: it.display_name || '',
        placeholder: '留空则继承目标模型的显示名' },
      { id: 'keep_original', label: '列表里保留原名', type: 'select', options: [
        { value: '0', label: '否 — 别名替换真名（推荐）', selected: !it.keep_original },
        { value: '1', label: '是 — 别名与真名并存', selected: !!it.keep_original },
      ] },
      { id: 'force_mapping', label: '回写响应 model 字段', type: 'select', options: [
        { value: '1', label: '是 — 响应里也是别名（推荐）',
          selected: it.force_mapping === undefined ? true : !!it.force_mapping },
        { value: '0', label: '否 — 响应里保留上游真名',
          selected: it.force_mapping === undefined ? false : !it.force_mapping },
      ] },
      { id: 'remark', label: '备注（可选）', value: it.remark || '' },
    ],
    submitText: cur ? '保存' : '添加',
    onSubmit: async function (v) {
      if (!v.alias) throw new Error('别名不能为空');
      var d = await api('/models/alias', {
        method: 'PUT',
        body: JSON.stringify({
          alias: v.alias, target_model: target,
          display_name: v.display_name || '',
          keep_original: v.keep_original === '1',
          force_mapping: v.force_mapping === '1',
          remark: v.remark || '',
        }),
      });
      GW.toast(d.message || '已保存', 'success');
      GW.R.models();
    },
  });
}

GW.actions['model-alias'] = function (ds) {
  var r = (GW.cache.models || [])[parseInt(ds.idx, 10)];
  if (!r) return;
  aliasModal(r.model_id, null);
};

GW.actions['alias-edit'] = function (ds) {
  var r = (GW.cache.modelAliases || [])[parseInt(ds.idx, 10)];
  if (!r) return;
  aliasModal(r.target_model, r);
};

GW.actions['alias-delete'] = function (ds) {
  var r = (GW.cache.modelAliases || [])[parseInt(ds.idx, 10)];
  if (!r) return;
  GW.confirmModal({
    title: '删除模型别名',
    body: '确定删除别名 ' + r.alias + '（→ ' + r.target_model + '）吗？'
      + '删除后下游只能用真名调用，原先用别名的客户端会收到 400。',
    danger: true,
    onConfirm: async function () {
      var d = await api('/models/alias?alias=' + encodeURIComponent(r.alias), { method: 'DELETE' });
      GW.toast(d.message || '删除成功', 'success');
      GW.R.models();
    },
  });
};

/* ================================================================
 *  代理池 — GET/POST /proxies, POST /proxies/bulk,
 *           PUT/DELETE /proxies/{id}, POST /proxies/{id}/test
 * ================================================================ */
GW.R.proxies = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions(
    '<button class="btn btn-primary btn-sm" data-act="proxy-create">+ 添加代理</button>' +
    '<button class="btn btn-sm" data-act="proxy-bulk-create">&#128203; 批量添加</button>'
  );
  try {
    var d = await api('/proxies');
    var list = (d && d.proxies) || [];
    var rt = (d && d.runtime) || {};
    GW.cache.proxies = list;
    c.innerHTML = '';

    // 活跃代理以库内 status 为准：运行时快照是惰性加载的，冷启动时为 0（snapshot_age=-1），不能当作真值
    var activeCount = list.filter(function (p) { return p.status === 'active'; }).length;
    var snapAge = rt.snapshot_age;
    var snapSub = (snapAge === undefined || snapAge < 0)
      ? '运行时快照未加载（首次选路时惰性建立）'
      : '运行时快照 ' + rt.active_proxies + ' 个 / ' + snapAge + 's 前';

    c.appendChild(GW.statGrid([
      { label: '代理总数', value: list.length },
      { label: '活跃代理', value: activeCount, sub: snapSub },
      { label: '轮询模式密钥', value: (d && d.rotate_keys) || 0 },
      { label: '复用客户端', value: rt.cached_clients || 0, sub: '按 (代理,流式) 维度复用' },
    ]));

    if (!list.length) {
      c.insertAdjacentHTML('beforeend', GW.emptyState('代理池为空，点击右上角「添加代理」录入 SOCKS5 / HTTP 代理（也可「批量添加」粘贴多行）', '🌐'));
      return;
    }

    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    var html = '<table><thead><tr><th>名称</th><th>协议</th><th>地址</th><th>认证</th><th>绑定密钥</th><th>状态</th><th>最近探测</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (p, i) {
      var st = p.status === 'active' ? badge('启用', 'green') : badge('停用', 'gray');
      var auth = p.has_auth ? badge('账号密码', 'blue') : badge('无认证', 'gray');
      var chk = '-';
      if (p.last_check_at) {
        chk = (p.last_check_ok ? badge('通', 'green') : badge('不通', 'red')) +
          ' <span class="text-sm">' + esc(GW.fmtTime(p.last_check_at)) + '</span>';
      }
      html += '<tr>' +
        '<td class="wrap-cell"><strong>' + esc(p.name || '-') + '</strong>' +
        (p.remark ? '<div class="text-sm">' + esc(p.remark) + '</div>' : '') + '</td>' +
        '<td>' + esc(p.scheme || '-') + '</td>' +
        '<td class="mono text-sm">' + esc(p.host || '-') + ':' + esc(p.port) + '</td>' +
        '<td>' + auth + (p.username ? ' <span class="mono text-sm">' + esc(p.username) + '</span>' : '') + '</td>' +
        '<td>' + GW.fmtNum(p.bound_keys || 0) + '</td>' +
        '<td>' + st + '</td>' +
        '<td>' + chk + '</td>' +
        '<td><div class="cell-actions">' +
        '<button class="btn btn-sm" data-act="proxy-test" data-id="' + esc(p.id) + '">测试</button>' +
        '<button class="btn btn-sm" data-act="proxy-edit" data-idx="' + i + '">编辑</button>' +
        '<button class="btn btn-sm" data-act="proxy-toggle" data-idx="' + i + '">' + (p.status === 'active' ? '停用' : '启用') + '</button>' +
        '<button class="btn btn-sm btn-danger" data-act="proxy-delete" data-idx="' + i + '">删除</button>' +
        '</div></td></tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    c.appendChild(wrap);

    var tip = document.createElement('p');
    tip.className = 'form-hint';
    tip.textContent = '代理密码加密存储、永不回显；删除代理会把绑定它的上游密钥自动回退为直连。';
    c.appendChild(tip);
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

function schemeOptions(cur) {
  return ['socks5', 'socks5h', 'http', 'https'].map(function (v) {
    return { value: v, label: v, selected: v === (cur || 'socks5') };
  });
}

GW.actions['proxy-create'] = function () {
  GW.formModal({
    title: '添加代理',
    hint: '无认证代理：用户名与密码留空即可。密码加密存储，之后不再回显。',
    fields: [
      { id: 'name', label: '名称', placeholder: '如 hk-node-1' },
      { id: 'scheme', label: '协议', type: 'select', options: schemeOptions('socks5') },
      { id: 'host', label: '地址', placeholder: 'IP 或域名' },
      { id: 'port', label: '端口', type: 'number', value: 1080 },
      { id: 'username', label: '用户名（可留空）', placeholder: '无认证代理留空' },
      { id: 'password', label: '密码（可留空）', type: 'password', placeholder: '无认证代理留空' },
      { id: 'remark', label: '备注（可选）', placeholder: '用途说明' },
    ],
    submitText: '添加',
    onSubmit: async function (v) {
      if (!v.name || !v.host) throw new Error('名称和地址不能为空');
      var port = parseInt(v.port, 10);
      if (!port || port < 1 || port > 65535) throw new Error('端口需为 1-65535');
      if (v.password && !v.username) throw new Error('填写密码时必须同时填写用户名');
      await api('/proxies', {
        method: 'POST',
        body: JSON.stringify({
          name: v.name, scheme: v.scheme, host: v.host, port: port,
          username: v.username || null, password: v.password || null,
          remark: v.remark || null,
        }),
      });
      GW.toast('添加成功', 'success');
      GW.R.proxies();
    },
  });
};

// 批量添加：每行一个 scheme://[user:pass@]host:port，名称由后端按「前缀-序号」自动生成；
// 单个添加路径保持不变
GW.actions['proxy-bulk-create'] = function () {
  GW.formModal({
    title: '批量添加代理',
    hint: '每行一个代理地址，格式 协议://用户名:密码@地址:端口，无认证则省略凭据部分（如 http://1.2.3.4:8080）。'
      + '协议支持 socks5 / socks5h / http / https；空行与 # 开头的注释行忽略；'
      + '名称自动生成，序号从库内同前缀最大值续排；与库内已有或本批内重复（协议+地址+端口+用户名相同）'
      + '的行会被跳过，单次上限 200 行。密码若含 @ 或 : 请写成 %40 / %3A。',
    fields: [
      { id: 'proxy_urls', label: '代理列表（每行一个）', type: 'textarea', rows: 10,
        placeholder: 'http://user:password@1.2.3.4:8080\nsocks5://5.6.7.8:1080\n# 以 # 开头的行会被忽略' },
      { id: 'name_prefix', label: '名称前缀（生成 前缀-01、前缀-02…）', value: 'px' },
      { id: 'remark', label: '备注（整批共用，可选）', placeholder: '如 供应商A 2026-08 批次' },
    ],
    submitText: '批量添加',
    onSubmit: async function (v) {
      if (!v.proxy_urls) throw new Error('请粘贴至少一行代理地址');
      var r = await api('/proxies/bulk', {
        method: 'POST',
        body: JSON.stringify({
          proxy_urls: v.proxy_urls,
          name_prefix: v.name_prefix || 'px',
          remark: v.remark || null,
        }),
      });
      GW.toast(r.message || '批量添加完成', (r.created_count || 0) > 0 ? 'success' : 'error');
      GW.R.proxies();
      if ((r.skipped_count || 0) > 0) {
        // 有跳过行时接管弹窗展示逐行结果，让管理员知道漏了哪几行、为什么
        showBulkResult(r, function (it) {
          return '第 ' + it.line + ' 行 → ' + it.name + '（' + it.scheme + '://' + it.host + ':' + it.port
            + (it.has_auth ? ' 认证=有' : ' 认证=无') + '）';
        });
        return false;
      }
    },
  });
};

GW.actions['proxy-edit'] = function (ds) {
  var p = (GW.cache.proxies || [])[Number(ds.idx)] || {};
  GW.formModal({
    title: '编辑代理',
    hint: p.has_auth ? '密码留空表示不修改；清空用户名将同时清除已存密码（改为无认证代理）。' : '如需启用认证，请同时填写用户名与密码。',
    fields: [
      { id: 'name', label: '名称', value: p.name || '' },
      { id: 'scheme', label: '协议', type: 'select', options: schemeOptions(p.scheme) },
      { id: 'host', label: '地址', value: p.host || '' },
      { id: 'port', label: '端口', type: 'number', value: p.port ?? 1080 },
      { id: 'username', label: '用户名（清空即取消认证）', value: p.username || '' },
      { id: 'password', label: '密码（留空表示不修改）', type: 'password', placeholder: p.has_auth ? '已设置，留空不改' : '未设置' },
      { id: 'remark', label: '备注', value: p.remark || '' },
    ],
    submitText: '保存',
    onSubmit: async function (v) {
      if (!v.name || !v.host) throw new Error('名称和地址不能为空');
      var port = parseInt(v.port, 10);
      if (!port || port < 1 || port > 65535) throw new Error('端口需为 1-65535');
      var body = {
        name: v.name, scheme: v.scheme, host: v.host, port: port,
        username: v.username, remark: v.remark,
      };
      // 密码字段留空 = 不修改；用户名清空时后端会同步清除已存密码
      if (v.password) body.password = v.password;
      await api('/proxies/' + encodeURIComponent(p.id), { method: 'PUT', body: JSON.stringify(body) });
      GW.toast('修改成功', 'success');
      GW.R.proxies();
    },
  });
};

GW.actions['proxy-toggle'] = function (ds) {
  var p = (GW.cache.proxies || [])[Number(ds.idx)] || {};
  var next = p.status === 'active' ? 'inactive' : 'active';
  GW.confirmModal({
    title: next === 'inactive' ? '确认停用' : '确认启用',
    body: next === 'inactive'
      ? '停用后代理立即退出轮询；绑定该代理的上游密钥将临时回退直连。'
      : '启用后该代理立即参与轮询与绑定生效。',
    confirmText: next === 'inactive' ? '停用' : '启用',
    danger: next === 'inactive',
    onConfirm: async function () {
      try {
        await api('/proxies/' + encodeURIComponent(p.id), { method: 'PUT', body: JSON.stringify({ status: next }) });
        GW.toast('已' + (next === 'inactive' ? '停用' : '启用'), 'success');
        GW.R.proxies();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

GW.actions['proxy-test'] = async function (ds) {
  GW.toast('正在测试连通性...', 'info');
  try {
    var r = await api('/proxies/' + encodeURIComponent(ds.id) + '/test', { method: 'POST' });
    GW.toast((r.ok ? '连通正常: ' : '连通失败: ') + (r.message || ''), r.ok ? 'success' : 'error');
    GW.R.proxies();
  } catch (e) { GW.toast(e.message, 'error'); }
};

GW.actions['proxy-delete'] = function (ds) {
  var p = (GW.cache.proxies || [])[Number(ds.idx)] || {};
  GW.confirmModal({
    title: '确认删除代理',
    body: '确定要删除代理「' + (p.name || p.id) + '」吗？' +
      (p.bound_keys ? '当前有 ' + p.bound_keys + ' 个上游密钥绑定它，删除后将自动回退为直连。' : '此操作不可撤销。'),
    danger: true,
    onConfirm: async function () {
      try {
        var r = await api('/proxies/' + encodeURIComponent(p.id), { method: 'DELETE' });
        GW.toast(r.message || '删除成功', 'success');
        GW.R.proxies();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

/* ================================================================
 *  下游客户 — GET/POST /clients, PUT/DELETE /clients/{id},
 *            GET/POST /clients/{id}/keys, GET /clients/{id}/keys/{kid}/reveal,
 *            PUT/DELETE /clients/{id}/keys/{kid}, GET /clients/{id}/usage
 * ================================================================ */
GW.R.clients = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-primary btn-sm" data-act="client-create">+ 创建客户</button>');
  try {
    var list = await api('/clients');
    GW.cache.clients = list;
    c.innerHTML = '';
    if (!list.length) { c.innerHTML = GW.emptyState('暂无下游客户'); return; }
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    var html = '<table><thead><tr><th>名称</th><th>用户类型</th><th>密钥数</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (cl, i) {
      var st = (cl.status === 'active' || cl.status === undefined) ? badge('活跃', 'green') : badge('停用', 'gray');
      html += '<tr>' +
        '<td class="wrap-cell"><strong>' + esc(cl.name || cl.display_name || '-') + '</strong></td>' +
        '<td>' + esc(cl.user_type || cl.userType || '-') + '</td>' +
        '<td>' + GW.fmtNum(cl.key_count ?? cl.keyCount) + '</td>' +
        '<td>' + st + '</td>' +
        '<td class="text-sm text-dim">' + GW.fmtTime(cl.created_at) + '</td>' +
        '<td><div class="cell-actions">' +
        '<button class="btn btn-sm" data-act="client-keys" data-idx="' + i + '">密钥</button>' +
        '<button class="btn btn-sm" data-act="client-usage" data-idx="' + i + '">用量</button>' +
        '<button class="btn btn-sm" data-act="client-edit" data-idx="' + i + '">编辑</button>' +
        '<button class="btn btn-sm btn-danger" data-act="client-delete" data-idx="' + i + '">删除</button>' +
        '</div></td></tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    c.appendChild(wrap);
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

GW.actions['client-create'] = function () {
  GW.formModal({
    title: '创建下游客户',
    fields: [
      { id: 'name', label: '名称', placeholder: '客户名称' },
      { id: 'user_type', label: '用户类型', type: 'select', options: [
        { value: 'new', label: 'new' },
        { value: 'old', label: 'old' },
      ] },
    ],
    submitText: '创建',
    onSubmit: async function (v) {
      if (!v.name) throw new Error('名称不能为空');
      await api('/clients', { method: 'POST', body: JSON.stringify({ name: v.name, user_type: v.user_type }) });
      GW.toast('创建成功', 'success');
      GW.R.clients();
    },
  });
};

GW.actions['client-edit'] = function (ds) {
  var cl = (GW.cache.clients || [])[Number(ds.idx)] || {};
  GW.formModal({
    title: '编辑客户',
    fields: [
      { id: 'name', label: '名称', value: cl.name || '' },
      { id: 'user_type', label: '用户类型', type: 'select', options: [
        { value: 'new', label: 'new', selected: (cl.user_type || cl.userType) === 'new' },
        { value: 'old', label: 'old', selected: (cl.user_type || cl.userType) !== 'new' },
      ] },
      { id: 'status', label: '状态', type: 'select', options: [
        { value: 'active', label: 'active', selected: (cl.status || 'active') === 'active' },
        { value: 'inactive', label: 'inactive', selected: cl.status === 'inactive' },
      ] },
    ],
    submitText: '保存',
    onSubmit: async function (v) {
      await api('/clients/' + encodeURIComponent(cl.id), {
        method: 'PUT',
        body: JSON.stringify({ name: v.name, user_type: v.user_type, status: v.status }),
      });
      GW.toast('修改成功', 'success');
      GW.R.clients();
    },
  });
};

GW.actions['client-delete'] = function (ds) {
  var cl = (GW.cache.clients || [])[Number(ds.idx)] || {};
  GW.confirmModal({
    title: '确认删除',
    body: '确定要删除客户「' + (cl.name || cl.id) + '」及其全部密钥吗？此操作不可撤销。',
    danger: true,
    onConfirm: async function () {
      try {
        await api('/clients/' + encodeURIComponent(cl.id), { method: 'DELETE' });
        GW.toast('删除成功', 'success');
        GW.R.clients();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

// 客户密钥管理弹窗（含 reveal / 启停 / 删除 / 创建，创建后明文仅显示一次）
GW.actions['client-keys'] = function (ds) {
  var cl = (GW.cache.clients || [])[Number(ds.idx)] || {};
  var clientId = cl.id;
  (async function () {
    try {
      var keys = await api('/clients/' + encodeURIComponent(clientId) + '/keys');
      GW.cache.clientKeys = keys;
      var box = GW.$('modalContent');
      box.textContent = '';

      var head = document.createElement('div'); head.className = 'modal-header';
      var h3 = document.createElement('h3'); h3.textContent = '密钥管理 — ' + (cl.name || clientId);
      var x = document.createElement('button'); x.className = 'modal-close'; x.textContent = '×'; x.dataset.act = 'dismiss-modal';
      head.appendChild(h3); head.appendChild(x);

      var body = document.createElement('div'); body.className = 'modal-body';
      if (keys.length) {
        var wrap = document.createElement('div'); wrap.className = 'table-wrap';
        var html = '<table><thead><tr><th>密钥前缀</th><th>状态</th><th>创建时间</th><th>最后使用</th><th>操作</th></tr></thead><tbody>';
        keys.forEach(function (k, i) {
          var active = k.status === 'active';
          html += '<tr>' +
            '<td class="mono text-sm">' + esc(k.key_prefix || '-') + '</td>' +
            '<td>' + (active ? badge('活跃', 'green') : badge('已停用', 'gray')) + '</td>' +
            '<td class="text-sm text-dim">' + GW.fmtTime(k.created_at) + '</td>' +
            '<td class="text-sm text-dim">' + GW.fmtTime(k.last_used_at) + '</td>' +
            '<td><div class="cell-actions">' +
            '<button class="btn btn-sm" data-act="client-key-reveal" data-idx="' + i + '">明文</button>' +
            (active
              ? '<button class="btn btn-sm btn-warning" data-act="client-key-toggle" data-idx="' + i + '">停用</button>'
              : '<button class="btn btn-sm btn-success" data-act="client-key-toggle" data-idx="' + i + '">启用</button>') +
            '<button class="btn btn-sm btn-danger" data-act="client-key-delete" data-idx="' + i + '">删除</button>' +
            '</div></td></tr>';
        });
        html += '</tbody></table>';
        wrap.innerHTML = html;
        body.appendChild(wrap);
      } else {
        var p = document.createElement('p'); p.className = 'text-sec'; p.textContent = '暂无密钥';
        body.appendChild(p);
      }
      var foot = document.createElement('div'); foot.className = 'modal-footer';
      var create = document.createElement('button'); create.className = 'btn btn-primary'; create.textContent = '创建新密钥';
      var cancel = document.createElement('button'); cancel.className = 'btn'; cancel.textContent = '关闭'; cancel.dataset.act = 'dismiss-modal';
      foot.appendChild(cancel); foot.appendChild(create);

      box.appendChild(head); box.appendChild(body); box.appendChild(foot);
      GW.$('modalOverlay').classList.add('show');

      create.addEventListener('click', async function () {
        try {
          var r = await api('/clients/' + encodeURIComponent(clientId) + '/keys', { method: 'POST', body: JSON.stringify({}) });
          GW.secretModal({
            title: '密钥已创建（仅此一次显示）',
            note: '客户: ' + (cl.name || clientId) + (r.key_name ? '　密钥名: ' + r.key_name : ''),
            secret: r.key || '',
            warn: '完整密钥仅此一次显示，请立即复制保存；关闭后将无法再次查看明文。',
          });
        } catch (e) { GW.toast(e.message, 'error'); }
      });
    } catch (e) { GW.toast(e.message, 'error'); }
  })();
};

GW.actions['client-key-reveal'] = async function (ds) {
  var k = (GW.cache.clientKeys || [])[Number(ds.idx)];
  if (!k) return;
  try {
    var r = await api('/clients/' + encodeURIComponent(k.client_id) + '/keys/' + encodeURIComponent(k.id) + '/reveal');
    GW.secretModal({
      title: '客户密钥明文',
      note: '前缀: ' + (r.prefix || k.key_prefix || '-'),
      secret: r.key || '',
      warn: '该操作已写入审计日志，请妥善保管此密钥。',
    });
  } catch (e) { GW.toast(e.message, 'error'); }
};

// 后端契约：status 仅接受 active | revoked
GW.actions['client-key-toggle'] = function (ds) {
  var k = (GW.cache.clientKeys || [])[Number(ds.idx)];
  if (!k) return;
  var toActive = k.status !== 'active';
  GW.confirmModal({
    title: toActive ? '启用密钥' : '停用密钥',
    body: '确定要' + (toActive ? '启用' : '停用') + '密钥 ' + (k.key_prefix || k.id) + ' 吗？',
    confirmText: toActive ? '启用' : '停用',
    onConfirm: async function () {
      try {
        await api('/clients/' + encodeURIComponent(k.client_id) + '/keys/' + encodeURIComponent(k.id), {
          method: 'PUT',
          body: JSON.stringify({ status: toActive ? 'active' : 'revoked' }),
        });
        GW.toast('密钥已' + (toActive ? '启用' : '停用'), 'success');
        // 重新打开密钥管理弹窗（刷新列表）
        var idx = (GW.cache.clients || []).findIndex(function (c) { return c.id === k.client_id; });
        if (idx >= 0) GW.actions['client-keys']({ idx: String(idx) });
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

GW.actions['client-key-delete'] = function (ds) {
  var k = (GW.cache.clientKeys || [])[Number(ds.idx)];
  if (!k) return;
  GW.confirmModal({
    title: '确认删除',
    body: '确定要删除密钥 ' + (k.key_prefix || k.id) + ' 吗？此操作不可撤销。',
    danger: true,
    onConfirm: async function () {
      try {
        await api('/clients/' + encodeURIComponent(k.client_id) + '/keys/' + encodeURIComponent(k.id), { method: 'DELETE' });
        GW.toast('删除成功', 'success');
        var idx = (GW.cache.clients || []).findIndex(function (c) { return c.id === k.client_id; });
        if (idx >= 0) GW.actions['client-keys']({ idx: String(idx) });
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

// 用量统计（后端口径：今日）
GW.actions['client-usage'] = function (ds) {
  var cl = (GW.cache.clients || [])[Number(ds.idx)] || {};
  (async function () {
    try {
      var u = await api('/clients/' + encodeURIComponent(cl.id) + '/usage');
      var box = GW.$('modalContent');
      box.textContent = '';
      var head = document.createElement('div'); head.className = 'modal-header';
      var h3 = document.createElement('h3'); h3.textContent = '用量统计 — ' + (cl.name || cl.id);
      var x = document.createElement('button'); x.className = 'modal-close'; x.textContent = '×'; x.dataset.act = 'dismiss-modal';
      head.appendChild(h3); head.appendChild(x);
      var body = document.createElement('div'); body.className = 'modal-body';
      body.appendChild(GW.statGrid([
        { label: '今日请求', value: GW.fmtNum(u.total ?? u.total_requests ?? u.totalRequests) },
        { label: '今日成功', value: GW.fmtNum(u.success ?? u.success_count) },
        { label: '今日 Token', value: GW.fmtNum(u.tokens ?? u.token_usage ?? u.tokenUsage) },
      ]));
      var foot = document.createElement('div'); foot.className = 'modal-footer';
      var ok = document.createElement('button'); ok.className = 'btn'; ok.textContent = '关闭'; ok.dataset.act = 'dismiss-modal';
      foot.appendChild(ok);
      box.appendChild(head); box.appendChild(body); box.appendChild(foot);
      GW.$('modalOverlay').classList.add('show');
    } catch (e) { GW.toast(e.message, 'error'); }
  })();
};

/* ================================================================
 *  桶监控 — GET /buckets, POST /buckets/{key_id}/{model}/unfreeze
 * ================================================================ */
GW.R.buckets = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-sm" data-act="reload-page">&#128260; 刷新</button>');
  try {
    var list = await api('/buckets');
    GW.cache.buckets = list;
    c.innerHTML = '';
    if (!list.length) { c.innerHTML = GW.emptyState('暂无桶数据'); return; }
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    var html = '<table><thead><tr><th>Key ID</th><th>模型</th><th>RPM</th><th>阈值</th><th>成功率</th><th>响应时间</th><th>冷却</th><th>健康分</th><th>软繁忙</th><th>隔离</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (b, i) {
      var sb = b.soft_busy ?? false;
      var iso = (b.isolation_remaining ?? 0) > 0;
      var cool = (b.cooldown_remaining ?? 0);
      var rt = b.avg_rt ? (b.avg_rt * 1000).toFixed(0) + 'ms' : '-';
      var canUnfreeze = cool > 0 || iso;
      html += '<tr>' +
        '<td class="mono text-sm">' + esc(b.key_id || '-') + '</td>' +
        '<td class="wrap-cell">' + esc(b.model || '-') + '</td>' +
        '<td>' + GW.fmtNum(b.rpm) + '</td>' +
        '<td>' + (b.threshold ?? '-') + '</td>' +
        '<td>' + (b.success_rate ?? '-') + '%</td>' +
        '<td>' + rt + '</td>' +
        '<td>' + (cool ? cool + 's' : '-') + '</td>' +
        '<td>' + (b.health_score ?? '-') + '</td>' +
        '<td>' + (sb ? badge('繁忙', 'yellow') : badge('正常', 'green')) + '</td>' +
        '<td>' + (iso ? badge('隔离', 'red') : badge('正常', 'green')) + '</td>' +
        '<td>' + (canUnfreeze ? '<button class="btn btn-sm btn-warning" data-act="bucket-unfreeze" data-idx="' + i + '">解冻</button>' : '<span class="text-dim">-</span>') + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    c.appendChild(wrap);
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

GW.actions['bucket-unfreeze'] = function (ds) {
  var b = (GW.cache.buckets || [])[Number(ds.idx)];
  if (!b) return;
  GW.confirmModal({
    title: '确认解冻桶',
    body: '确定要解冻该桶吗？Key: ' + (b.key_id || '-') + '，模型: ' + (b.model || '-'),
    confirmText: '解冻',
    onConfirm: async function () {
      try {
        await api('/buckets/' + encodeURIComponent(b.key_id) + '/' + encodeURIComponent(b.model) + '/unfreeze', { method: 'POST' });
        GW.toast('桶已解冻', 'success');
        GW.R.buckets();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};
})();
