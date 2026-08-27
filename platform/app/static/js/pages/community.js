// 职责：QQ交流群页——三个群卡片（群号复制/一键加群）与加群须知

import { escapeHtml } from '../ui.js';
import { setSidebarVisible, highlightNav } from '../layout.js';

export async function renderQQGroups() {
  setSidebarVisible(false);
  highlightNav('/qq-groups');
  const content = document.getElementById('content-area');

  // QQ群数据（预留扩展：新增群只需在数组中追加）
  const groups = [
    {
      name: 'AQUA AI 三群',
      number: '1073851223',
      link: 'https://qm.qq.com/q/jGnpIQWY5c',
      members: '新群',
      gradient: 'linear-gradient(135deg, #00d4ff, #0099cc)',
    },
    {
      name: 'AQUA AI 二群',
      number: '957919628',
      link: 'https://qm.qq.com/q/gCxec4AezK',
      members: '200人',
      gradient: 'linear-gradient(135deg, #6c757d, #495057)',
    },
    {
      name: 'AQUA AI 一群',
      number: '1038910590',
      link: 'https://qm.qq.com/q/5e4t2bCK',
      members: '200人',
      gradient: 'linear-gradient(135deg, #12b7f5, #0d96d6)',
    },
  ];

  content.innerHTML = `
    <div class="qq-page">
      <div class="qq-page-header">
        <div class="icon">💬</div>
        <h1>加入 AQUA AI 交流群</h1>
        <p>与开发者和用户一起交流AI使用技巧、反馈问题、获取最新动态</p>
      </div>

      <div class="qq-list">
        ${groups.map((g) => `
          <div class="qq-card">
            <div class="qq-card-head" style="background:${g.gradient}">
              <div class="qq-bubble-1"></div>
              <div class="qq-bubble-2"></div>
              <h2>${escapeHtml(g.name)}</h2>
            </div>
            <div class="qq-card-body">
              <div class="qq-card-row">
                <div>
                  <div class="qq-label">群号</div>
                  <div class="qq-number-row">
                    <code class="qq-number">${escapeHtml(g.number)}</code>
                    <button class="btn sm" data-copy="${escapeHtml(g.number)}" data-copy-msg="群号已复制" style="padding:4px 10px;font-size:12px">复制</button>
                  </div>
                </div>
                <div class="qq-side">
                  <div class="qq-members">
                    <div class="lbl">成员</div>
                    <div class="val">${escapeHtml(g.members)}</div>
                  </div>
                  <a href="${escapeHtml(g.link)}" target="_blank" rel="noopener noreferrer" class="btn lg btn-qq-join-lg" style="background:${g.gradient}">立即加入 →</a>
                </div>
              </div>
            </div>
          </div>
        `).join('')}
      </div>

      <div class="qq-note">
        <h3>📖 加群须知</h3>
        <ul>
          <li>入群后请阅读群公告，遵守群规</li>
          <li>遇到问题请先查看 <a href="#/docs">API文档</a> 和 <a href="#/models">模型列表</a></li>
          <li>反馈问题时请提供：使用的模型、错误信息、请求参数</li>
          <li>一群、二群已满员，请加入三群，三个群内容同步</li>
        </ul>
      </div>
    </div>
  `;
}
