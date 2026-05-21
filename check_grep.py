import re, tarfile

tf_path = 'releases/main-dev/deng-tool-rejoin-main-dev.tar.gz'
with tarfile.open(tf_path, 'r:gz') as tf:
    for m in tf.getmembers():
        if m.name.endswith('agent/supervisor.py'):
            art_src = tf.extractfile(m).read().decode('utf-8', errors='replace')
            break

# New precise grep pattern (equivalent in Python regex):
# grep -qE '"(Joining)"[[:space:]]*[,)]'
p1 = re.compile(r'"(Joining)"\s*[,)]')
p2 = re.compile(r'"(Join Unconfirmed)"\s*[,)]')

m1 = p1.search(art_src)
m2 = p2.search(art_src)
print('Artifact supervisor:')
print('  Joining match:', 'FOUND: ' + repr(m1.group()) if m1 else 'NO MATCH - correct, no false positive')
print('  Join Unconfirmed match:', 'FOUND: ' + repr(m2.group()) if m2 else 'NO MATCH - correct')
print('  Old Smart Detection:', 'YES (WARNING)' if (m1 or m2) else 'NO')
print()

src = open('agent/supervisor.py', encoding='utf-8').read()
m1d = p1.search(src)
m2d = p2.search(src)
print('Dev supervisor:')
print('  Joining match:', 'FOUND: ' + repr(m1d.group()) if m1d else 'NO MATCH - correct')
print('  Join Unconfirmed match:', 'FOUND: ' + repr(m2d.group()) if m2d else 'NO MATCH - correct')

# Test that old false-positive lines do NOT match
test_lines = [
    'STATUS_JOINING           = "Joining"           # Deep-link',
    'STATUS_JOIN_UNCONFIRMED  = "Join Unconfirmed"  # comment',
    'self.launching_since = None  # when Launching/Joining was set',
]
print()
print('False-positive sanity checks:')
for ln in test_lines:
    m = p1.search(ln) or p2.search(ln)
    print('  ' + ('WOULD MATCH (BAD)' if m else 'no match (GOOD)') + ': ' + repr(ln[:60]))
