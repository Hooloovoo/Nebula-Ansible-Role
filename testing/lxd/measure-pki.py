#!/usr/bin/env python3
"""Reproduce PR A's task-size/state measurements, including uncommitted helpers."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml


def measure(role, revision=None):
    if revision:
        paths = subprocess.check_output(['git', '-C', str(role), 'ls-tree', '-r', '--name-only', revision, '--', 'tasks'], text=True).splitlines()
        files = {name: subprocess.check_output(['git', '-C', str(role), 'show', revision + ':' + name], text=True)
                 for name in paths if Path(name).name == 'install_pki.yml' or Path(name).name.startswith('pki_')}
    else:
        files = {str(p.relative_to(role)): p.read_text() for p in (role / 'tasks').glob('*.yml')
                 if p.name == 'install_pki.yml' or p.name.startswith('pki_')}
    facts, registers, aliases = set(), set(), set()

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            facts.update(value.get('set_fact', {}))
            aliases.update(value.get('vars', {}))
            if 'register' in value:
                registers.add(value['register'])
            for item in value.values():
                walk(item)

    for text in files.values():
        walk(yaml.safe_load(text))
    lines = [line for text in files.values() for line in text.splitlines()]
    return {
        'revision': revision or 'working tree',
        'files': {name: len(text.splitlines()) for name, text in sorted(files.items())},
        'lines': len(lines), 'longest_line': max(map(len, lines)),
        'distinct_pki_names': len(set(re.findall(r'\bpki_\w+', '\n'.join(lines)))),
        'declared_names': len(facts | registers | aliases),
        'fact_or_result_names': len(facts | registers),
        'set_fact_names': sorted(n for n in facts if n.startswith('pki_')),
        'registered_results': sorted(n for n in registers if n.startswith('pki_')),
        'scoped_aliases': sorted(n for n in aliases if n.startswith('pki_')),
        'task_source_sha256': hashlib.sha256(''.join(name + '\0' + text for name, text in sorted(files.items())).encode()).hexdigest(),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', type=Path)
    parser.add_argument('--revision')
    args = parser.parse_args()
    print(json.dumps(measure(args.role.resolve(), args.revision), indent=2))
