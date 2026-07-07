import re

js_path = r'd:\my-project\take-it-controll-road2freedom\0.start-here\TradingAgents-CN-lite\a-share-research-agent\apps\api\static\dashboard.js'
content = open(js_path, 'r', encoding='utf-8').read()

chart_defaults = """
// --- Chart.js Global Defaults for Dark Theme ---
if (window.Chart) {
  Chart.defaults.color = '#94a3b8';
  if (Chart.defaults.scale && Chart.defaults.scale.grid) {
    Chart.defaults.scale.grid.color = '#1e293b';
  }
  Chart.defaults.borderColor = '#334155';
  Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
  if (Chart.defaults.plugins && Chart.defaults.plugins.tooltip) {
    Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(15, 23, 42, 0.9)';
    Chart.defaults.plugins.tooltip.titleColor = '#f8fafc';
    Chart.defaults.plugins.tooltip.bodyColor = '#e2e8f0';
    Chart.defaults.plugins.tooltip.borderColor = '#334155';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
  }
  if (Chart.defaults.elements && Chart.defaults.elements.line) {
    Chart.defaults.elements.line.tension = 0.3; // smoother curves
  }
}
"""

if 'Chart.defaults.color' not in content:
    content = content.replace('let _klineChart = null;', chart_defaults + '\nlet _klineChart = null;')
    open(js_path, 'w', encoding='utf-8').write(content)
    print('Chart.js defaults added')