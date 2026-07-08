"""对照 switchTab case 与 HTML data-tab，找出缺失。"""
import re
import pathlib

REPO = pathlib.Path(r"d:\my-project\take-it-controll-road2freedom\0.start-here\TradingAgents-CN-lite\a-share-research-agent")
js = (REPO / "apps/api/static/dashboard.js").read_text(encoding="utf-8")
html = (REPO / "apps/api/static/index.html").read_text(encoding="utf-8")

# switchTab 函数中的 case 分支
switch_cases = sorted(set(re.findall(r'tabName === "([a-z]+)"', js)))
# HTML 中所有 data-tab
html_tabs = sorted(set(re.findall(r'data-tab="([a-z]+)"', html)))

print("=" * 60)
print("switchTab case 分支:", switch_cases)
print("HTML data-tab:", html_tabs)
print("=" * 60)

# 缺失：HTML 有但 switchTab 没
missing_in_switch = sorted(set(html_tabs) - set(switch_cases))
if missing_in_switch:
    print("⚠️  HTML 有但 switchTab 没有 (用户切到该 tab 时不会拉数据):")
    for t in missing_in_switch:
        print(f"  - {t}")
else:
    print("✅ 全部 HTML tab 都在 switchTab 中")

# 反向：switchTab 有但 HTML 没有
extra_in_switch = sorted(set(switch_cases) - set(html_tabs))
if extra_in_switch:
    print("⚠️  switchTab 有但 HTML 没有 (死代码):")
    for t in extra_in_switch:
        print(f"  - {t}")

# 检查所有 HTML id 在 JS 中是否被引用
html_ids = set(re.findall(r'id="([a-zA-Z][\w-]+)"', html))
js_used_ids = set(re.findall(r'getElementById\("([a-zA-Z][\w-]+)"\)', js))
js_used_ids |= set(re.findall(r"getElementById\('([a-zA-Z][\w-]+)'\)", js))

# JS 引用了但 HTML 不存在
dangling = sorted(js_used_ids - html_ids)
if dangling:
    print(f"\n⚠️  JS 引用了但 HTML 不存在的 ID ({len(dangling)} 个):")
    for i in dangling[:15]:
        print(f"  - {i}")

# HTML 有但 JS 没用的 ID（可能是 decoration 或被其他 JS 引用）
unused = sorted(html_ids - js_used_ids)
# 排除明显是结构性的
ignore = {"brand", "tab-bar", "current-page-title", "health-bar", "topbar", "sidebar", "main-content", "content-scroll", "breadcrumb"}
unused_filtered = [u for u in unused if u not in ignore and not u.startswith("bt-") and not u.startswith("kline")]
if unused_filtered:
    print(f"\nℹ️  HTML 有但 JS 未显式 getElementById 引用的 ID ({len(unused_filtered)} 个):")
    for i in unused_filtered[:10]:
        print(f"  - {i}")
