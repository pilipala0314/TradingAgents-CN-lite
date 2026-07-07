import re

css_path = r'd:\my-project\take-it-controll-road2freedom\0.start-here\TradingAgents-CN-lite\a-share-research-agent\apps\api\static\style.css'
content = open(css_path, 'r', encoding='utf-8').read()

media_query = """
@media (max-width: 768px) {
  .app-layout {
    flex-direction: column;
  }
  .sidebar {
    width: 100%;
    height: auto;
    border-right: none;
    border-bottom: 1px solid var(--border-light);
  }
  .tab-bar {
    flex-direction: row;
    padding: 12px 16px;
    gap: 12px;
  }
  .tab {
    flex-shrink: 0;
  }
  .nav-divider {
    width: 1px;
    height: 24px;
    margin: 0 4px;
  }
}
"""

if '@media (max-width: 768px)' not in content:
    content += media_query
    open(css_path, 'w', encoding='utf-8').write(content)
    print('Added media queries')
