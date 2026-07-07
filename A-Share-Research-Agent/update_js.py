import re

js_path = r'd:\my-project\take-it-controll-road2freedom\0.start-here\TradingAgents-CN-lite\a-share-research-agent\apps\api\static\dashboard.js'
content = open(js_path, 'r', encoding='utf-8').read()

old_switch = """function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tabName));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.dataset.tab === tabName));"""

new_switch = """function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((t) => {
    const isActive = t.dataset.tab === tabName;
    t.classList.toggle("active", isActive);
    if (isActive) {
      const titleEl = document.getElementById("current-page-title");
      if (titleEl) titleEl.innerText = t.innerText.trim();
    }
  });
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.dataset.tab === tabName));"""

content = content.replace(old_switch, new_switch)
open(js_path, 'w', encoding='utf-8').write(content)
print('Updated dashboard.js')