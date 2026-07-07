import re

css_path = r'd:\my-project\take-it-controll-road2freedom\0.start-here\TradingAgents-CN-lite\a-share-research-agent\apps\api\static\style.css'
content = open(css_path, 'r', encoding='utf-8').read()

content = re.sub(r"@import url\('https://fonts\.googleapis\.com.*?\);\n", "", content)

open(css_path, 'w', encoding='utf-8').write(content)
print('Removed Google Fonts')