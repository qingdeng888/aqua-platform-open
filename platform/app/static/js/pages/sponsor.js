// 职责：赞助页——资金用途展示与免责声明（收款码区块已按上游变更移除）

import { setSidebarVisible, highlightNav } from '../layout.js';

export async function renderSponsor() {
  setSidebarVisible(false);
  highlightNav('/sponsor');
  const content = document.getElementById('content-area');
  content.innerHTML = `
    <div class="sponsor-page">
      <div class="sponsor-header">
        <div class="icon">💜</div>
        <h1>支持 AQUA AI平台</h1>
        <p>你的每一份心意，都是让平台持续运转的动力</p>
      </div>

      <div class="sponsor-usage">
        <div class="sponsor-usage-item">
          <div class="pct success">65%</div>
          <div class="lbl">项目运营与维护</div>
          <div class="desc">用于平台日常维护、上游API对接、功能开发、技术迭代、社区运营</div>
        </div>
        <div class="sponsor-usage-item">
          <div class="pct">35%</div>
          <div class="lbl">服务器与带宽</div>
          <div class="desc">用于云服务器租赁、带宽流量、域名解析等基础设施</div>
        </div>
      </div>

      <div class="sponsor-warm">❤️ 心意到就行，多少都可以</div>

      <div class="sponsor-disclaimer">
        <strong>⚠️ 免责声明</strong><br><br>
        1. 本平台为免费开源性质，赞助款项完全用于服务器维护和项目运营，不附加任何回报承诺。<br>
        2. 赞助为自愿行为，赞助金额不限，赞助后不提供退款服务。<br>
        3. 平台运营方保留对赞助资金使用的最终解释权。<br>
        4. 如有任何疑问，请通过QQ群联系管理员。
      </div>
    </div>
  `;
}
