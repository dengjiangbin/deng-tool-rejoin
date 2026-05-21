import re
src = open('agent/supervisor.py', encoding='utf-8').read()

p1 = re.compile(r"""['"](Joining)['"]\s*[,)]""")
p2 = re.compile(r"""['"](Join Unconfirmed)['"]\s*[,)]""")

found = False
for m in p1.finditer(src):
    line_no = src[:m.start()].count('\n') + 1
    print(f'Joining match at line {line_no}: {repr(src[max(0,m.start()-30):m.end()+30])}')
    found = True

for m in p2.finditer(src):
    line_no = src[:m.start()].count('\n') + 1
    print(f'Join Unconfirmed match at line {line_no}: {repr(src[max(0,m.start()-30):m.end()+30])}')
    found = True

if not found:
    print('LOCAL: no matches - doctor check PASSES locally')

# Also grep-check (what installer does)
lines = src.split('\n')
joining_noncomment = [
    (i+1, ln) for i, ln in enumerate(lines)
    if 'Joining' in ln and not ln.strip().startswith('#')
]
print(f'\nNon-comment lines with Joining ({len(joining_noncomment)} found):')
for no, ln in joining_noncomment[:15]:
    print(f'  {no}: {ln.rstrip()}')

# Check ARTIFACT supervisor
import tarfile, io
tf_path = 'releases/main-dev/deng-tool-rejoin-main-dev.tar.gz'
try:
    with tarfile.open(tf_path, 'r:gz') as tf:
        for member in tf.getmembers():
            if member.name.endswith('agent/supervisor.py'):
                art_src = tf.extractfile(member).read().decode('utf-8', errors='replace')
                a1 = p1.findall(art_src)
                a2 = p2.findall(art_src)
                print(f'\nARTIFACT supervisor - Joining matches: {a1}')
                print(f'ARTIFACT supervisor - Join Unconfirmed matches: {a2}')
                # Show non-comment joining lines
                art_lines = art_src.split('\n')
                art_nc = [(i+1, ln) for i, ln in enumerate(art_lines)
                          if 'Joining' in ln and not ln.strip().startswith('#')]
                print(f'Artifact non-comment Joining lines: {len(art_nc)}')
                for no, ln in art_nc[:10]:
                    print(f'  {no}: {ln.rstrip()}')
                break
except Exception as e:
    print(f'Could not open artifact: {e}')
