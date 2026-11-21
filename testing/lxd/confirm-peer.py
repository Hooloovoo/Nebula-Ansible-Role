#!/usr/bin/env python3
"""Test-controller probe: require a fresh tunnel presenting the intended leaf."""
import json
import subprocess
import sys
import time

BASE = '/root/nebula-test'
ADDRESSES = {'lh': '10.193.0.1', 'server': '10.193.0.2', 'client': '10.193.0.3'}
COMMON = ['ssh', '-i', BASE + '/id_ed25519', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
          '-o', 'UserKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=5', '-o', 'ControlMaster=no', '-o', 'ControlPath=none']


def output(args):
    return subprocess.check_output(args, text=True, timeout=20)


host = sys.argv[1]
if host == 'client':
    intended = output(['/opt/nebula/nebula-cert', 'print', '-json', '-path', '/opt/nebula/client.crt'])
else:
    intended = output(COMMON + ['root@' + ADDRESSES[host],
                                '/opt/nebula/nebula-cert print -json -path /opt/nebula/' + host + '.crt'])
selected = json.loads(intended)
expected = (selected[0] if isinstance(selected, list) else selected)['fingerprint']
peer = 'server' if host == 'client' else host
address = ADDRESSES[peer]
debug = COMMON + ['-p', '2222', 'debug@127.0.0.1']
proxy = ' '.join(COMMON + ['-W', '127.0.0.1:2222', 'root@10.193.0.2'])


def observed():
    if host == 'client':
        return json.loads(output(COMMON + ['-o', 'ProxyCommand=' + proxy, '-p', '2222', 'debug@localhost',
                                           'print-tunnel 10.193.0.3']))
    return json.loads(output(debug + ['print-tunnel ' + address]))


try:
    before = observed().get('localIndex')
except (subprocess.SubprocessError, ValueError):
    before = None
last = None
for attempt in range(10):
    # Multiple completed HostInfos can coexist. Closing the primary can promote
    # an older sibling; do not mistake that for a fresh, updated handshake.
    try:
        output(debug + ['close-tunnel ' + address])
        output(COMMON + ['root@' + address, 'true'])
        last = observed()
        cert = last['cert'][0] if isinstance(last['cert'], list) else last['cert']
        if last.get('localIndex') and last['localIndex'] != before and cert['fingerprint'] == expected:
            print('Fresh handshake confirmed', host, expected, 'index', before, '->', last['localIndex'])
            raise SystemExit(0)
    except (subprocess.SubprocessError, ValueError, KeyError):
        pass
    time.sleep(0.5)
raise SystemExit('No completed updated handshake observed: ' + json.dumps(last))
