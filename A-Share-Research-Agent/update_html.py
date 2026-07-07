import re
import sys

html_path = r'd:\my-project\take-it-controll-road2freedom\0.start-here\TradingAgents-CN-lite\a-share-research-agent\apps\api\static\index.html'
content = open(html_path, 'r', encoding='utf-8').read()

new_body_start = """<body>
  <div class="app-layout">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-icon">
          <svg viewBox="0 0 24 24" width="24" height="24" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
        </div>
        <h1>A-Share Agent</h1>
      </div>
      <nav id="tab-bar" class="tab-bar">
        <button class="tab active" data-tab="overview">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
          市场概览
        </button>
        <button class="tab" data-tab="stock">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
          个股分析
        </button>
        <button class="tab" data-tab="compare">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M16 3h5v5"/><path d="M8 3H3v5"/><path d="M12 22v-8.3a4 4 0 0 0-1.172-2.872L3 3"/><path d="m15 9 6-6"/></svg>
          多股对比
        </button>
        <button class="tab" data-tab="topology">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>
          链路大屏
        </button>
        <button class="tab" data-tab="signals">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
          今日信号
        </button>
        <button class="tab" data-tab="backtest">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M21 12a9 9 0 0 1-9 9m9-9a9 9 0 0 0-9-9m9 9H3m9 9a9 9 0 0 1-9-9m9 9c1.66 0 3-4.03 3-9s-1.34-9-3-9m0 18c-1.66 0-3-4.03-3-9s1.34-9 3-9m-9 9a9 9 0 0 1 9-9"/></svg>
          回测引擎
        </button>
        <button class="tab" data-tab="analysis">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M12 20V10"/><path d="M18 20V4"/><path d="M6 20v-4"/></svg>
          高级分析
        </button>
        <div class="nav-divider"></div>
        <button class="tab" data-tab="health">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>
          数据源
        </button>
        <button class="tab" data-tab="alerts">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>
          监控告警
        </button>
        <button class="tab" data-tab="monitor">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
          系统追踪
        </button>
        <button class="tab" data-tab="ops">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path><circle cx="12" cy="12" r="3"></circle></svg>
          运维管理
        </button>
      </nav>
    </aside>

    <div class="main-content">
      <header class="topbar">
        <div class="breadcrumb">
          <span class="text-muted">A-Share-Research-Agent</span>
          <span class="separator">/</span>
          <span id="current-page-title" style="color: var(--text); font-weight: 500;">市场概览</span>
        </div>
        <div id="health-bar"><span class="label">健康度加载中…</span></div>
      </header>

      <main class="content-scroll">"""

content = re.sub(r'<body>[\s\S]*?<main>', new_body_start, content)

new_body_end = """      </main>
    </div>
  </div>

  <script src="/static/dashboard.js"></script>
</body>"""

content = re.sub(r'</main>[\s\S]*?</body>', new_body_end, content)

open(html_path, 'w', encoding='utf-8').write(content)
print('Replaced HTML structure')
