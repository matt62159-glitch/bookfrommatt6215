"""전체 기능판(ppt-helper-full.html) 만들기: index.html에 JSZip과 Anthropic SDK를 넣어 한 파일로 묶는다.
사용: python3 build.py  (ppt-helper 폴더에서)"""
import pathlib
here = pathlib.Path(__file__).parent
page = (here / 'index.html').read_text(encoding='utf-8')
jszip = (here / 'vendor/jszip-3.10.1.min.js').read_text(encoding='utf-8')
sdk = (here / 'vendor/anthropic-sdk-0.131.0.min.js').read_text(encoding='utf-8')
tag = '<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>'
assert tag in page and '</script' not in jszip + sdk
page = page.replace(tag, f'<script>{jszip}</script>\n<script>{sdk}</script>')
out = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
       '<style>:root{color-scheme:light dark}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
       '</head>\n<body>\n' + page + '\n</body>\n</html>\n')
(here / 'ppt-helper-full.html').write_text(out, encoding='utf-8')
print('ppt-helper-full.html', len(out.encode()) // 1024, 'KB')
