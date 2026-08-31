// 职责：网关管理控制台前端静态冒烟检查——引用完整性、内联事件/XSS 纪律、页面渲染器
//       与 API 契约锚点（无浏览器依赖，node 直接跑）
// 用法：node tests/static-smoke.mjs （工作目录任意，路径从仓库根解析）

import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const staticDir = join(repoRoot, 'gateway', 'app', 'static');
const consoleHtml = join(staticDir, 'console.html');

let failed = 0;
const check = (ok, msg) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`);
  if (!ok) failed++;
};

const html = readFileSync(consoleHtml, 'utf8');

/* 1) console.html 引用的本地 js/css 文件必须存在 */
const refs = [...html.matchAll(/(?:src|href)="(\/static\/[^"?]+)(?:\?[^"]*)?"/g)].map((m) => m[1]);
check(refs.length >= 3, `console.html 引用了 ${refs.length} 个静态资源（core + pages-data + pages-ops + pages-test）`);
for (const ref of refs) {
  const local = join(staticDir, ref.replace(/^\/static\//, ''));
  check(existsSync(local), `引用文件存在: ${ref}`);
}

/* 2) console.html 无内联事件（交互全部走 JS 绑定/事件委托） */
const inlineHandlers = html.match(/\son(click|mouseover|mouseout|submit|input|change|load|error)\s*=/gi) || [];
check(inlineHandlers.length === 0, `console.html 无内联事件处理器 (发现 ${inlineHandlers.length} 处)`);

/* 3) console.html 无内联 <script> 代码块（只允许 src 引用；<style> 不受限） */
const inlineScripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
  .filter((m) => m[1].trim().length > 0);
check(inlineScripts.length === 0, `console.html 无内联脚本块 (发现 ${inlineScripts.length} 处)`);

/* 4) 所有 js 文件：禁止“模板插值进入内联事件属性”（XSS 纪律） */
function listJsFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...listJsFiles(p));
    else if (name.endsWith('.js')) out.push(p);
  }
  return out;
}
const jsFiles = listJsFiles(join(staticDir, 'js'));
check(jsFiles.length >= 3, `js 模块数量 ${jsFiles.length} (core + 三组页面模块)`);

let attrInterp = 0;
const attrInterpRe = /\son(?:click|mouseover|mouseout|submit|input|change)\s*=\s*(?:"[^"]*\$\{|'[^']*\$\{|`[^`]*\$\{)/gi;
for (const f of jsFiles) {
  const lines = readFileSync(f, 'utf8').split('\n');
  lines.forEach((line, i) => {
    if (attrInterpRe.test(line)) {
      attrInterp++;
      console.error(`      ↳ ${f}:${i + 1} 疑似插值内联事件: ${line.trim().slice(0, 100)}`);
    }
    attrInterpRe.lastIndex = 0;
  });
}
check(attrInterp === 0, `js 模板中无“数据插值进内联事件” (发现 ${attrInterp} 处)`);

/* 5) 转义工具 esc() 定义于 core.js，并被两组页面模块引用 */
const coreSrc = readFileSync(join(staticDir, 'js', 'console', 'core.js'), 'utf8');
check(/function esc\(/.test(coreSrc) && /GW\.esc = esc/.test(coreSrc), 'core.js 定义并导出 esc()');

for (const f of ['pages-data.js', 'pages-ops.js']) {
  const src = readFileSync(join(staticDir, 'js', 'console', f), 'utf8');
  check(/\besc\(/.test(src), `数据渲染模块 ${f} 使用了转义工具 esc()`);
}

/* 6) 不引外部 CDN/框架脚本（零构建约束） */
const external = html.match(/<script[^>]+src=["']https?:/gi) || [];
check(external.length === 0, '无外部 CDN 脚本引用');

/* 7) API 契约锚点抽查：关键 /gw/admin 端点仍按原路径调用 */
const allJs = jsFiles.map((f) => readFileSync(f, 'utf8')).join('\n');
const endpoints = [
  '/login', '/dashboard', '/upstreams', '/proxies', '/clients', '/buckets', '/request-logs',
  '/algorithm-stats', '/algorithms/realtime', '/settings', '/maintenance',
  '/error-codes', '/commercial-detection', '/system/concurrency', '/system/ip-monitor',
  '/system/ip-monitor/blocked', '/system/ip-monitor/anomalies', '/system/ip-monitor/unblock',
  '/model-test/models', '/model-test/probe', '/model-test/selftest-key',
];
const missing = endpoints.filter((ep) => !allJs.includes(`'${ep}`));
check(missing.length === 0, `API 端点契约齐全 (缺失: ${missing.join(', ') || '无'})`);

/* 8) 页面完整性：PN 中每个页面都有对应的 GW.R.<page> 渲染器 */
const pnMatch = coreSrc.match(/var PN = \[([^\]]+)\]/);
check(!!pnMatch, 'core.js 定义了页面清单 PN');
const pages = pnMatch ? [...pnMatch[1].matchAll(/'([^']+)'/g)].map((m) => m[1]) : [];
const unregistered = pages.filter((p) => !new RegExp(`GW\\.R\\.${p}\\s*=`).test(allJs));
check(pages.length >= 12 && unregistered.length === 0,
  `页面渲染器齐全 ${pages.length} 个 (缺失: ${unregistered.join(', ') || '无'})`);

/* 8b) 代理池页面：出网模式三态齐全 + 池内代理增删改测动作已注册 */
check(/GW\.proxyModeBadge\s*=/.test(allJs), '前端提供出网模式徽标 GW.proxyModeBadge');
const proxyActs = ['proxy-create', 'proxy-bulk-create', 'proxy-edit', 'proxy-delete',
  'proxy-toggle', 'proxy-test'];
const missingActs = proxyActs.filter((a) => !allJs.includes(`GW.actions['${a}']`));
check(missingActs.length === 0, `代理池动作齐全 (缺失: ${missingActs.join(', ') || '无'})`);

/* 8b2) 上游密钥页：单个添加与批量添加两条路径并存，批量走 textarea + /upstreams/bulk */
const keyActs = ['upstream-create', 'upstream-bulk-create', 'upstream-edit', 'upstream-delete',
  'upstream-reveal', 'upstream-unfreeze'];
const missingKeyActs = keyActs.filter((a) => !allJs.includes(`GW.actions['${a}']`));
check(missingKeyActs.length === 0,
  `上游密钥动作齐全，单个/批量添加并存 (缺失: ${missingKeyActs.join(', ') || '无'})`);
check(allJs.includes("'/upstreams/bulk'"), '批量添加调用 POST /upstreams/bulk');
check(/f\.type === 'textarea'/.test(coreSrc) && /createElement\('textarea'\)/.test(coreSrc),
  'formModal 支持 textarea 字段（批量粘贴多行密钥）');
// 批量结果面板逐行展示，必须走 textContent，不许把后端回显拼进 innerHTML
const dataSrc = readFileSync(join(staticDir, 'js', 'console', 'pages-data.js'), 'utf8');
const bulkPanel = (dataSrc.match(/function showBulkResult\([\s\S]*?\n}/) || [''])[0];
check(bulkPanel.length > 0 && !/innerHTML/.test(bulkPanel),
  '批量结果面板仅用 textContent 输出（无 innerHTML）');

/* 8b3) 代理池批量添加：与单个添加并存，走 textarea + /proxies/bulk，结果面板与密钥批量共用 */
check(allJs.includes("GW.actions['proxy-create']") && allJs.includes("GW.actions['proxy-bulk-create']"),
  '代理池单个添加与批量添加两条路径并存');
check(allJs.includes("'/proxies/bulk'"), '代理批量添加调用 POST /proxies/bulk');
check(/showBulkResult\(r,\s*function/.test(dataSrc),
  '代理批量结果复用 showBulkResult（只换「已创建」文案，不另写一套面板）');
const proxyBulkAct = (dataSrc.match(/GW\.actions\['proxy-bulk-create'\][\s\S]*?\n};/) || [''])[0];
check(/proxy_urls/.test(proxyBulkAct) && /type: 'textarea'/.test(proxyBulkAct),
  '代理批量添加用 textarea 收多行 proxy_urls');
check(proxyBulkAct.length > 0 && !/innerHTML/.test(proxyBulkAct),
  '代理批量添加动作内无 innerHTML');

/* 8b4) 代理池多选批量：勾选 + 一键全选 + 批量测试/启用/停用 */
const proxySelActs = ['proxy-sel-all', 'proxy-sel-none', 'proxy-bulk-test',
  'proxy-bulk-enable', 'proxy-bulk-disable', 'proxy-bulk-abort'];
const missingSelActs = proxySelActs.filter((a) => !dataSrc.includes(`GW.actions['${a}']`));
check(missingSelActs.length === 0,
  `代理池多选批量动作齐全 (缺失: ${missingSelActs.join(', ') || '无'})`);
check(dataSrc.includes("'/proxies/status'") && /method: 'PUT'/.test(dataSrc),
  '批量启停调用 PUT /proxies/status（一次请求，不逐个打 /proxies/{id}）');
const proxyTable = (dataSrc.match(/function paintProxyTable\([\s\S]*?\n}/) || [''])[0];
check(/data-pid="' \+ esc\(p\.id\)/.test(proxyTable),
  '行 checkbox 与行 data-pid 均经 esc() 输出');
check(/data-all="1"/.test(proxyTable), '表头提供一键全选 checkbox（data-all）');
check(/\[data-all\]/.test(dataSrc) && /indeterminate/.test(dataSrc),
  '表头全选支持三态（全选/半选/未选）');
const proxyBulkTest = (dataSrc.match(/GW\.actions\['proxy-bulk-test'\][\s\S]*?\n};/) || [''])[0];
check(/new AbortController\(\)/.test(proxyBulkTest) && /signal\.aborted/.test(proxyBulkTest),
  '批量测试可中止（AbortController + signal.aborted 双检）');
check(/PROXY_TEST_CONC/.test(proxyBulkTest) && /queue\.shift\(\)/.test(proxyBulkTest),
  '批量测试走并发池（PROXY_TEST_CONC 个 worker 轮取队列）');
check(/GW\.\$\('proxyTableWrap'\)/.test(proxyBulkTest),
  '批量测试在页面被切走后主动收手（检查 proxyTableWrap 是否还在）');
check(proxyBulkTest.length > 0 && !/innerHTML/.test(proxyBulkTest),
  '批量测试动作内无 innerHTML');
const proxyCheckFn = (dataSrc.match(/function setProxyCheck\([\s\S]*?\n}/) || [''])[0];
check(/textContent = ' ' \+ msg/.test(proxyCheckFn),
  '探测结果的后端文案走 textContent（badge 仅静态映射）');

/* 8c) 模型测试页：动作齐全 + 自测密钥不落盘 + innerHTML 只写静态模板/已转义组件 */
const testSrc = readFileSync(join(staticDir, 'js', 'console', 'pages-test.js'), 'utf8');
const mtActs = ['mtest-refresh', 'mtest-show-key', 'mtest-rotate-key'];
const missingMt = mtActs.filter((a) => !testSrc.includes(`GW.actions['${a}']`));
check(missingMt.length === 0, `模型测试动作齐全 (缺失: ${missingMt.join(', ') || '无'})`);
// 注释里可以提「不写 localStorage」，代码里不许真写，故先剔除注释行再判定
const testCode = testSrc.split('\n')
  .filter((l) => !/^\s*(\/\/|\/\*|\*)/.test(l))
  .join('\n');
check(!/localStorage|sessionStorage|document\.cookie/.test(testCode),
  '内置自测密钥只存闭包，未写入 localStorage/sessionStorage/cookie');
check(/new AbortController\(\)/.test(testSrc) && /signal\.aborted/.test(testSrc),
  '批量测试可中止（AbortController + 中止态判定）');

const testLines = testSrc.split('\n');
const badInner = [];
testLines.forEach((line, i) => {
  if (!/\.innerHTML\s*=/.test(line)) return;
  const rhs = line.split(/\.innerHTML\s*=/)[1].trim();
  const next = (testLines[i + 1] || '').trim();
  const ok = rhs === '' ? next.startsWith("'") : /^('|GW\.spinner\(|GW\.errorCard\(|badge\()/.test(rhs);
  if (!ok) badInner.push(i + 1);
});
check(badInner.length === 0,
  `pages-test.js 的 innerHTML 仅写静态模板/已转义组件 (可疑行: ${badInner.join(', ') || '无'})`);

/* 8d) 模型测试后端：默认提示词就位，且探测结果不外泄代理 URL（内嵌账号密码） */
const mtBackend = readFileSync(join(repoRoot, 'gateway', 'app', 'model_test.py'), 'utf8');
check(mtBackend.includes('DEFAULT_TEST_PROMPT = "你是什么模型，你可以帮我干什么事情"'),
  '后端定义了默认测试提示词 DEFAULT_TEST_PROMPT');
check(!/proxy_url/.test(mtBackend), '探测响应不含 proxy_url 字段（代理 URL 内嵌凭据，只回 direct/proxy）');

/* 8e) 默认输出预算前后端一致：推理模型思维链先吃配额，两侧默认值必须同步 */
const mtDefaultBudget = (mtBackend.match(/DEFAULT_MAX_TOKENS\s*=\s*(\d+)/) || [])[1];
const feInputDefault = (testSrc.match(/id="mtMaxTokens"\s+value="(\d+)"/) || [])[1];
const feClampFallback = (testSrc.match(/clampInt\(GW\.\$\('mtMaxTokens'\)\.value,\s*1,\s*512,\s*(\d+)\)/) || [])[1];
check(!!mtDefaultBudget && feInputDefault === mtDefaultBudget && feClampFallback === mtDefaultBudget,
  `max_tokens 默认值前后端一致 (后端 ${mtDefaultBudget}, 输入框 ${feInputDefault}, 回落 ${feClampFallback})`);

/* 8f) 模型管理页：动作齐全 + 搜索为前端即时过滤 + 隐藏即禁用开关 */
const mdlActs = ['model-add', 'model-delete', 'model-toggle', 'model-bulk-hide',
  'model-bulk-show', 'model-refresh', 'model-alias', 'alias-edit', 'alias-delete'];
const missingMdl = mdlActs.filter((a) => !dataSrc.includes(`GW.actions['${a}']`));
check(missingMdl.length === 0, `模型管理动作齐全 (缺失: ${missingMdl.join(', ') || '无'})`);
check(allJs.includes("'/models/visibility'") && allJs.includes("'/models/block-setting'"),
  '模型管理调用 PUT /models/visibility 与 PUT /models/block-setting');
check(/id="mdlSearch"/.test(dataSrc) && /function matchedModels\(/.test(dataSrc) &&
  /addEventListener\('input'[\s\S]{0,120}paintModelTable\(\)/.test(dataSrc),
  '模型管理页有搜索框，且按输入即时前端过滤（不重建输入框、不发请求）');
check(/mdlBlockToggle/.test(dataSrc) && /block_calls/.test(dataSrc),
  '模型管理页提供「隐藏的模型同时禁止调用」开关');
// 模型 ID 来自上游，渲染必须转义；行动作只带 data-idx
const mdlPaint = (dataSrc.match(/function paintModelTable\([\s\S]*?\n}/) || [''])[0];
check(mdlPaint.length > 0 && /esc\(r\.model_id\)/.test(mdlPaint) &&
  !/\$\{/.test(mdlPaint) && !/data-act="model-[a-z-]+" data-id="/.test(mdlPaint),
  '模型表格经 esc() 输出模型 ID，行动作只带 data-idx');

/* 8f2) 模型别名 / 映射：同页新增卡片表格，共用同一个搜索词 */
check(allJs.includes("'/models/alias'") && /method: 'PUT'/.test(dataSrc) &&
  /\/models\/alias\?alias=' \+ encodeURIComponent/.test(dataSrc),
  '别名调用 PUT /models/alias 与 DELETE /models/alias?alias=（值经 encodeURIComponent）');
// 别名文本来自管理员输入，渲染必须转义（badge() 内部 esc）；行动作只带 data-idx
const aliasPaint = (dataSrc.match(/function paintAliasTable\([\s\S]*?\n}/) || [''])[0];
check(aliasPaint.length > 0 && /esc\(r\.alias\)/.test(aliasPaint) &&
  /esc\(r\.target_model\)/.test(aliasPaint) && !/\$\{/.test(aliasPaint) &&
  !/data-act="alias-[a-z-]+" data-id="/.test(aliasPaint),
  '别名表经 esc() 输出别名与目标模型，行动作只带 data-idx');
check(/\(r\.aliases \|\| \[\]\)\.map\(function \(a\) \{ return badge\(a, 'blue'\); \}\)/.test(mdlPaint),
  '模型表格的「别名」列经 badge()（内部 esc）渲染，不做字符串内插');
check(/function matchedAliases\(/.test(dataSrc) &&
  /r\.aliases \|\| \[\]\)\.some/.test(dataSrc) &&
  /addEventListener\('input'[\s\S]{0,160}paintAliasTable\(\)/.test(dataSrc),
  '同一个搜索框同时过滤模型表与别名表，且模型搜索命中别名文本');
check(/keep_original: v\.keep_original === '1'/.test(dataSrc) &&
  /force_mapping: v\.force_mapping === '1'/.test(dataSrc) &&
  /type: 'select'/.test(dataSrc),
  '别名弹窗用 select 表达布尔开关（保留原名 / 回写响应），未改动 core.js');
check(/target_missing \? badge\('目标已不存在', 'red'\)/.test(aliasPaint),
  '目标模型已不存在的别名行有红色徽标提示清理');

/* 8g) 后端：白名单已下线，可见性与「名称有效性」分离 */
const pubSrc = readFileSync(join(repoRoot, 'gateway', 'app', 'public_api.py'), 'utf8');
check(!/_VERIFIED_WORKING_MODELS/.test(pubSrc),
  'public_api 已移除硬编码白名单 _VERIFIED_WORKING_MODELS');
check(/async def get_model_list\(/.test(pubSrc) && /apply_overrides\(raw, overrides\)/.test(pubSrc),
  'public_api 提供 get_model_list()：上游全量 + 管理员覆盖层');
check(/refresh_verified_models\(all_known_models\(raw, overrides\)\)/.test(pubSrc),
  '模型ID纠错基于「真实存在」全量集合（含被隐藏项），隐藏不会导致静默改写模型');
check(/code": "model_disabled/.test(pubSrc) && /is_call_blocked\(model\)/.test(pubSrc),
  '开关开启时隐藏模型调用返回 400 model_disabled');

/* 8h) 别名层后端：解析在纠错之前、别名不进纠错集合、全链路只用真名 */
const regSrc = readFileSync(join(repoRoot, 'gateway', 'app', 'model_registry.py'), 'utf8');
check(/apply_aliases\(apply_overrides\(raw, overrides\), aliases\)/.test(pubSrc),
  '对外列表 = 上游全量 → 覆盖层 → 别名层');
const chatBody = pubSrc.split('async def chat_completions(')[1].split(/\n(?:@router\.|async def |def )/)[0];
check(chatBody.indexOf('await resolve_alias(model)') >= 0 &&
  chatBody.indexOf('await resolve_alias(model)') <
    chatBody.indexOf('validate_and_correct_model(model)'),
  '别名解析发生在模型纠错之前（别名进纠错集合会被模糊改写成别名再发上游 → 404）');
check(/body\["model"\] = real_model/.test(chatBody) &&
  (pubSrc.match(/refresh_verified_models\(/g) || []).length === 1 &&
  !/refresh_verified_models\(apply_aliases/.test(pubSrc),
  '解析后全链路只用真名：body["model"] 改写，纠错集合仍只吃 all_known_models');
const embBody = pubSrc.split('async def embeddings(')[1].split('\n\n\n')[0];
check(/await resolve_alias\(model\)/.test(embBody),
  '/v1/embeddings 也解析别名（该端点没有纠错步骤）');
const mtSrc = readFileSync(join(repoRoot, 'gateway', 'app', 'model_test.py'), 'utf8');
check(/await resolve_alias\(model\)/.test(mtSrc) && /"model": real_model/.test(mtSrc),
  '模型测试的直连探测先把别名解析回真名');
check(/CREATE UNIQUE INDEX IF NOT EXISTS idx_model_aliases_lower/.test(
  readFileSync(join(repoRoot, 'gateway', 'app', 'database.py'), 'utf8')),
  'model_aliases 建了 lower(alias) 唯一索引（别名解析大小写不敏感）');
check(/keep_original: bool = False/.test(regSrc) && /force_mapping: bool = True/.test(regSrc),
  '别名默认语义：替换真名（keep_original=0）+ 回写响应 model（force_mapping=1）');

/* 9) 纯网关形态：前端不得残留已下线的 platform 耦合端点 */
const removed = ['/platform-tokens', '/system/user-stats', '/system/health'];
const leftover = removed.filter((ep) => allJs.includes(ep));
check(leftover.length === 0, `无已下线的 platform 耦合端点 (残留: ${leftover.join(', ') || '无'})`);

/* 10) 时间戳展示：后端统一写 UTC Z，前端必须做本地时区转换而非字符串截断 */
check(/function fmtTime\([\s\S]*?new Date\(String\(t\)\)[\s\S]*?getHours\(\)/.test(coreSrc),
  'core.js 的 fmtTime() 按本地时区渲染（解析后取本地时分，非字符串切片）');
const opsSrc = readFileSync(join(staticDir, 'js', 'console', 'pages-ops.js'), 'utf8');
const rawTs = ['d.created_at', 'd.started_at', 'd.completed_at'].filter(
  (f) => new RegExp(`\\[\\s*'[^']+'\\s*,\\s*${f.replace('.', '\\.')}\\s*\\]`).test(opsSrc));
check(rawTs.length === 0, `请求日志详情的时间字段均经 fmtTime 渲染 (裸值: ${rawTs.join(', ') || '无'})`);

console.log(failed === 0 ? '\n全部检查通过 ✓' : `\n${failed} 项检查未通过 ✗`);
process.exit(failed === 0 ? 0 : 1);

