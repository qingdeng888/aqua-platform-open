// 职责：API文档页（公开版与控制台版共用内容）——基础信息/认证/对话补全/SDK示例/向量嵌入/模型列表/热门模型/IDE集成/错误码

import { Icons } from '../icons.js';
import { ensureAuth } from '../auth.js';
import { escapeAttr } from '../ui.js';
import { setSidebarVisible, highlightNav, updateUserInfo } from '../layout.js';

const BASE_URL = location.origin + '/v1';
const DEFAULT_MODEL = 'deepseek-ai/deepseek-v4-flash';

/** 首页API文档（不需要登录） */
export async function renderDocs() {
  setSidebarVisible(false);
  highlightNav('/docs');
  document.getElementById('content-area').innerHTML = docsContent();
}

/** 控制台API文档（需要登录，有侧边栏） */
export async function renderConsoleDocs() {
  if (!(await ensureAuth())) return;
  updateUserInfo();
  setSidebarVisible(true);
  highlightNav('/console/docs');
  document.getElementById('content-area').innerHTML = docsContent();
}

function copyBtn(targetId) {
  return `<button class="btn xs" data-copy-target="${targetId}" data-copy-msg="代码已复制">${Icons.copy(12)} 复制</button>`;
}

function docsContent() {
  const base = escapeAttr(BASE_URL);
  return `
    <div class="page">
      <div class="page-header">
        <h1 class="page-title">API文档</h1>
        <p class="page-subtitle">OpenAI 兼容接口，支持所有主流 SDK 和 IDE</p>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">基础信息</h3></div>
        <div class="api-urls">
          <div class="api-url-row">
            <span class="api-url-label">Base URL:</span>
            <code>${BASE_URL}</code>
            <button class="btn xs" data-copy="${base}">${Icons.copy(12)} 复制</button>
          </div>
          <div class="api-url-row">
            <span class="api-url-label">完整端点:</span>
            <code>${BASE_URL}/chat/completions</code>
            <button class="btn xs" data-copy="${base}/chat/completions">${Icons.copy(12)} 复制</button>
          </div>
          <div class="api-url-row">
            <span class="api-url-label">兼容路径:</span>
            <code>/v1/...</code> 和 <code>/api/v1/...</code> 均可使用
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">认证方式</h3></div>
        <p class="docs-p">使用 Bearer Token 认证，在请求头中添加 <code>Authorization: Bearer YOUR_API_KEY</code></p>
        <p class="docs-p text-muted">也支持 <code>x-api-key: YOUR_API_KEY</code> 方式</p>
        <div class="docs-intro-box">
          <div style="margin-bottom:6px;font-weight:600">获取 API 密钥：</div>
          <ol class="docs-ol">
            <li>进入 <a href="#/console/keys">密钥管理</a> 页面</li>
            <li>点击「创建密钥」按钮</li>
            <li>复制生成的密钥（以 <code>sk-</code> 开头，旧 <code>acu_</code> 密钥仍可使用）</li>
            <li>每个用户最多创建 5 个密钥</li>
          </ol>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">对话补全 API</h3></div>
        <p class="docs-p" style="margin-bottom:12px"><code>POST /v1/chat/completions</code> — 支持流式和非流式响应</p>
        <div class="code-block">
          <div class="code-header"><span>cURL</span>${copyBtn('code-chat')}</div>
          <pre id="code-chat"><code>curl ${BASE_URL}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -d '{
    "model": "${DEFAULT_MODEL}",
    "messages": [
      {"role": "system", "content": "你是一个有用的助手"},
      {"role": "user", "content": "你好"}
    ],
    "stream": true,
    "max_tokens": 4096
  }'</code></pre>
        </div>
        <div style="margin-top:16px">
          <h4 class="docs-h4">请求参数</h4>
          <div style="overflow-x:auto">
            <table class="docs-table">
              <thead><tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
              <tbody>
                <tr><td><code>model</code></td><td>string</td><td>是</td><td>模型ID，如 ${DEFAULT_MODEL}</td></tr>
                <tr><td><code>messages</code></td><td>array</td><td>是</td><td>消息数组，含 role 和 content</td></tr>
                <tr><td><code>stream</code></td><td>boolean</td><td>否</td><td>是否流式输出，默认 false</td></tr>
                <tr><td><code>max_tokens</code></td><td>integer</td><td>否</td><td>最大生成Token数</td></tr>
                <tr><td><code>temperature</code></td><td>float</td><td>否</td><td>温度参数 0-2，默认 1</td></tr>
                <tr><td><code>top_p</code></td><td>float</td><td>否</td><td>核采样参数 0-1，默认 1</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">Python SDK 示例</h3></div>
        <div class="code-block">
          <div class="code-header"><span>Python (openai&gt;=1.0)</span>${copyBtn('code-python')}</div>
          <pre id="code-python"><code>pip install openai

from openai import OpenAI

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="${BASE_URL}"
)

# 非流式
response = client.chat.completions.create(
    model="${DEFAULT_MODEL}",
    messages=[{"role": "user", "content": "你好"}],
)
print(response.choices[0].message.content)

# 流式
response = client.chat.completions.create(
    model="${DEFAULT_MODEL}",
    messages=[{"role": "user", "content": "你好"}],
    stream=True
)
for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")</code></pre>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">Node.js SDK 示例</h3></div>
        <div class="code-block">
          <div class="code-header"><span>JavaScript (openai&gt;=4.0)</span>${copyBtn('code-js')}</div>
          <pre id="code-js"><code>npm install openai

import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "YOUR_API_KEY",
  baseURL: "${BASE_URL}",
});

const response = await client.chat.completions.create({
  model: "${DEFAULT_MODEL}",
  messages: [{ role: "user", content: "你好" }],
  stream: true,
});

for await (const chunk of response) {
  process.stdout.write(chunk.choices[0]?.delta?.content || "");
}</code></pre>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">向量嵌入 API</h3></div>
        <p class="docs-p" style="margin-bottom:12px"><code>POST /v1/embeddings</code> — 文本向量化</p>
        <div class="code-block">
          <div class="code-header"><span>Python</span>${copyBtn('code-embed')}</div>
          <pre id="code-embed"><code>response = client.embeddings.create(
    model="nvidia/llama-3.1-8b-instruct",
    input="Hello world"
)
print(response.data[0].embedding)</code></pre>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">模型列表 API</h3></div>
        <div class="code-block">
          <div class="code-header"><span>GET /v1/models</span>${copyBtn('code-models')}</div>
          <pre id="code-models"><code>curl ${BASE_URL}/models \\
  -H "Authorization: Bearer YOUR_API_KEY"</code></pre>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">热门模型</h3></div>
        <div style="overflow-x:auto">
          <table class="docs-table">
            <thead><tr><th>模型ID</th><th>厂商</th><th>能力</th></tr></thead>
            <tbody>
              <tr><td><code>deepseek-ai/deepseek-v4-flash</code></td><td>DeepSeek</td><td>推理 · 1M上下文</td></tr>
              <tr><td><code>deepseek-ai/deepseek-v4-pro</code></td><td>DeepSeek</td><td>推理 · 1M上下文</td></tr>
              <tr><td><code>z-ai/glm-5.2</code></td><td>智谱</td><td>推理 · Agentic</td></tr>
              <tr><td><code>minimaxai/minimax-m3</code></td><td>MiniMax</td><td>推理 · 视觉 · 1M上下文</td></tr>
              <tr><td><code>openai/gpt-oss-120b</code></td><td>OpenAI</td><td>推理</td></tr>
              <tr><td><code>moonshotai/kimi-k2.6</code></td><td>月之暗面</td><td>推理 · 视觉 · Agentic</td></tr>
              <tr><td><code>qwen/qwen3.5-397b-a17b</code></td><td>千问</td><td>推理 · 视觉 · 1M上下文</td></tr>
              <tr><td><code>meta/llama-4-maverick-17b-128e-instruct</code></td><td>Meta</td><td>推理 · 视觉 · 1M上下文</td></tr>
              <tr><td><code>nvidia/nemotron-3-ultra-550b-a55b</code></td><td>NVIDIA</td><td>推理 · 1M上下文</td></tr>
              <tr><td><code>stepfun-ai/step-3.7-flash</code></td><td>阶跃星辰</td><td>推理 · 视觉</td></tr>
              <tr><td><code>mistralai/mistral-medium-3.5-128b</code></td><td>Mistral</td><td>推理</td></tr>
              <tr><td><code>google/gemma-4-31b-it</code></td><td>Google</td><td>推理</td></tr>
            </tbody>
          </table>
        </div>
        <p class="docs-note">完整可用模型列表请查看 <a href="#/console/models">模型列表</a> 页面</p>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">IDE 集成指南</h3></div>
        <div class="docs-p" style="line-height:2">
          <div style="margin-bottom:12px">
            <h4 class="docs-card-h4">Cursor / CC Switch</h4>
            <p class="docs-card-p">Settings → Models → OpenAI API Base URL: <code>${BASE_URL}</code></p>
            <p class="docs-card-p">API Key: 填入你的 AQUA 密钥</p>
          </div>
          <div style="margin-bottom:12px">
            <h4 class="docs-card-h4">Continue / Copilot</h4>
            <p class="docs-card-p">配置文件中设置 <code>apiBase: "${BASE_URL}"</code></p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="card-title">错误码说明</h3></div>
        <div style="overflow-x:auto">
          <table class="docs-table">
            <thead><tr><th>HTTP状态码</th><th>错误码</th><th>说明</th></tr></thead>
            <tbody>
              <tr><td>401</td><td><code>invalid_api_key</code></td><td>API密钥无效或已禁用</td></tr>
              <tr><td>400</td><td><code>invalid_json</code></td><td>请求体JSON格式错误</td></tr>
              <tr><td>400</td><td><code>invalid_request_error</code></td><td>请求参数错误</td></tr>
              <tr><td>403</td><td><code>no_api_key</code></td><td>没有可用的API密钥</td></tr>
              <tr><td>502</td><td><code>upstream_timeout</code></td><td>上游服务超时</td></tr>
              <tr><td>503</td><td><code>all_keys_exhausted</code></td><td>所有上游密钥耗尽</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}
