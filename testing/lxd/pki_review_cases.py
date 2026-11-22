"""Regression cases for PR A's preflight and incomplete-recovery boundaries."""
import json
import base64
import re
import subprocess

from series_cases import play


def process(s):
    return s.guest('server', 'systemctl', 'show', 'nebula', '-p', 'MainPID', '--value').stdout


def state(s):
    return s.ca_hashes('server', 'server.crt', 'server.key', 'ca.crt'), process(s)


def backup_dirs(s):
    return json.loads(s.guest('server', 'python3', '-c',
                             'import glob,json; print(json.dumps(glob.glob("/tmp/nebula-pki-*")))').stdout)


def drift(s):
    s.guest('server', 'ip', 'address', 'del', '10.193.0.2/24', 'dev', 'neb0')
    s.guest('server', 'ip', 'address', 'add', '10.193.0.9/24', 'dev', 'neb0')
    before = state(s)
    for changing in (False, True):
        inventory = s.inventory()
        data = json.loads(inventory.read_text())
        if changing:
            data['all']['children']['nodes']['hosts']['server']['nebula_cert_groups'] = ['new-request']
        inventory.write_text(json.dumps(data))
        status, output = play(s, inventory, 'server', check=False)
        label = 'changed PKI' if changing else 'unchanged PKI'
        s.check('runtime drift rejects ' + label + ' before installation', status != 0 and 'runtime drift' in output.lower())
        s.check('runtime drift preserves live files and process for ' + label, before == state(s))
        addresses = s.guest('server', 'ip', '-json', 'address', 'show', 'dev', 'neb0').stdout
        s.check('runtime drift does not restart or repair the interface for ' + label,
                any(a['local'] == '10.193.0.9' for link in json.loads(addresses) for a in link['addr_info']))
        s.check('runtime drift creates no backup for ' + label, not backup_dirs(s))


def recovery(s):
    before = state(s)
    s.guest('server', 'cp', '-p', '/opt/nebula/server.key', '/opt/nebula/manual-recovery.key')
    s.guest('server', 'mkdir', '-p', '/opt/nebula/test-bin')
    wrapper = ('#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\na=sys.argv[1:]\n'
               'if len(a)>3 and a[3].startswith("/tmp/nebula-pki-") and Path("/run/nebula-backup-fail").exists():\n'
               ' Path(a[3]).write_text("partial snapshot")\n'
               ' Path("/run/nebula-backup-injected").touch()\n sys.exit(44)\n'
               'if len(a)>2 and a[2].startswith("/tmp/nebula-pki-"):\n'
               ' Path("/run/nebula-restore-injected").touch()\n sys.exit(43)\n'
               'os.execv("/usr/bin/cp",["cp"]+a)\n')
    s.guest('server', 'python3', '-c', 'from pathlib import Path; import sys; p=Path("/opt/nebula/test-bin/cp"); '
            'p.write_text(sys.argv[1]); p.chmod(0o700)', wrapper)
    retained = []
    try:
        inputs = {'certificate_name': 'server', 'wrong_key': 'client.key',
                  'test_command_path': '/opt/nebula/test-bin:/usr/sbin:/usr/bin:/sbin:/bin'}
        s.guest('server', 'touch', '/run/nebula-backup-fail')
        status, output = play(s, s.inventory(), 'server', playbook='safe-apply.yml', check=False,
                             extra=['-e', json.dumps(inputs)])
        s.check('partial backup failure never restores incomplete originals over live files', status != 0 and
                state(s) == before and
                s.guest('server', 'test', '-e', '/run/nebula-backup-injected', check=False).returncode == 0 and
                s.guest('server', 'test', '!', '-e', '/run/nebula-restore-injected', check=False).returncode == 0)
        s.check('partial backup failure cleans disposable files and masks sensitive task output',
                not backup_dirs(s) and 'hidden by no_log' in output)
        s.guest('server', 'rm', '/run/nebula-backup-fail')
        status, output = play(s, s.inventory(), 'server', playbook='safe-apply.yml', check=False,
                             extra=['-e', json.dumps(inputs)])
        s.check('restore failure fixture reaches real validation and attempted restoration', status != 0 and
                'Validate complete PKI set' in output and
                s.guest('server', 'test', '-e', '/run/nebula-restore-injected', check=False).returncode == 0)
        retained = backup_dirs(s)
        s.check('incomplete recovery retains a root-only original-key backup', len(retained) == 1 and
                s.guest('server', 'stat', '-c', '%a:%u', retained[0]).stdout.strip() == '700:0' and
                s.guest('server', 'sha256sum', retained[0] + '/server.key').stdout.split()[0] == before[0][1])
        s.check('incomplete recovery reports its location and original validation cause',
                retained[0] in output and 'recovery' in output.lower() and 'pair' in output.lower())
        s.check('failed restoration leaves the original process running', process(s) == before[1])
    finally:
        # Independent operator recovery, bypassing the deliberately broken cp.
        source = retained[0] + '/server.key' if retained else '/opt/nebula/manual-recovery.key'
        s.guest('server', '/usr/bin/cp', '-p', source, '/opt/nebula/server.key')
        for directory in backup_dirs(s):
            s.guest('server', 'rm', '-rf', '--', directory)
    s.check('retained backup permits byte-identical independent recovery', state(s) == before)
    status, output = play(s, s.inventory(), 'server')
    s.check('a later include resets recovery state and converges without activation', status == 0 and
            state(s) == before and not backup_dirs(s))


def probe(s):
    before = state(s)
    names = ('server.crt', 'server.key', 'ca.crt')
    for name in names:
        s.guest('server', 'cp', '-p', '/opt/nebula/' + name, '/opt/nebula/probe-original-' + name)
    s.guest('server', 'mkdir', '-p', '/opt/nebula/test-bin')
    wrapper = ('#!/bin/sh\nif [ "$1" = "is-active" ]; then\n'
               ' printf "%s\\n" "deliberate-process-probe-failure" >&2\n exit 1\nfi\n'
               'exec /usr/bin/systemctl "$@"\n')
    s.guest('server', 'python3', '-c', 'from pathlib import Path; import sys; '
            'p=Path("/opt/nebula/test-bin/systemctl"); p.write_text(sys.argv[1]); p.chmod(0o700)', wrapper)
    try:
        status, output = play(s, s.inventory(), 'server', playbook='safe-apply.yml', check=False,
                             extra=['-e', json.dumps({'certificate_name': 'server', 'wrong_key': 'client.key',
                                    'test_command_path': '/opt/nebula/test-bin:/usr/sbin:/usr/bin:/sbin:/bin'})])
        s.check('unexpected process-probe failure stops before unvalidated publication', status != 0 and
                'deliberate-process-probe-failure' in output)
        s.check('unexpected process-probe failure preserves files and the running process', state(s) == before)
        s.check('unexpected process-probe failure creates no backup', not backup_dirs(s))
    finally:
        for name in names:
            s.guest('server', '/usr/bin/cp', '-p', '/opt/nebula/probe-original-' + name, '/opt/nebula/' + name)
        s.guest('server', 'rm', '/opt/nebula/test-bin/systemctl')
    s.guest('server', 'systemctl', 'stop', 'nebula')
    s.groups['server'].append('inactive-case')
    status, _ = play(s, s.inventory(), 'server')
    s.check('stopped service keeps the normal copy-and-start path', status == 0 and
            int(process(s)) > 0 and process(s) != before[1] and state(s)[0] != before[0])
    s.check('stopped service installation creates no transaction backup', not backup_dirs(s))


def validation(s):
    fixture = s.guest('lh', 'python3', '-c',
                      'import tempfile; print(tempfile.mkdtemp(prefix="pki-review-",dir="/opt/nebula"))').stdout.strip()

    def issue(address):
        s.guest('lh', 'rm', '-f', fixture + '/server.crt', fixture + '/server.key')
        s.guest('lh', '/opt/nebula/nebula-cert', 'sign', '-ca-crt', '/opt/nebula/ca.crt',
                '-ca-key', '/opt/nebula/ca.key', '-name', 'server', '-ip', address + '/24',
                '-groups', 'review', '-out-crt', fixture + '/server.crt', '-out-key', fixture + '/server.key')

    issue('10.193.0.2')
    before = state(s)
    binary = '/opt/nebula/nebula'
    s.guest('server', 'mv', binary, binary + '.review-real')
    try:
        wrapper = ('#!/bin/sh\nif [ "$1" = "-test" ]; then\n'
                   ' printf "%s\\n" "deliberate-nebula-test-failure"\n exit 42\nfi\n'
                   'exec /opt/nebula/nebula.review-real "$@"\n')
        s.guest('server', 'python3', '-c', 'from pathlib import Path; import sys; p=Path(sys.argv[1]); '
                'p.write_text(sys.argv[2]); p.chmod(0o700)', binary, wrapper)
        status, output = play(s, s.inventory(), 'server', playbook='safe-apply.yml', check=False,
                             extra=['-vvv', '-e', json.dumps({'certificate_name': 'server', 'test_leaf_dir': fixture})])
        s.check('independent nebula -test failure retains its stdout diagnostic', status != 0 and
                'Validate complete PKI set' in output and 'deliberate-nebula-test-failure' in output)
        s.check('independent nebula -test failure restores bytes and process', state(s) == before and not backup_dirs(s))
        # Check payloads in memory, without logging or saving private key content.
        key = subprocess.check_output(s.lxc('exec', s.args.remote + ':' + s.names['lh']) +
                                      ['--', 'cat', fixture + '/server.key'])
        body = b''.join(key.splitlines()[1:-1]).decode()
        s.check('verbose failure output hides raw and slurped private key payloads',
                key.decode() not in output and body not in output and base64.b64encode(key).decode() not in output)
        del key
    finally:
        s.guest('server', 'mv', binary + '.review-real', binary)

    status, output = play(s, s.inventory(), 'server', playbook='safe-apply.yml',
                         extra=['-e', json.dumps({'certificate_name': 'server', 'test_leaf_dir': fixture, 'repeat_apply': True})])
    s.check('same-play repeated includes publish compatible PKI and preserve the process',
            status == 0 and state(s)[0] != before[0] and process(s) == before[1])
    s.check('same-play repeated includes back up and activate only once',
            len(re.findall(r'TASK \[.* : Reset state owned by this installer include\]', output)) == 2 and
            len(re.findall(r'TASK \[.* : Create root-only backup directory\]', output)) == 1 and
            len(re.findall(r'TASK \[.* : Activate PKI using the selected operation\]', output)) == 1 and not backup_dirs(s))

    config = s.config('server')
    config['tun']['disabled'] = True
    s.guest('server', 'python3', '-c', 'from pathlib import Path; import sys; '
            'Path("/opt/nebula/config.yml").write_text(sys.argv[1])', json.dumps(config))
    s.guest('server', 'systemctl', 'restart', 'nebula')
    old_pid = process(s)
    s.guest('server', 'mkdir', '-p', '/opt/nebula/test-bin')
    real_ip = s.guest('server', '/bin/sh', '-c', 'command -v ip').stdout.strip()
    wrapper = ('#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\na=sys.argv[1:]\n'
               'if a and a[0]=="-json" and a[-1]=="neb0":\n'
               ' Path("/run/nebula-ip-injected").touch()\n sys.exit(77)\n'
               'os.execv(' + repr(real_ip) + ',["ip"]+a)\n')
    s.guest('server', 'python3', '-c', 'from pathlib import Path; import sys; p=Path("/opt/nebula/test-bin/ip"); '
            'p.write_text(sys.argv[1]); p.chmod(0o700)', wrapper)
    issue('10.193.0.6')
    inputs = {'certificate_name': 'server', 'test_leaf_dir': fixture,
              'test_command_path': '/opt/nebula/test-bin:/usr/sbin:/usr/bin:/sbin:/bin'}
    status, _ = play(s, s.inventory(), 'server', playbook='safe-apply.yml', extra=['-vvv', '-e', json.dumps(inputs)])
    s.check('disabled tunnel certificate-network changes restart successfully', status == 0 and process(s) != old_pid)
    s.check('disabled tunnel preflight and activation do not query interface addresses',
            s.guest('server', 'test', '!', '-e', '/run/nebula-ip-injected', check=False).returncode == 0)
    before = state(s)
    s.guest('lh', 'cp', '/opt/nebula/client.key', fixture + '/client.key')
    status, _ = play(s, s.inventory(), 'server', playbook='safe-apply.yml', check=False,
                     extra=['-vvv', '-e', json.dumps(dict(inputs, wrong_key='client.key'))])
    s.check('disabled tunnel invalid PKI restores the previous identity and process', status != 0 and state(s) == before)
    s.check('disabled tunnel recovery skips interface queries and cleans its backup', not backup_dirs(s) and
            s.guest('server', 'test', '!', '-e', '/run/nebula-ip-injected', check=False).returncode == 0)
