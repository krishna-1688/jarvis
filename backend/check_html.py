with open('login_page.html', 'r', encoding='utf-8') as f:
    html = f.read()

import re
img_pattern = re.compile(r'src="data:.*;base64,(.*?)"')
img = re.search(img_pattern, html)
print("Found:", img is not None)
if img:
    print("Length:", len(img.group(1)))