#!/usr/bin/env python3
"""Run PR A's actual probe task against synthetic CLI states, not real services."""
import argparse
import copy
import os
from pathlib import Path
import subprocess
import tempfile

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', type=Path)
    parser.add_argument('--ansible-playbook', default='ansible-playbook')
    args = parser.parse_args()
    inspection = yaml.safe_load((args.role / 'tasks/pki_inspect.yml').read_text())
    probe = copy.deepcopy(next(t for t in inspection if t['name'] == 'Inspect running configuration and process'))
    running = yaml.safe_load((args.role / 'tasks/install_pki.yml').read_text())[0]['vars']['pki_running']
    cases = [
        dict(name='active', state='active', rc=0, running=True),
        dict(name='unconfigured', state='active', rc=0, configured=False),
        dict(name='inactive', state='inactive', rc=3),
        dict(name='failed', state='failed', rc=3),
        dict(name='unknown unit', state='unknown', rc=4),
        dict(name='activating', state='activating', rc=3, reject=True),
        dict(name='reloading', state='reloading', rc=0, reject=True),
        dict(name='deactivating', state='deactivating', rc=3, reject=True),
        dict(name='maintenance', state='maintenance', rc=3, reject=True),
        dict(name='refreshing', state='refreshing', rc=0, reject=True),
        dict(name='probe error', state='', rc=1, stderr='deliberate-probe-error', reject=True),
        dict(name='zero PID', state='active', rc=0, pid=0, reject=True),
        dict(name='PID query error', state='active', rc=0, show_rc=1, reject=True),
        dict(name='malformed active output', state='active\nextra', rc=0, reject=True),
        dict(name='contradictory status', state='active', rc=3, reject=True),
    ]
    with tempfile.TemporaryDirectory(prefix='pki-probe-states-') as directory:
        root = Path(directory)
        (root / 'bin').mkdir()
        (root / 'configured').mkdir()
        (root / 'configured/config.yml').write_text('{}\n')
        command = root / 'bin/systemctl'
        command.write_text('''#!/bin/sh
case "$1" in
  is-active)
    test -z "$PROBE_STATE" || printf '%s\\n' "$PROBE_STATE"
    test -z "$PROBE_STDERR" || printf '%s\\n' "$PROBE_STDERR" >&2
    exit "$PROBE_RC" ;;
  show)
    if test "$PROBE_SHOW_RC" != 0; then
      printf '%s\\n' 'deliberate-PID-query-error' >&2
      exit "$PROBE_SHOW_RC"
    fi
    printf '%s\\n' "$PROBE_PID" ;;
  *) exit 99 ;;
esac
''')
        command.chmod(0o700)
        tasks = [
            dict(name='Reset expected-failure capture', set_fact=dict(probe_caught=False)),
            dict(name='Run the unchanged production probe task', block=[probe],
                 vars=dict(nebula_config_dir='{{ fixture_root }}/{{ "configured" if probe_case.configured | default(true) else "missing" }}'),
                 environment={
                     'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'],
                     'PROBE_STATE': '{{ probe_case.state }}',
                     'PROBE_RC': '{{ probe_case.rc | string }}',
                     'PROBE_STDERR': '{{ probe_case.stderr | default("") }}',
                     'PROBE_SHOW_RC': '{{ probe_case.show_rc | default(0) | string }}',
                     'PROBE_PID': '{{ probe_case.pid | default(1234) | string }}',
                 }, rescue=[dict(name='Capture rejected probe state', set_fact=dict(probe_caught=True))]),
            dict(name='Assert classification and raw failure output', assert_={
                'that': [
                    'probe_caught == (probe_case.reject | default(false))',
                    'probe_caught or (pki_running == (probe_case.running | default(false)))',
                    'not probe_caught or probe_case.state in pki_process.stdout',
                    'not probe_caught or (probe_case.stderr | default("")) in pki_process.stderr',
                ], 'success_msg': '{{ probe_case.name }} classified as expected',
            }),
        ]
        tasks[-1]['assert'] = tasks[-1].pop('assert_')
        (root / 'case.yml').write_text(yaml.safe_dump(tasks, sort_keys=False))
        play = [dict(hosts='localhost', gather_facts=False, connection='local', vars={
            'fixture_root': str(root), 'pki_service': 'nebula', 'pki_running': running,
            'ansible_facts': {'system': 'Linux'}, 'probe_cases': cases,
        }, tasks=[dict(name='Exercise each service state', include_tasks=str(root / 'case.yml'),
                       loop='{{ probe_cases }}', loop_control={'loop_var': 'probe_case', 'label': '{{ probe_case.name }}'})])]
        (root / 'play.yml').write_text(yaml.safe_dump(play, sort_keys=False))
        subprocess.run([args.ansible_playbook, '-i', 'localhost,', str(root / 'play.yml')], check=True)
    print(f'PASS: {len(cases)} synthetic states using the production Ansible probe; no real service was queried.')


if __name__ == '__main__':
    main()
