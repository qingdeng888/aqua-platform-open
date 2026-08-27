// 职责：前端静态冒烟检查——引用完整性、内联事件/XSS 纪律、escapeHtml 引用（无浏览器依赖，node 直接跑）
// 用法：node tests/static-smoke.mjs （工作目录任意，路径从仓库根解析）

import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const staticDir = join(repoRoot, 'platform', 'app', 'static');
const indexHtml = join(staticDir, 'index.html');

let failed = 0;
const check = (ok, msg) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`);
  if (!ok) failed++;
};

const html = readFileSync(indexHtml, 'utf8');

/* 1) index.html 引用的本地 js/css 文件必须存在 */
const refs = [...html.matchAll(/(?:src|href)="(\/static\/[^"?]+)(?:\?[^"]*)?"/g)].map((m) => m[1]);
check(refs.length >= 4, `index.html 引用了 ${refs.length} 个静态资源（3 层 CSS + 模块入口 JS）`);
for (const ref of refs) {
  const local = join(staticDir, ref.replace(/^\/static\//, ''));
  check(existsSync(local), `引用文件存在: ${ref}`);
}

/* 2) index.html 无内联事件（含静态字符串也一并禁止，交互全部走 JS 绑定/委托） */
const inlineHandlers = html.match(/\son(click|mouseover|mouseout|submit|input|change|load|error)\s*=/gi) || [];
check(inlineHandlers.length === 0, `index.html 无内联事件处理器 (发现 ${inlineHandlers.length} 处)`);

/* 3) index.html 无内联 <script> 代码块（只允许 src 引用） */
const inlineScripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
  .filter((m) => m[1].trim().length > 0);
check(inlineScripts.length === 0, `index.html 无内联脚本块 (发现 ${inlineScripts.length} 处)`);

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
const jsFiles = listJsFiles(staticDir);
check(jsFiles.length >= 15, `js 模块数量 ${jsFiles.length} (拆分后的模块化结构)`);

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

/* 5) escapeHtml 存在且被页面模块引用 */
const uiSrc = readFileSync(join(staticDir, 'js', 'ui.js'), 'utf8');
check(/export function escapeHtml/.test(uiSrc), 'ui.js 导出 escapeHtml');

const pageDir = join(staticDir, 'js', 'pages');
const pageFiles = readdirSync(pageDir).filter((f) => f.endsWith('.js'));
let escUsers = 0;
for (const f of pageFiles) {
  const src = readFileSync(join(pageDir, f), 'utf8');
  if (/escapeHtml|escapeAttr|safeUrl/.test(src)) escUsers++;
}
check(escUsers >= 10,
  `pages/*.js 引用 escapeHtml/escapeAttr/safeUrl 的模块数 ${escUsers}/${pageFiles.length}（纯静态展示页豁免）`);
const mustEscape = ['keys.js', 'chat.js', 'logs.js', 'stats.js', 'overview.js', 'feedback.js'];
for (const f of mustEscape) {
  const src = readFileSync(join(pageDir, f), 'utf8');
  check(/escapeHtml|escapeAttr/.test(src), `数据渲染页 ${f} 使用了转义工具`);
}

/* 6) 不引外部 CDN/框架脚本（零构建约束） */
const external = html.match(/<script[^>]+src=["']https?:/gi) || [];
check(external.length === 0, '无外部 CDN 脚本引用');

/* 7) API 契约锚点抽查：关键端点仍按原路径调用 */
const allJs = jsFiles.map((f) => readFileSync(f, 'utf8')).join('\n');
const endpoints = [
  '/api/public/stats', '/api/auth/login', '/api/auth/register', '/api/auth/send-code',
  '/api/auth/reset-password', '/api/auth/logout', '/api/user/profile', '/api/user/stats',
  '/api/user/keys', '/reveal', '/toggle', '/api/chat/models', '/api/chat/history',
  '/api/chat/completions', '/api/user/request-logs', '/api/user/settings',
  '/api/user/feedback', '/api/user/models/status', '/api/user/leaderboard',
  '/api/user/concurrency-stats',
];
const missing = endpoints.filter((ep) => !allJs.includes(ep));
check(missing.length === 0, `API 端点契约齐全 (缺失: ${missing.join(', ') || '无'})`);

/* 8) 路由完整性：旧版全部路由均有注册 */
const routes = ['/', '/login', '/register', '/reset-password', '/models', '/docs', '/qq-groups',
  '/sponsor', '/legal/disclaimer', '/console', '/console/keys', '/console/chat',
  '/console/models', '/console/stats', '/console/docs', '/console/settings',
  '/console/logs', '/console/feedback', '/console/model-status'];
const mainSrc = readFileSync(join(staticDir, 'js', 'main.js'), 'utf8');
const unregistered = routes.filter((r) => !mainSrc.includes(`'${r}'`));
check(unregistered.length === 0, `路由注册齐全 (缺失: ${unregistered.join(', ') || '无'})`);

/* 9) ES 模块 import 路径全部可解析（避免 404 模块导致整站白屏） */
let badImports = 0;
for (const f of jsFiles) {
  const src = readFileSync(f, 'utf8');
  for (const m of src.matchAll(/from\s+['"](\.\.?\/[^'"]+)['"]/g)) {
    const target = resolve(dirname(f), m[1]);
    if (!existsSync(target)) {
      badImports++;
      console.error(`      ↳ ${f} 引用不存在的模块: ${m[1]}`);
    }
  }
}
check(badImports === 0, `模块 import 路径全部可解析 (错误 ${badImports} 处)`);

console.log(failed === 0 ? '\n全部检查通过 ✓' : `\n${failed} 项检查未通过 ✗`);
process.exit(failed === 0 ? 0 : 1);
