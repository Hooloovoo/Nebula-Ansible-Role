#!/usr/bin/env python3
"""Run a rotation playbook from the mesh while observing SSH connectivity."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time

BASE = '/root/nebula-test'
PLAYBOOK = sys.argv[1]
LIMIT = sys.argv[2] if len(sys.argv) > 2 else 'all'
SSH = ['ssh', '-i', BASE + '/id_ed25519', '-o', 'BatchMode=yes',
       '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
       '-o', 'ConnectTimeout=5', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
       'root@10.193.0.2']
events = []
stop = threading.Event()


def probe():
    try:
        return subprocess.run(SSH + ['true'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=8).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def monitor():
    while not stop.is_set():
        timestamp = time.monotonic()
        events.append({'time': timestamp, 'ok': probe()})
        stop.wait(0.5)


def roundtrip(session, payload):
    try:
        session.stdin.write(payload + '\n')
        session.stdin.flush()
        ready, _, _ = select.select([session.stdout], [], [], 5)
        return bool(ready) and session.stdout.readline().strip() == payload
    except (BrokenPipeError, OSError):
        return False


if not probe():
    raise SystemExit('Initial SSH over Nebula failed')
session = subprocess.Popen(SSH + ['cat'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, bufsize=1)
if not roundtrip(session, 'before'):
    raise SystemExit('Persistent SSH session did not start')
thread = threading.Thread(target=monitor, daemon=True)
thread.start()
started = time.monotonic()
environment = os.environ.copy()
environment['ANSIBLE_HOST_KEY_CHECKING'] = 'False'
environment['ANSIBLE_PIPELINING'] = 'True'
environment['ANSIBLE_SSH_ARGS'] = '-o ControlMaster=no -o ControlPersist=no'
with open(BASE + '/ansible.log', 'w') as log:
    try:
        command = ['ansible-playbook', '-i', BASE + '/inventory.json', BASE + '/' + PLAYBOOK]
        if LIMIT != 'all':
            command += ['--limit', LIMIT]
        command += sys.argv[3:]
        play = subprocess.run(command,
                              env=environment, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, timeout=600)
        status = play.returncode
    except subprocess.TimeoutExpired:
        status = 124
finished = time.monotonic()
responsive = roundtrip(session, 'after')
deadline = time.monotonic() + 120
reconnected = False
while time.monotonic() < deadline:
    if probe():
        reconnected = True
        break
    time.sleep(2)
stop.set()
thread.join(timeout=10)
session.terminate()
try:
    session.wait(timeout=5)
except subprocess.TimeoutExpired:
    session.kill()
    session.wait()
Path(BASE + '/result.json').write_text(json.dumps({
    'ansible_exit': status, 'persistent_session_responsive_after_play': responsive,
    'reconnected': reconnected, 'play_seconds': round(finished - started, 2),
    'probes': [{'seconds_from_play_start': round(e['time'] - started, 2), 'ok': e['ok']} for e in events],
}, indent=2) + '\n')
