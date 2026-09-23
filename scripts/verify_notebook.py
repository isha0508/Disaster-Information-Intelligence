import json

with open('notebooks/kaggle_distilbert_humanitarian.ipynb') as f:
    nb = json.load(f)

cells     = nb['cells']
code_cells = [c for c in cells if c['cell_type'] == 'code']
md_cells   = [c for c in cells if c['cell_type'] == 'markdown']

print(f'Valid JSON     : YES')
print(f'Total cells    : {len(cells)}')
print(f'Code cells     : {len(code_cells)}')
print(f'Markdown cells : {len(md_cells)}')
print(f'Kernel         : {nb["metadata"]["kernelspec"]["name"]}')
print(f'GPU accel      : {nb["metadata"]["kaggle"]["accelerator"]}')
print(f'Internet       : {nb["metadata"]["kaggle"]["isInternetEnabled"]}')
print()

for c in cells:
    ctype = c['cell_type']
    cid   = c.get('id', '?')
    src   = c['source']
    first = (src[0] if isinstance(src, list) and src
             else src[:70] if isinstance(src, str)
             else '').strip()[:68]
    print(f'  [{ctype:8s}]  {cid:<35}  {first}')
