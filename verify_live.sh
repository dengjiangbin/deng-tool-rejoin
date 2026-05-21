#!/bin/sh
curl -fsSL https://rejoin.deng.my.id/install/test/latest -o /tmp/rv3.sh 2>&1
sh -n /tmp/rv3.sh && echo "sh-n: PASS"
bash -n /tmp/rv3.sh && echo "bash-n: PASS"
DC=$(grep -c "doctor install" /tmp/rv3.sh 2>/dev/null)
echo "doctor_install_occurrences: $DC (must be 0)"
echo "Joining grep lines:"
grep 'grep.*Joining' /tmp/rv3.sh || echo "(none - OK if using qE pattern)"
grep 'qE.*Joining' /tmp/rv3.sh
echo "Old Smart Detection line:"
grep '_OLD_STATES' /tmp/rv3.sh | head -3
echo "Probe ID from SHA:"
grep 'EXPECTED_SHA256' /tmp/rv3.sh | head -1
