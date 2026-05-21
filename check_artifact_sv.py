import tarfile, re

tf_path = 'releases/main-dev/deng-tool-rejoin-main-dev.tar.gz'
with tarfile.open(tf_path, 'r:gz') as tf:
    for m in tf.getmembers():
        if m.name.endswith('agent/supervisor.py'):
            art_src = tf.extractfile(m).read().decode('utf-8', errors='replace')
            break

# Exact regex from build_info.py
p1 = re.compile(r"""['"](Joining)['"]\s*[,)]""")
p2 = re.compile(r"""['"](Join Unconfirmed)['"]\s*[,)]""")
print(f'ARTIFACT supervisor.py: {len(art_src.splitlines())} lines')
print()

# Show ALL occurrences of Joining/Join Unconfirmed with context
for pat, name in [(p1, 'Joining'), (p2, 'Join Unconfirmed')]:
    matches = list(pat.finditer(art_src))
    print(f'--- Regex [{name}] matches: {len(matches)} ---')
    for m in matches:
        lno = art_src[:m.start()].count('\n') + 1
        print(f'  line {lno}: {repr(art_src[max(0,m.start()-40):m.end()+40])}')

print()
# Show every line with Joining
print('--- ALL lines containing "Joining" in artifact ---')
for i, ln in enumerate(art_src.splitlines(), 1):
    if 'Joining' in ln:
        stripped = ln.strip()
        is_comment = stripped.startswith('#')
        print(f'  {i}: {"[COMMENT]" if is_comment else "[CODE]"} {ln.rstrip()}')

print()
print('--- ALL lines containing "Join Unconfirmed" in artifact ---')
for i, ln in enumerate(art_src.splitlines(), 1):
    if 'Join Unconfirmed' in ln:
        stripped = ln.strip()
        is_comment = stripped.startswith('#')
        print(f'  {i}: {"[COMMENT]" if is_comment else "[CODE]"} {ln.rstrip()}')
