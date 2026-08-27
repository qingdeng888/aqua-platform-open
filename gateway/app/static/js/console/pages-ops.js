/* AQUA Gateway 管理控制台 — pages-ops.js
 * 运维面页面：请求日志(logs) / 算法引擎(algo + 算法详情) / 系统监控(monitor) /
 *            系统配置(config) / 平台令牌(tokens) / 错误码(errors) / 商用检测(commercial)
 * 所有动态数据经 esc()/textContent 输出；行操作按钮走 data-act + cache 索引委托。
 */
(function () {
'use strict';

var GW = window.GW, api = GW.api, esc = GW.esc, badge = GW.badge;

/* ================================================================
 *  请求日志 — GET /request-logs-stats/summary, GET /request-logs,
 *            GET /request-logs/{id}
 * ================================================================ */
GW.R.logs = async function (page) {
  if (!page) page = 1;
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('');
  try {
    // 统计摘要（失败不阻塞日志列表）
    var stats = null;
    try { stats = await api('/request-logs-stats/summary'); } catch (e) { /* ignore */ }

    c.innerHTML = '';

    // ---- 筛选栏（状态保持在 S.logFilters，翻页不丢失） ----
    var bar = document.createElement('div');
    bar.className = 'filter-bar';
    var filters = [
      { key: 'status_code', ph: '状态码', w: '80px' },
      { key: 'http_method', ph: 'HTTP方法', w: '90px' },
      { key: 'model', ph: '模型', w: '140px' },
      { key: 'request_path', ph: '路径', w: '130px' },
      { key: 'client_ip', ph: '客户端IP', w: '130px' },
    ];
    filters.forEach(function (f) {
      var inp = document.createElement('input');
      inp.placeholder = f.ph;
      if (f.w) inp.style.width = f.w;
      inp.value = GW.S.logFilters[f.key] || '';
      inp.dataset.fkey = f.key;
      inp.addEventListener('input', function () { GW.S.logFilters[f.key] = this.value; });
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') GW.R.logs(1); });
      bar.appendChild(inp);
    });
    var searchBtn = document.createElement('button');
    searchBtn.className = 'btn btn-sm btn-primary';
    searchBtn.textContent = '搜索';
    searchBtn.addEventListener('click', function () { GW.R.logs(1); });
    bar.appendChild(searchBtn);
    c.appendChild(bar);

    // ---- 统计摘要 ----
    if (stats) {
      var l = stats.latency || {};
      var st1 = GW.statGrid([
        { label: '总请求', value: GW.fmtNum(stats.total) },
        { label: '成功率', value: (stats.success_rate ?? 0) + '%' },
        { label: '成功/错误/认证失败', value: { html: '<span class="text-success">' + GW.fmtNum(stats.success_count) + '</span>/<span class="text-danger">' + GW.fmtNum(stats.error_count) + '</span>/<span class="text-warning">' + GW.fmtNum(stats.auth_fail_count) + '</span>' } },
        { label: '总 Token 数', value: (stats.total_tokens ?? 0) / 1e6 >= 0.1 ? ((stats.total_tokens ?? 0) / 1e6).toFixed(1) + 'M' : GW.fmtNum(stats.total_tokens) },
        { label: '平均延迟', value: (l.avg_ms ?? 0) + 'ms' },
        { label: 'P95 延迟', value: (l.p95_ms ?? 0) + 'ms' },
        { label: '最大延迟', value: (l.max_ms ?? 0) + 'ms' },
        { label: 'Prompt/Completion', value: ((stats.total_prompt_tokens ?? 0) / 1e3).toFixed(1) + 'K / ' + ((stats.total_completion_tokens ?? 0) / 1e3).toFixed(1) + 'K' },
      ]);
      c.appendChild(st1);

      // 近 24 小时趋势
      var hs = stats.hourly_stats || [];
      if (hs.length) {
        var hc = document.createElement('div');
        hc.className = 'card mb-12';
        var ht = document.createElement('div'); ht.className = 'card-title'; ht.textContent = '近 24 小时请求趋势';
        hc.appendChild(ht);
        var bars = document.createElement('div');
        bars.className = 'chart-bars';
        bars.style.height = '90px';
        var maxH = Math.max.apply(null, hs.map(function (h) { return h.cnt || 0; }).concat([1]));
        hs.forEach(function (h) {
          var hv = h.cnt || 0;
          var col = document.createElement('div');
          col.className = 'chart-hour';
          col.title = h.hour + ':00 — ' + hv + ' 次';
          var bar = document.createElement('div');
          bar.className = 'bar';
          bar.style.height = Math.max(4, (hv / maxH) * 60) + 'px';
          var lab = document.createElement('div');
          lab.className = 'hl';
          lab.textContent = String(h.hour || '').slice(-2) + '时';
          col.appendChild(bar); col.appendChild(lab);
          bars.appendChild(col);
        });
        hc.appendChild(bars);
        c.appendChild(hc);
      }

      // Top 榜（路径/模型/客户端/IP/错误）+ 最近错误
      var tops = [
        { title: 'Top 请求路径', rows: stats.top_paths, cols: ['request_path', 'cnt'], headers: ['路径', '次数'] },
        { title: 'Top 模型', rows: stats.top_models, cols: ['model', 'cnt', 'total_tokens', 'avg_latency'], headers: ['模型', '请求', 'Token', '延迟'] },
        { title: 'Top 客户端', rows: stats.top_clients, cols: ['client_display', 'cnt', 'total_tokens', 'avg_latency'], headers: ['客户端', '请求', 'Token', '延迟'] },
        { title: 'Top 请求 IP', rows: stats.top_ips, cols: ['client_ip', 'cnt'], headers: ['IP', '次数'] },
        { title: 'Top 错误类型', rows: stats.top_errors, cols: ['error_type', 'business_code', 'cnt'], headers: ['类型', '业务码', '次数'] },
      ];
      var haveTop = tops.some(function (t) { return t.rows && t.rows.length; });
      if (haveTop) {
        var grid = document.createElement('div');
        grid.className = 'grid-2';
        tops.forEach(function (t) {
          if (!t.rows || !t.rows.length) return;
          var card = document.createElement('div');
          card.className = 'card';
          var title = document.createElement('div'); title.className = 'card-title'; title.textContent = t.title;
          card.appendChild(title);
          var wrap = document.createElement('div'); wrap.className = 'table-wrap';
          var html = '<table><thead><tr>';
          t.headers.forEach(function (h) { html += '<th>' + esc(h) + '</th>'; });
          html += '</tr></thead><tbody>';
          t.rows.forEach(function (r) {
            html += '<tr>';
            t.cols.forEach(function (col, ci) {
              var v = r[col];
              if (col === 'total_tokens') v = ((v ?? 0) / 1e6).toFixed(1) + 'M';
              if (col === 'avg_latency') v = v != null ? parseInt(v, 10) + 'ms' : '-';
              html += '<td class="text-sm">' + esc(v ?? '-') + (ci === 0 ? '' : '') + '</td>';
            });
            html += '</tr>';
          });
          html += '</tbody></table>';
          wrap.innerHTML = html;
          card.appendChild(wrap);
          grid.appendChild(card);
        });
        c.appendChild(grid);
      }

      var re = stats.recent_errors || [];
      if (re.length) {
        var rec = document.createElement('div');
        rec.className = 'card';
        var rt = document.createElement('div'); rt.className = 'card-title text-danger'; rt.textContent = '最近错误（最新 10 条）';
        rec.appendChild(rt);
        var rw = document.createElement('div'); rw.className = 'table-wrap';
        var rhtml = '<table><thead><tr><th>时间</th><th>客户端</th><th>模型</th><th>路径</th><th>状态码</th><th>错误类型</th></tr></thead><tbody>';
        re.forEach(function (e2) {
          rhtml += '<tr><td class="text-sm">' + esc(GW.fmtTime(e2.created_at)) + '</td>' +
            '<td class="text-sm">' + esc(e2.client_display || '-') + '</td>' +
            '<td class="text-sm">' + esc(e2.model || '-') + '</td>' +
            '<td class="text-sm">' + esc(e2.request_path || '-') + '</td>' +
            '<td class="text-danger">' + esc(e2.status_code ?? '-') + '</td>' +
            '<td>' + esc(e2.error_type || '-') + '</td></tr>';
        });
        rhtml += '</tbody></table>';
        rw.innerHTML = rhtml;
        rec.appendChild(rw);
        c.appendChild(rec);
      }
    }

    // ---- 日志列表 ----
    var params = new URLSearchParams({ page: page, page_size: 20 });
    ['status_code', 'http_method', 'model', 'request_path', 'client_ip'].forEach(function (k) {
      var v = GW.S.logFilters[k];
      if (v) params.set(k, v);
    });
    var data = await api('/request-logs?' + params.toString());

    var logs = data.data || data.items || data.records || [];
    var total = data.total || logs.length || 0;
    var totalPages = data.total_pages || data.totalPages || Math.ceil(total / 20) || 1;

    if (!logs.length) {
      c.appendChild(htmlToEl('<div class="state-block card"><span class="ico">&#128196;</span><div class="msg">暂无日志</div></div>'));
      return;
    }
    GW.cache.logs = logs;

    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    var html = '<table><thead><tr><th>时间</th><th>客户端</th><th>来源IP</th><th>路径</th><th>方法</th><th>模型</th><th>状态码</th><th>延迟</th><th>Token</th><th>分类</th><th>操作</th></tr></thead><tbody>';
    logs.forEach(function (lg) {
      var cat = lg.log_category === 'normal' ? badge('正常', 'green') : (lg.log_category === 'auth_fail' ? badge('认证失败', 'yellow') : badge('错误', 'red'));
      var sc = lg.status_code;
      html += '<tr>' +
        '<td class="text-sm mono">' + esc(GW.fmtTime(lg.created_at)) + '</td>' +
        '<td class="text-sm" title="' + esc(lg.client_name || lg.client_id || '') + '"><span class="truncate" style="max-width:110px">' + esc(lg.client_name || lg.client_id || '-') + '</span></td>' +
        '<td><code>' + esc(lg.client_ip || '-') + '</code></td>' +
        '<td class="text-sm"><span class="truncate" style="max-width:110px">' + esc(lg.request_path || '-') + '</span></td>' +
        '<td>' + esc(lg.http_method || '-') + '</td>' +
        '<td class="text-sm"><span class="truncate" style="max-width:100px">' + esc(lg.model || '-') + '</span></td>' +
        '<td>' + statusBadge(sc) + '</td>' +
        '<td>' + GW.fmtLatency(lg.latency_ms) + '</td>' +
        '<td>' + (lg.total_tokens ? (lg.total_tokens / 1e3).toFixed(0) + 'K' : '-') + '</td>' +
        '<td>' + cat + '</td>' +
        '<td><button class="btn btn-sm" data-act="log-detail" data-id="' + esc(lg.id) + '">详情</button></td></tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    c.appendChild(wrap);
    c.appendChild(GW.pagination(page, total, totalPages, function (np) { GW.R.logs(np); }));
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

function statusBadge(sc) {
  if (sc == null) return '-';
  var color = sc == 200 ? 'green' : (sc >= 500 ? 'red' : (sc >= 400 ? 'yellow' : 'gray'));
  return '<span class="badge badge-' + color + '">' + esc(sc) + '</span>';
}

function htmlToEl(html) {
  var d = document.createElement('div');
  d.innerHTML = html;
  return d.firstChild;
}

// 日志详情弹窗（字段经 esc/textContent 输出）
GW.actions['log-detail'] = function (ds) {
  (async function () {
    try {
      var d = await api('/request-logs/' + encodeURIComponent(ds.id));
      var box = GW.$('modalContent');
      box.textContent = '';

      var head = document.createElement('div'); head.className = 'modal-header';
      var h3 = document.createElement('h3'); h3.textContent = '日志详情';
      var x = document.createElement('button'); x.className = 'modal-close'; x.textContent = '×'; x.dataset.act = 'dismiss-modal';
      head.appendChild(h3); head.appendChild(x);

      var body = document.createElement('div'); body.className = 'modal-body';
      body.appendChild(GW.statGrid([
        { label: '状态码', value: { html: statusBadge(d.status_code) } },
        { label: '延迟', value: GW.fmtLatency(d.latency_ms) },
        { label: 'Token', value: GW.fmtNum(d.total_tokens) },
        { label: '分类', value: d.log_category || '-' },
      ]));

      var fields = [
        ['请求ID', d.id], ['客户端ID', d.client_id], ['客户端名称', d.client_name],
        ['上游密钥ID', d.upstream_key_id], ['请求时间', d.created_at], ['开始时间', d.started_at],
        ['完成时间', d.completed_at], ['来源IP', d.client_ip], ['User-Agent', d.user_agent],
        ['请求路径', d.request_path], ['HTTP方法', d.http_method], ['模型', d.model],
        ['流式', d.is_stream ? '是' : '否'], ['Prompt Token', d.prompt_tokens],
        ['Completion Token', d.completion_tokens], ['错误类型', d.error_type],
        ['业务码', d.business_code], ['错误信息', d.error_msg],
      ];
      var wrap = document.createElement('div'); wrap.className = 'table-wrap';
      var html = '<table class="kv-table"><tbody>';
      fields.forEach(function (f) {
        if (f[1] === undefined || f[1] === null || f[1] === '') return;
        html += '<tr><td>' + esc(f[0]) + '</td><td class="wrap-cell">' + esc(String(f[1]).slice(0, 500)) + '</td></tr>';
      });
      html += '</tbody></table>';
      wrap.innerHTML = html;
      body.appendChild(wrap);

      function addPreBlock(title, content, isErr) {
        var sec = document.createElement('div'); sec.className = 'mt-12';
        var t = document.createElement('div'); t.className = 'section-title'; t.style.marginTop = '0'; t.style.fontSize = '11px';
        t.textContent = title;
        if (isErr) t.className += ' text-danger';
        var pre = document.createElement('pre'); pre.className = 'code-block' + (isErr ? ' err' : '');
        pre.textContent = content;
        sec.appendChild(t); sec.appendChild(pre);
        body.appendChild(sec);
      }
      if (d.error_detail) addPreBlock('错误详情', String(d.error_detail).slice(0, 4000), true);
      if (d.response_body) {
        var rb;
        try { rb = typeof d.response_body === 'string' ? JSON.stringify(JSON.parse(d.response_body), null, 2) : JSON.stringify(d.response_body, null, 2); }
        catch (e2) { rb = String(d.response_body).slice(0, 2000); }
        addPreBlock('响应内容', rb);
      }
      if (d.request_body) addPreBlock('请求内容', String(d.request_body).slice(0, 2000));

      var foot = document.createElement('div'); foot.className = 'modal-footer';
      var ok = document.createElement('button'); ok.className = 'btn'; ok.textContent = '关闭'; ok.dataset.act = 'dismiss-modal';
      foot.appendChild(ok);

      box.appendChild(head); box.appendChild(body); box.appendChild(foot);
      GW.$('modalOverlay').classList.add('show');
    } catch (e) { GW.toast(e.message, 'error'); }
  })();
};

/* ================================================================
 *  算法引擎 — GET /algorithm-stats + GET /algorithms/realtime（已修复端点）
 *  算法详情 — GET /algorithm/{num}
 * ================================================================ */
GW.R.algo = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-sm" data-act="reload-page">&#128260; 刷新</button>');
  try {
    var results = await Promise.all([
      api('/algorithm-stats'),
      api('/algorithms/realtime').catch(function () { return null; }), // 端点刚修复，失败不阻塞总览
    ]);
    var stats = results[0];
    var rt = results[1];
    c.innerHTML = '';

    // ---- 17 算法总览卡片 ----
    var algos = Object.keys(stats).filter(function (k) { return k.indexOf('algorithm_') === 0; });
    var gs = stats.global_stats || {};
    if (algos.length) {
      var title = document.createElement('div'); title.className = 'section-title'; title.textContent = '17 算法总览';
      c.appendChild(title);
      var cards = [];
      algos.forEach(function (k) {
        var a = stats[k];
        if (!a) return;
        var num = parseInt(k.replace('algorithm_', ''), 10);
        var main = a.total_buckets ?? a.value ?? a.count ?? a.current ?? a.total ?? '-';
        cards.push({
          label: a.name || k,
          value: main,
          sub: a.note || '',
          click: function () { GW.navTo('algo' + num); },
        });
      });
      c.appendChild(GW.statGrid(cards));

      if (gs.total_buckets != null) {
        c.appendChild(globalStatsSection(gs));
      }
    } else {
      c.innerHTML = GW.emptyState('暂无算法数据');
      return;
    }

    // ---- 实时状态（/algorithms/realtime，原 500 已修复） ----
    if (rt) {
      var t2 = document.createElement('div'); t2.className = 'section-title'; t2.textContent = '实时状态';
      c.appendChild(t2);
      var rgs = rt.global_status || {};
      c.appendChild(globalStatsSection(rgs, ['当前QPS', '在途请求', '软繁忙桶', '预热中桶', '调度次数']));
      var details = rt.algorithm_details || {};
      var liveCards = [];
      Object.keys(details).forEach(function (k) {
        var d = details[k];
        if (!d) return;
        var num = parseInt(k.replace('algorithm_', ''), 10);
        var meta = d.meta || {};
        var sm = d.summary || {};
        var subParts = [];
        Object.keys(sm).slice(0, 2).forEach(function (sk) {
          subParts.push(sk.replace(/_/g, ' ') + ': ' + (sm[sk] ?? '-'));
        });
        liveCards.push({
          label: meta.name || k,
          value: sm.total_buckets ?? d.value ?? '-',
          sub: subParts.join(' · '),
          click: function () { GW.navTo('algo' + num); },
        });
      });
      if (liveCards.length) {
        var lw = document.createElement('div');
        lw.className = 'text-xs text-dim mb-8';
        lw.textContent = '桶总数: ' + GW.fmtNum(rt.bucket_count) + ' · 采样时间: ' + (rt.timestamp ? GW.fmtTime(new Date(rt.timestamp * 1000).toISOString()) : '-');
        c.appendChild(lw);
        c.appendChild(GW.statGrid(liveCards));
      }
    } else {
      var note = document.createElement('div');
      note.className = 'card text-sm text-dim';
      note.textContent = '实时状态 (/algorithms/realtime) 暂不可用';
      c.appendChild(note);
    }
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

function globalStatsSection(gs, extraKeys) {
  var sec = document.createElement('div');
  sec.className = 'card';
  var t = document.createElement('div'); t.className = 'card-title'; t.textContent = '全局统计';
  sec.appendChild(t);
  var items = [
    { label: '总桶数', value: GW.fmtNum(gs.total_buckets) },
    { label: '冷却桶数', value: GW.fmtNum(gs.cooling_buckets ?? gs.cooled_buckets) },
    { label: '隔离桶数', value: GW.fmtNum(gs.isolated_buckets) },
    { label: '健康密钥数', value: GW.fmtNum(gs.healthy_key_count) },
    { label: '平均健康分', value: gs.avg_health_score ?? '-' },
    { label: '降级模式', value: { html: gs.degraded_mode ? badge('是', 'red') : badge('否', 'green') } },
  ];
  (extraKeys || []).forEach(function (k) {
    if (gs['current_qps'] != null && k === '当前QPS') items.push({ label: '当前QPS', value: gs.current_qps });
    if (gs['inflight_requests'] != null && k === '在途请求') items.push({ label: '在途请求', value: GW.fmtNum(gs.inflight_requests) });
    if (gs['soft_busy_buckets'] != null && k === '软繁忙桶') items.push({ label: '软繁忙桶', value: GW.fmtNum(gs.soft_busy_buckets) });
    if (gs['warmup_buckets'] != null && k === '预热中桶') items.push({ label: '预热中桶', value: GW.fmtNum(gs.warmup_buckets) });
    if (gs['total_select_calls'] != null && k === '调度次数') items.push({ label: '调度次数', value: GW.fmtNum(gs.total_select_calls) });
  });
  sec.appendChild(GW.statGrid(items));
  return sec;
}

// 算法详情页（侧栏 algoN 子项 / 总览卡片点击进入）
GW.algoDetail = function (num) {
  (async function () {
    var c = GW.$('content');
    c.innerHTML = GW.spinner();
    GW.headerActions('<button class="btn btn-sm" data-act="reload-page">&#128260; 刷新</button>');
    try {
      var d = await api('/algorithm/' + num);
      c.innerHTML = '';
      var meta = d.meta || {};
      var summary = d.summary || {};
      var gs = d.global_stats || {};
      var bd = d.bucket_details;
      var cd = d.client_details;
      var config = d.config;

      var back = document.createElement('div');
      back.className = 'mb-12';
      var backBtn = document.createElement('button');
      backBtn.className = 'btn btn-sm'; backBtn.dataset.act = 'nav'; backBtn.dataset.page = 'algo';
      backBtn.textContent = '← 返回算法引擎';
      back.appendChild(backBtn);
      c.appendChild(back);

      var mc = document.createElement('div');
      mc.className = 'card';
      var mh = document.createElement('h3');
      mh.textContent = meta.name || ('算法 #' + num);
      mc.appendChild(mh);
      if (meta.category) {
        var cat = document.createElement('span');
        cat.innerHTML = ' ' + badge(meta.category, 'cyan');
        mc.appendChild(cat);
      }
      if (meta.trigger) {
        var trg = document.createElement('div'); trg.className = 'text-sm text-sec mt-8';
        trg.textContent = '触发条件: ' + meta.trigger;
        mc.appendChild(trg);
      }
      if (meta.desc) {
        var ds = document.createElement('p'); ds.className = 'text-sm text-sec mt-8'; ds.style.lineHeight = '1.6';
        ds.textContent = meta.desc;
        mc.appendChild(ds);
      }
      c.appendChild(mc);

      var sumKeys = Object.keys(summary);
      if (sumKeys.length) {
        var t1 = document.createElement('div'); t1.className = 'section-title'; t1.textContent = '汇总统计';
        c.appendChild(t1);
        c.appendChild(GW.statGrid(sumKeys.map(function (k) {
          var v = summary[k];
          return { label: k.replace(/_/g, ' '), value: (typeof v === 'number') ? v.toLocaleString('en-US') : (v ?? '-') };
        })));
      }

      if (Object.keys(gs).length) c.appendChild(globalStatsSection(gs));

      var bdSec = buildDetailTable(bd, '桶详情');
      if (bdSec) c.appendChild(bdSec);
      var cdSec = buildDetailTable(cd, '客户端详情');
      if (cdSec) c.appendChild(cdSec);

      if (config && Object.keys(config).length) {
        var t3 = document.createElement('div'); t3.className = 'section-title'; t3.textContent = '配置';
        c.appendChild(t3);
        var cfgCard = document.createElement('div'); cfgCard.className = 'card';
        var cw = document.createElement('div'); cw.className = 'table-wrap';
        var ch = '<table class="kv-table"><tbody>';
        Object.keys(config).forEach(function (k) {
          var v = config[k];
          ch += '<tr><td>' + esc(k) + '</td><td class="wrap-cell">' + esc(v === null || v === undefined ? '-' : String(v)) + '</td></tr>';
        });
        ch += '</tbody></table>';
        cw.innerHTML = ch;
        cfgCard.appendChild(cw);
        c.appendChild(cfgCard);
      }
    } catch (e) {
      c.innerHTML = GW.errorCard(e.message);
    }
  })();
};

function buildDetailTable(items, title) {
  if (!items || !items.length) return null;
  var sec = document.createElement('div');
  var t = document.createElement('div'); t.className = 'section-title';
  t.textContent = title;
  var cnt = document.createElement('span'); cnt.className = 'cnt'; cnt.textContent = '(' + items.length + ')';
  t.appendChild(cnt);
  sec.appendChild(t);
  var wrap = document.createElement('div'); wrap.className = 'table-wrap card';
  var headers = Object.keys(items[0]);
  var html = '<table><thead><tr>';
  headers.forEach(function (h) { html += '<th>' + esc(h.replace(/_/g, ' ')) + '</th>'; });
  html += '</tr></thead><tbody>';
  items.forEach(function (item) {
    html += '<tr>';
    headers.forEach(function (h) {
      var v = item[h];
      html += '<td class="text-sm">' + ((v === null || v === undefined) ? '-' : (typeof v === 'number' ? v.toLocaleString('en-US') : esc(String(v)))) + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
  sec.appendChild(wrap);
  return sec;
}

/* ================================================================
 *  系统监控 — GET /system/{concurrency,ip-monitor,ip-monitor/blocked,
 *             ip-monitor/anomalies,user-stats,health},
 *            POST /system/ip-monitor/unblock
 * ================================================================ */
GW.R.monitor = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-sm" data-act="reload-page">&#128260; 刷新</button>');
  try {
    var res = await Promise.all([
      api('/system/concurrency').catch(function () { return null; }),
      api('/system/ip-monitor').catch(function () { return null; }),
      api('/system/ip-monitor/blocked').catch(function () { return null; }),
      api('/system/ip-monitor/anomalies').catch(function () { return null; }),
      api('/system/user-stats').catch(function () { return null; }),
      api('/system/health').catch(function () { return null; }),
    ]);
    var concurrency = res[0], ipMonitor = res[1], blocked = res[2], anomalies = res[3], userStats = res[4], health = res[5];
    c.innerHTML = '';

    // 系统健康
    if (health) {
      var h = document.createElement('div');
      h.className = 'card mb-12';
      var ht = document.createElement('div');
      ht.className = 'flex items-center justify-between';
      var ht1 = document.createElement('span'); ht1.className = 'card-title'; ht1.style.margin = '0'; ht1.textContent = '系统健康';
      var ht2 = document.createElement('span'); ht2.innerHTML = health.status === 'healthy' ? badge('健康', 'green') : badge('异常', 'red');
      ht.appendChild(ht1); ht.appendChild(ht2);
      h.appendChild(ht);
      if (health.checks) {
        var hw = document.createElement('div'); hw.className = 'table-wrap mt-12';
        var hh = '<table><thead><tr><th>检查项</th><th>状态</th><th>详情</th></tr></thead><tbody>';
        Object.keys(health.checks).forEach(function (k) {
          var v = health.checks[k];
          var ok = v.status ? true : (v === true);
          hh += '<tr><td>' + esc(k) + '</td><td>' + (ok ? badge('通过', 'green') : badge('异常', 'red')) + '</td>' +
            '<td class="text-sm wrap-cell">' + esc((v && (v.detail || v.message)) || '-') + '</td></tr>';
        });
        hh += '</tbody></table>';
        hw.innerHTML = hh;
        h.appendChild(hw);
      }
      c.appendChild(h);
    }

    if (concurrency) {
      var t1 = document.createElement('div'); t1.className = 'section-title'; t1.textContent = '并发统计';
      c.appendChild(t1);
      c.appendChild(GW.statGrid([
        { label: '当前并发', value: concurrency.current ?? concurrency.current_concurrency ?? concurrency.current_requests ?? '-' },
        { label: '峰值', value: concurrency.peak ?? concurrency.max ?? '-' },
        { label: '限制', value: concurrency.limit ?? concurrency.max_limit ?? '-' },
      ]));
    }

    if (ipMonitor) {
      var t2 = document.createElement('div'); t2.className = 'section-title'; t2.textContent = 'IP 监测';
      c.appendChild(t2);
      c.appendChild(GW.statGrid([
        { label: '总请求 IP', value: ipMonitor.total_ips ?? ipMonitor.totalIps ?? '-' },
        { label: '异常 IP', value: ipMonitor.anomaly_count ?? ipMonitor.anomalyCount ?? '-' },
        { label: '封禁 IP', value: ipMonitor.blocked_count ?? ipMonitor.blockedCount ?? '-' },
      ]));
    }

    if (blocked && blocked.length) {
      GW.cache.blockedIps = blocked;
      var t3 = document.createElement('div'); t3.className = 'section-title'; t3.textContent = '封禁 IP 列表';
      var c3 = document.createElement('span'); c3.className = 'cnt'; c3.textContent = '(' + blocked.length + ')';
      t3.appendChild(c3);
      c.appendChild(t3);
      var bw = document.createElement('div'); bw.className = 'table-wrap card';
      var bh = '<table><thead><tr><th>IP</th><th>原因</th><th>时间</th><th>操作</th></tr></thead><tbody>';
      blocked.forEach(function (b, i) {
        bh += '<tr><td><code>' + esc(b.ip || b.address || '-') + '</code></td>' +
          '<td class="text-sm wrap-cell">' + esc(b.reason || '-') + '</td>' +
          '<td class="text-sm text-dim">' + esc(b.blocked_at || b.time || '-') + '</td>' +
          '<td><button class="btn btn-sm btn-warning" data-act="ip-unblock" data-idx="' + i + '">解封</button></td></tr>';
      });
      bh += '</tbody></table>';
      bw.innerHTML = bh;
      c.appendChild(bw);
    }

    if (anomalies && anomalies.length) {
      var t4 = document.createElement('div'); t4.className = 'section-title'; t4.textContent = '异常 IP';
      var c4 = document.createElement('span'); c4.className = 'cnt'; c4.textContent = '(' + anomalies.length + ')';
      t4.appendChild(c4);
      c.appendChild(t4);
      var aw = document.createElement('div'); aw.className = 'table-wrap card';
      var ah = '<table><thead><tr><th>IP</th><th>类型</th><th>次数</th><th>最后时间</th></tr></thead><tbody>';
      anomalies.forEach(function (a) {
        ah += '<tr><td><code>' + esc(a.ip || a.address || '-') + '</code></td>' +
          '<td class="text-sm">' + esc(a.type || a.anomaly_type || '-') + '</td>' +
          '<td>' + esc(a.count ?? a.times ?? '-') + '</td>' +
          '<td class="text-sm text-dim">' + esc(a.last_time || a.last_seen || '-') + '</td></tr>';
      });
      ah += '</tbody></table>';
      aw.innerHTML = ah;
      c.appendChild(aw);
    }

    if (userStats) {
      var t5 = document.createElement('div'); t5.className = 'section-title'; t5.textContent = '用户分类统计';
      c.appendChild(t5);
      c.appendChild(GW.statGrid(Object.keys(userStats).map(function (k) {
        return { label: k, value: userStats[k] };
      })));
    }

    if (!health && !concurrency && !ipMonitor && !blocked && !anomalies && !userStats) {
      c.innerHTML = GW.emptyState('暂无监控数据');
    }
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

GW.actions['ip-unblock'] = function (ds) {
  var b = (GW.cache.blockedIps || [])[Number(ds.idx)];
  if (!b) return;
  var ip = b.ip || b.address;
  GW.confirmModal({
    title: '确认解封',
    body: '确定要解封 IP ' + ip + ' 吗？',
    confirmText: '解封',
    onConfirm: async function () {
      try {
        await api('/system/ip-monitor/unblock', { method: 'POST', body: JSON.stringify({ ip: ip }) });
        GW.toast('解封成功', 'success');
        GW.R.monitor();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

/* ================================================================
 *  系统配置 — GET /settings, POST /settings, POST /maintenance
 * ================================================================ */
var CONFIG_FIELDS = [
  { key: 'upstream_base_url', label: '上游基础 URL', type: 'text' },
  { key: 'chat_path', label: 'Chat 路径', type: 'text' },
  { key: 'models_path', label: 'Models 路径', type: 'text' },
  { key: 'cooldown_seconds', label: '冷却时长（秒）', type: 'number' },
  { key: 'switch_threshold', label: '切换阈值', type: 'number' },
];

GW.R.config = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-primary btn-sm" data-act="config-save">保存配置</button>');
  try {
    var settings = await api('/settings');
    c.innerHTML = '';

    var card = document.createElement('div');
    card.className = 'card';
    var t = document.createElement('div'); t.className = 'card-title'; t.textContent = '网关策略';
    card.appendChild(t);
    CONFIG_FIELDS.forEach(function (f) {
      var g = document.createElement('div'); g.className = 'form-group';
      var lab = document.createElement('label'); lab.textContent = f.label;
      var inp = document.createElement('input');
      inp.type = f.type;
      inp.className = 'cfg-input';
      inp.dataset.cfgKey = f.key;
      inp.value = settings[f.key] === null || settings[f.key] === undefined ? '' : String(settings[f.key]);
      g.appendChild(lab); g.appendChild(inp);
      card.appendChild(g);
    });
    c.appendChild(card);

    // 维护模式（后端返回 maintenance_mode 字符串 "true"/"false"）
    var mt = document.createElement('div');
    mt.className = 'card mt-12';
    var mtH = document.createElement('div');
    mtH.className = 'flex items-center justify-between';
    var mtT = document.createElement('span'); mtT.className = 'card-title'; mtT.style.margin = '0'; mtT.textContent = '维护模式';
    var mtB = document.createElement('span');
    var isMt = String(settings.maintenance_mode) === 'true';
    mtB.innerHTML = isMt ? badge('开启中', 'red') : badge('已关闭', 'green');
    mtH.appendChild(mtT); mtH.appendChild(mtB);
    mt.appendChild(mtH);
    var mtP = document.createElement('p'); mtP.className = 'text-sm text-sec mt-8'; mtP.textContent = '开启后所有 API 请求将返回 503 维护页面';
    mt.appendChild(mtP);
    var mtBtn = document.createElement('button');
    mtBtn.className = 'btn btn-warning btn-sm mt-12';
    mtBtn.textContent = isMt ? '关闭维护模式' : '开启维护模式';
    mtBtn.dataset.act = 'config-maintenance';
    mt.appendChild(mtBtn);
    c.appendChild(mt);
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

// 保存配置：按后端 PolicyUpdateRequest 契约发送扁平字段（旧版 {key:{...}} 会被静默忽略）
GW.actions['config-save'] = async function () {
  var body = {};
  var inputs = document.querySelectorAll('.cfg-input');
  inputs.forEach(function (inp) {
    var key = inp.dataset.cfgKey;
    var val = inp.value.trim();
    if (val === '') return;
    if (key === 'cooldown_seconds' || key === 'switch_threshold') body[key] = parseInt(val, 10);
    else body[key] = val;
  });
  try {
    await api('/settings', { method: 'POST', body: JSON.stringify(body) });
    GW.toast('保存成功', 'success');
    GW.R.config();
  } catch (e) { GW.toast(e.message, 'error'); }
};

GW.actions['config-maintenance'] = function () {
  GW.confirmModal({
    title: '切换维护模式',
    body: '确定要切换维护模式吗？开启后所有 API 请求将返回 503。',
    confirmText: '切换',
    onConfirm: async function () {
      try {
        var r = await api('/maintenance', { method: 'POST' });
        // 后端返回 {maintenance_mode: bool, message}
        GW.toast('维护模式: ' + (r.maintenance_mode ? '开启' : '关闭'), 'info');
        GW.R.config();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

/* ================================================================
 *  平台令牌 — GET/POST /platform-tokens, DELETE /platform-tokens/{id}
 * ================================================================ */
GW.R.tokens = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-primary btn-sm" data-act="token-create">+ 创建令牌</button>');
  try {
    var list = await api('/platform-tokens');
    GW.cache.tokens = list;
    c.innerHTML = '';
    if (!list.length) { c.innerHTML = GW.emptyState('暂无平台令牌'); return; }
    var wrap = document.createElement('div');
    wrap.className = 'table-wrap card';
    var html = '<table><thead><tr><th>名称</th><th>作用域</th><th>状态</th><th>创建时间</th><th>过期时间</th><th>最后使用</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (t) {
      var scopes = t.scopes || [];
      var scopeHtml = scopes.length
        ? '<div class="badge-row">' + scopes.map(function (s) { return badge(s, 'cyan'); }).join('') + '</div>'
        : '<span class="text-dim">-</span>';
      var st = t.status === 'active' ? badge('活跃', 'green') : badge(t.status || '-', 'gray');
      var expired = t.expires_at && String(t.expires_at) < new Date().toISOString().slice(0, 19) + 'Z';
      if (expired) st = badge('已过期', 'red');
      html += '<tr>' +
        '<td class="wrap-cell"><strong>' + esc(t.name || '-') + '</strong></td>' +
        '<td class="wrap-cell">' + scopeHtml + '</td>' +
        '<td>' + st + '</td>' +
        '<td class="text-sm text-dim">' + GW.fmtTime(t.created_at) + '</td>' +
        '<td class="text-sm text-dim">' + GW.fmtTime(t.expires_at) + '</td>' +
        '<td class="text-sm text-dim">' + GW.fmtTime(t.last_used_at) + '</td>' +
        '<td><button class="btn btn-sm btn-danger" data-act="token-delete" data-id="' + esc(t.id) + '" data-name="' + esc(t.name || '') + '">删除</button></td></tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
    c.appendChild(wrap);
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

GW.actions['token-create'] = function () {
  GW.formModal({
    title: '创建平台令牌',
    hint: '默认有效期 30 天；不勾选任何作用域时后端将使用最小默认集（clients:write / keys:write / keys:reveal / models:read）。',
    fields: [
      { id: 'name', label: '名称', placeholder: '令牌名称' },
      { id: 'scopes', label: '作用域（多选）', type: 'scopes', options: GW.TOKEN_SCOPES.map(function (s) {
        return { value: s.value, label: s.label, desc: s.desc, checked: s.dft };
      }) },
    ],
    submitText: '创建',
    onSubmit: async function (v) {
      if (!v.name) throw new Error('名称不能为空');
      var body = { name: v.name };
      if (v.scopes.length) body.scopes = v.scopes;
      var r = await api('/platform-tokens', { method: 'POST', body: JSON.stringify(body) });
      GW.R.tokens();
      // 完整令牌仅此一次显示（后端只存哈希，之后无法再取回明文）
      GW.secretModal({
        title: '令牌已创建（仅此一次显示）',
        note: r.message || '',
        secret: r.token || '',
        warn: '请立即复制保存；关闭后将无法再次查看明文。',
      });
      return false; // 接管弹窗（展示一次性明文），阻止 formModal 自动关闭
    },
  });
};

GW.actions['token-delete'] = function (ds) {
  GW.confirmModal({
    title: '确认删除',
    body: '确定要删除令牌「' + (ds.name || ds.id) + '」吗？此操作不可撤销。',
    danger: true,
    onConfirm: async function () {
      try {
        await api('/platform-tokens/' + encodeURIComponent(ds.id), { method: 'DELETE' });
        GW.toast('删除成功', 'success');
        GW.R.tokens();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

/* ================================================================
 *  错误码 — GET /error-codes
 * ================================================================ */
GW.R.errors = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('');
  try {
    var raw = await api('/error-codes');
    var list = raw.error_codes || raw.codes || raw.data || raw;
    var codes = Array.isArray(list) ? list : [];
    c.innerHTML = '';
    if (!codes.length) { c.innerHTML = GW.emptyState('暂无错误码数据'); return; }
    codes.forEach(function (e) {
      var card = document.createElement('div');
      card.className = 'card';
      var head = document.createElement('div');
      head.className = 'flex items-center justify-between mb-8';
      var code = document.createElement('span');
      code.style.cssText = 'font-size:22px;font-weight:700;color:var(--primary)';
      code.textContent = e.code ?? e.error_code ?? '-';
      var title = document.createElement('span');
      title.innerHTML = badge(e.title || e.name || '-', 'blue');
      head.appendChild(code); head.appendChild(title);
      card.appendChild(head);
      var desc = document.createElement('div'); desc.className = 'text-sm mb-8'; desc.textContent = e.description || e.desc || '-';
      card.appendChild(desc);
      var cause = document.createElement('div'); cause.className = 'text-xs text-sec'; cause.textContent = '常见原因: ' + (e.cause || e.reason || e.common_cause || '-');
      card.appendChild(cause);
      var sol = document.createElement('div'); sol.className = 'text-xs text-sec mt-12'; sol.textContent = '解决方案: ' + (e.solution || e.resolve || '-');
      card.appendChild(sol);
      c.appendChild(card);
    });
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

/* ================================================================
 *  商用检测 — GET /commercial-detection, PUT /commercial-detection/{id},
 *            POST /commercial-detection/{id}/block|unblock
 *            设置（已修复端点）：GET /commercial/settings,
 *            POST /commercial/toggle?enabled=, POST /commercial/threshold?threshold=,
 *            POST|DELETE /commercial/whitelist/{client_id}
 * ================================================================ */
GW.R.commercial = async function () {
  var c = GW.$('content');
  c.innerHTML = GW.spinner();
  GW.headerActions('<button class="btn btn-sm" data-act="reload-page">&#128260; 刷新</button>');
  try {
    var res = await Promise.all([
      api('/commercial-detection'),
      api('/commercial/settings').catch(function () { return null; }), // 端点刚修复，失败不阻塞列表
    ]);
    var list = res[0] || [];
    var settings = res[1];
    GW.cache.commercial = list;
    c.innerHTML = '';

    // ---- 检测设置（/commercial/settings 系列） ----
    if (settings) {
      var sc = document.createElement('div');
      sc.className = 'card mb-12';
      var scH = document.createElement('div');
      scH.className = 'flex items-center justify-between';
      var scT = document.createElement('span'); scT.className = 'card-title'; scT.style.margin = '0'; scT.textContent = '检测设置';
      scH.appendChild(scT);

      var sw = document.createElement('label');
      sw.className = 'switch';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!settings.detection_enabled;
      cb.id = 'cmToggle';
      var track = document.createElement('span'); track.className = 'track';
      sw.appendChild(cb); sw.appendChild(track);
      scH.appendChild(sw);
      sc.appendChild(scH);
      var scP = document.createElement('p'); scP.className = 'text-xs text-sec mt-8'; scP.textContent = '商用行为识别总开关';
      sc.appendChild(scP);

      var thrRow = document.createElement('div');
      thrRow.className = 'flex items-center gap-8 mt-12';
      var thrLab = document.createElement('span'); thrLab.className = 'text-sm text-sec'; thrLab.textContent = '置信度阈值';
      var thrInp = document.createElement('input');
      thrInp.type = 'number'; thrInp.min = '0'; thrInp.max = '100';
      thrInp.id = 'cmThreshold'; thrInp.style.width = '80px'; thrInp.style.padding = '5px 8px';
      thrInp.style.borderRadius = 'var(--radius-sm)'; thrInp.style.border = '1px solid var(--border)';
      thrInp.style.background = 'var(--bg-0)'; thrInp.style.color = 'var(--text)';
      thrInp.value = settings.confidence_threshold ?? 70;
      var thrBtn = document.createElement('button');
      thrBtn.className = 'btn btn-sm'; thrBtn.dataset.act = 'commercial-threshold'; thrBtn.textContent = '保存阈值';
      thrRow.appendChild(thrLab); thrRow.appendChild(thrInp); thrRow.appendChild(thrBtn);
      sc.appendChild(thrRow);

      var wlT = document.createElement('div'); wlT.className = 'section-title'; wlT.style.fontSize = '12px'; wlT.textContent = '白名单 (' + (settings.whitelist_count ?? 0) + ')';
      sc.appendChild(wlT);
      var wlWrap = document.createElement('div'); wlWrap.className = 'flex flex-wrap gap-8';
      var wl = settings.whitelist || [];
      if (wl.length) {
        wl.forEach(function (id) {
          var item = document.createElement('span');
          item.className = 'badge badge-gray';
          item.textContent = id + ' ';
          var del = document.createElement('button');
          del.textContent = '×';
          del.style.cssText = 'background:none;border:none;color:var(--danger);cursor:pointer;font-weight:700;padding:0 0 0 2px';
          del.dataset.act = 'commercial-wl-del';
          del.dataset.id = id;
          item.appendChild(del);
          wlWrap.appendChild(item);
        });
      } else {
        var none = document.createElement('span'); none.className = 'text-xs text-dim'; none.textContent = '（空）';
        wlWrap.appendChild(none);
      }
      sc.appendChild(wlWrap);

      var addRow = document.createElement('div');
      addRow.className = 'flex items-center gap-8 mt-12';
      var addInp = document.createElement('input');
      addInp.placeholder = 'client_id';
      addInp.id = 'cmWlInput';
      addInp.style.cssText = 'width:240px;padding:5px 8px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg-0);color:var(--text);font-size:12px';
      var addBtn = document.createElement('button');
      addBtn.className = 'btn btn-sm'; addBtn.dataset.act = 'commercial-wl-add'; addBtn.textContent = '加入白名单';
      addRow.appendChild(addInp); addRow.appendChild(addBtn);
      sc.appendChild(addRow);
      c.appendChild(sc);

      cb.addEventListener('change', async function () {
        var enabled = cb.checked;
        try {
          await api('/commercial/toggle?enabled=' + (enabled ? 'true' : 'false'), { method: 'POST' });
          GW.toast(enabled ? '商用检测已启用' : '商用检测已禁用', 'success');
        } catch (e) {
          GW.toast(e.message, 'error');
          cb.checked = !enabled;
        }
      });
    }

    // ---- 检测结果列表 ----
    if (!list.length) {
      c.appendChild(htmlToEl(GW.emptyState('暂无商用检测数据')));
      return;
    }
    list.forEach(function (item, i) {
      var card = document.createElement('div');
      card.className = 'card';
      var head = document.createElement('div');
      head.className = 'flex items-center justify-between mb-8 flex-wrap gap-8';
      var nameBox = document.createElement('div');
      var nm = document.createElement('strong'); nm.textContent = item.client_name || item.client_id || '-';
      var conf = item.confidence_score ?? item.confidence ?? item.score;
      var confLab = document.createElement('span'); confLab.className = 'text-sec text-sm'; confLab.textContent = '　置信度: ' + (conf ?? '-') + '%';
      nameBox.appendChild(nm); nameBox.appendChild(confLab);
      var flagBox = document.createElement('div'); flagBox.className = 'flex gap-8';
      if (item.false_positive) flagBox.innerHTML += badge('误报', 'yellow');
      if (item.admin_confirmed) flagBox.innerHTML += badge('已确认', 'blue');
      head.appendChild(nameBox); head.appendChild(flagBox);
      card.appendChild(head);

      var dims = [
        ['interval_cv', '变异系数'], ['model_switch_count', '模型切换'], ['avg_concurrent', '平均并发'], ['template_ratio', '模板占比'],
      ];
      var dimWrap = document.createElement('div'); dimWrap.className = 'flex flex-wrap gap-8';
      dims.forEach(function (d) {
        if (item[d[0]] === undefined || item[d[0]] === null) return;
        var b = document.createElement('span'); b.className = 'badge badge-gray';
        b.textContent = d[1] + ': ' + item[d[0]];
        dimWrap.appendChild(b);
      });
      card.appendChild(dimWrap);

      var lu = document.createElement('div'); lu.className = 'text-xs text-dim mt-8';
      lu.textContent = 'client_id: ' + (item.client_id || '-') + (item.last_updated ? ' · 更新于 ' + GW.fmtTime(item.last_updated) : '');
      card.appendChild(lu);

      var acts = document.createElement('div');
      acts.className = 'flex gap-8 mt-12 flex-wrap';
      var confirmed = !!item.admin_confirmed;
      var fp = !!item.false_positive;

      var b1 = document.createElement('button'); b1.className = 'btn btn-sm'; b1.dataset.act = 'commercial-confirm'; b1.dataset.idx = i; b1.textContent = confirmed ? '取消确认' : '确认';
      var b2 = document.createElement('button'); b2.className = 'btn btn-sm'; b2.dataset.act = 'commercial-fp'; b2.dataset.idx = i; b2.textContent = fp ? '取消误报' : '标记误报';
      var b3 = document.createElement('button'); b3.className = 'btn btn-sm btn-danger'; b3.dataset.act = 'commercial-block'; b3.dataset.id = item.client_id; b3.textContent = '封禁';
      var b4 = document.createElement('button'); b4.className = 'btn btn-sm btn-warning'; b4.dataset.act = 'commercial-unblock'; b4.dataset.id = item.client_id; b4.textContent = '解封';
      acts.appendChild(b1); acts.appendChild(b2); acts.appendChild(b3); acts.appendChild(b4);
      card.appendChild(acts);
      c.appendChild(card);
    });
  } catch (e) {
    c.innerHTML = GW.errorCard(e.message);
  }
};

GW.actions['commercial-threshold'] = async function () {
  var inp = GW.$('cmThreshold');
  var v = parseInt(inp && inp.value, 10);
  if (isNaN(v) || v < 0 || v > 100) { GW.toast('阈值需为 0-100 的整数', 'error'); return; }
  try {
    var r = await api('/commercial/threshold?threshold=' + v, { method: 'POST' });
    GW.toast('阈值已设为 ' + (r.threshold ?? v), 'success');
  } catch (e) { GW.toast(e.message, 'error'); }
};

GW.actions['commercial-wl-add'] = async function () {
  var inp = GW.$('cmWlInput');
  var v = (inp && inp.value || '').trim();
  if (!v) { GW.toast('请输入 client_id', 'error'); return; }
  try {
    var r = await api('/commercial/whitelist/' + encodeURIComponent(v), { method: 'POST' });
    GW.toast(r.message || '已加入白名单', 'success');
    GW.R.commercial();
  } catch (e) { GW.toast(e.message, 'error'); }
};

GW.actions['commercial-wl-del'] = function (ds) {
  var id = ds.id;
  GW.confirmModal({
    title: '移除白名单',
    body: '确定要将 ' + id + ' 移出商用检测白名单吗？',
    onConfirm: async function () {
      try {
        var r = await api('/commercial/whitelist/' + encodeURIComponent(id), { method: 'DELETE' });
        GW.toast(r.message || '已移除', 'success');
        GW.R.commercial();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

GW.actions['commercial-confirm'] = function (ds) {
  var item = (GW.cache.commercial || [])[Number(ds.idx)];
  if (!item) return;
  var body = { admin_confirmed: !item.admin_confirmed, false_positive: !!item.false_positive };
  GW.confirmModal({
    title: body.admin_confirmed ? '确认商用' : '取消确认',
    body: '确定要' + (body.admin_confirmed ? '将该客户端确认为商用行为' : '取消商用确认') + '吗？',
    onConfirm: async function () {
      try {
        await api('/commercial-detection/' + encodeURIComponent(item.client_id), { method: 'PUT', body: JSON.stringify(body) });
        GW.toast('更新成功', 'success');
        GW.R.commercial();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

GW.actions['commercial-fp'] = function (ds) {
  var item = (GW.cache.commercial || [])[Number(ds.idx)];
  if (!item) return;
  var body = { admin_confirmed: !!item.admin_confirmed, false_positive: !item.false_positive };
  GW.confirmModal({
    title: body.false_positive ? '标记误报' : '取消误报',
    body: body.false_positive ? '标记误报后该客户端将加入白名单并清除限速，确定吗？' : '确定要取消误报标记吗？',
    onConfirm: async function () {
      try {
        await api('/commercial-detection/' + encodeURIComponent(item.client_id), { method: 'PUT', body: JSON.stringify(body) });
        GW.toast('更新成功', 'success');
        GW.R.commercial();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

GW.actions['commercial-block'] = function (ds) {
  GW.confirmModal({
    title: '确认封禁',
    body: '确定要封禁客户端 ' + ds.id + ' 吗？将拉黑其账户与网关密钥。',
    danger: true,
    confirmText: '封禁',
    onConfirm: async function () {
      try {
        await api('/commercial-detection/' + encodeURIComponent(ds.id) + '/block', { method: 'POST', body: JSON.stringify({}) });
        GW.toast('已封禁', 'success');
        GW.R.commercial();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};

GW.actions['commercial-unblock'] = function (ds) {
  GW.confirmModal({
    title: '确认解封',
    body: '确定要解封客户端 ' + ds.id + ' 吗？',
    confirmText: '解封',
    onConfirm: async function () {
      try {
        await api('/commercial-detection/' + encodeURIComponent(ds.id) + '/unblock', { method: 'POST', body: JSON.stringify({}) });
        GW.toast('已解封', 'success');
        GW.R.commercial();
      } catch (e) { GW.toast(e.message, 'error'); }
    },
  });
};
})();
