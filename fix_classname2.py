# -*- coding: utf-8 -*-
path = r'C:/Users/1000302853/Desktop/Japan-university-entrance-examination-information-collection-website/web/nyushi-navi/src/app/page.tsx'
content = open(path, encoding='utf-8').read()

# className2 の誤記を修正
if 'className2' in content:
    idx = content.find('className2')
    print('found className2 at:', idx)
    print(repr(content[idx-100:idx+100]))
    # className2="..." を削除
    import re
    content = re.sub(r'\s*className2="[^"]*"', '', content)
    open(path, 'w', encoding='utf-8').write(content)
    print('className2 removed')
else:
    print('className2 not found in page.tsx')

# 他のファイルも確認
import os
for root, dirs, files in os.walk(r'C:/Users/1000302853/Desktop/Japan-university-entrance-examination-information-collection-website/web/nyushi-navi/src'):
    for f in files:
        if f.endswith('.tsx') or f.endswith('.ts'):
            fp = os.path.join(root, f)
            c = open(fp, encoding='utf-8').read()
            if 'className2' in c:
                print(f'Found in: {fp}')
