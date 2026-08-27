/* AQUA Gateway 管理控制台 — pages-data.js
 * 数据面页面：仪表盘(dash) / 上游密钥(keys) / 下游客户(clients) / 桶监控(buckets)
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
 *  上游密钥 — GET/POST /upstreams, PUT/DELETE /upstreams/{id},
 *             POST /upstreams/{id}/unfreeze, GET /upstreams/{id}/reveal
 * ================================================================ */
GW.R.keys = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-primary btn-sm" data-act="upstream-create">+ 创建密钥</button>');
  try {
    var list = await api('/upstreams');
    GW.cache.keys = list;
    c.innerHTML = '';
    if (!list.length) { c.innerHTML = GW.emptyState('暂无上游密钥'); return; }
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    var html = '<table><thead><tr><th>名称</th><th>密钥前缀</th><th>Provider</th><th>权重</th><th>当前RPM</th><th>成功率</th><th>429</th><th>5xx</th><th>健康分</th><th>状态</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (k, i) {
      var frozen = (k.cooled_buckets ?? 0) > 0 || (k.cooldown_remaining ?? 0) > 0 || (k.isolated_buckets ?? 0) > 0;
      var st = frozen ? badge('冷却中', 'yellow') : badge('正常', 'green');
      html += '<tr>' +
        '<td class="wrap-cell"><strong>' + esc(k.name || '-') + '</strong></td>' +
        '<td class="mono text-sm">' + esc(k.key_prefix || '-') + '</td>' +
        '<td>' + esc(k.provider || '-') + '</td>' +
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

GW.actions['upstream-create'] = function () {
  GW.formModal({
    title: '创建上游密钥',
    fields: [
      { id: 'name', label: '名称', placeholder: '名称' },
      { id: 'api_key', label: 'API Key', placeholder: 'API 密钥' },
      { id: 'provider', label: 'Provider', value: 'nvidia' },
      { id: 'weight', label: '权重', type: 'number', value: 1 },
      { id: 'rpm_limit', label: 'RPM 限制', type: 'number', value: 40 },
      { id: 'switch_threshold', label: '切换阈值', type: 'number', value: 38 },
    ],
    submitText: '创建',
    onSubmit: async function (v) {
      if (!v.name || !v.api_key) throw new Error('名称和 API Key 不能为空');
      await api('/upstreams', {
        method: 'POST',
        body: JSON.stringify({
          name: v.name, api_key: v.api_key, provider: v.provider || 'nvidia',
          weight: parseInt(v.weight, 10) || 1,
          rpm_limit: parseInt(v.rpm_limit, 10) || 40,
          switch_threshold: parseInt(v.switch_threshold, 10) || 38,
        }),
      });
      GW.toast('创建成功', 'success');
      GW.R.keys();
    },
  });
};

GW.actions['upstream-edit'] = function (ds) {
  var k = (GW.cache.keys || [])[Number(ds.idx)] || {};
  // 后端 PUT 契约仅接受 name/weight/rpm_limit/switch_threshold/status（api_key/provider 不可改）
  GW.formModal({
    title: '编辑上游密钥',
    hint: 'API Key 与 Provider 创建后不可修改；如需更换请删除后重建。',
    fields: [
      { id: 'name', label: '名称', value: k.name || '' },
      { id: 'weight', label: '权重', type: 'number', value: k.weight ?? 1 },
      { id: 'rpm_limit', label: 'RPM 限制', type: 'number', value: k.rpm_limit ?? k.rpm ?? 40 },
      { id: 'switch_threshold', label: '切换阈值', type: 'number', value: k.switch_threshold ?? 38 },
    ],
    submitText: '保存',
    onSubmit: async function (v) {
      await api('/upstreams/' + encodeURIComponent(k.id), {
        method: 'PUT',
        body: JSON.stringify({
          name: v.name,
          weight: parseInt(v.weight, 10) || 1,
          rpm_limit: parseInt(v.rpm_limit, 10) || 40,
          switch_threshold: parseInt(v.switch_threshold, 10) || 38,
        }),
      });
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
