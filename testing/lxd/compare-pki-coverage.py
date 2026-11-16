#!/usr/bin/env python3
"""Require split PKI runs to retain the combined run's assertion multiset."""
import argparse
from collections import Counter
import json
from pathlib import Path


def load_runs(paths, mode):
    runs = {}
    for path in paths:
        manifest = json.loads((path / 'manifest.json').read_text())
        if manifest['pki_mode'] != mode:
            raise ValueError(f'{path}: expected PKI mode {mode}')
        if manifest.get('status') != 'passed':
            raise ValueError(f'{path}: run did not finish successfully')
        if manifest['scenario'] not in ('safe-apply', 'safe-apply-ip-change'):
            raise ValueError(f'{path}: not a PKI acceptance scenario')
        key = manifest['version'], manifest['scenario']
        if key in runs:
            raise ValueError(f'Duplicate {mode} run for {key}')
        assertions = json.loads((path / 'assertions.json').read_text())
        if not assertions or any(not item['passed'] for item in assertions):
            raise ValueError(f'{path}: empty or failing assertions')
        # Baseline upgrades have topology-specific assertions, and may be run
        # against different baselines for A, B and A+B. Compare acceptance cases.
        runs[key] = Counter(item['name'] for item in assertions
                            if not item['name'].startswith('baseline upgrade '))
    return runs


def compare(combined, install, in_place):
    if combined.keys() != install.keys() or combined.keys() != in_place.keys():
        raise ValueError('Supply all three modes for the same scenario/version pairs')
    for key, expected in combined.items():
        actual = install[key] + in_place[key]
        missing, extra = expected - actual, actual - expected
        if missing or extra:
            raise ValueError(f'{key}: missing from splits={dict(missing)}; missing from combined={dict(extra)}')
    return sum(sum(items.values()) for items in combined.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--combined', required=True, nargs='+', type=Path, metavar='RUN_DIRECTORY')
    parser.add_argument('--install', required=True, nargs='+', type=Path, metavar='RUN_DIRECTORY')
    parser.add_argument('--in-place', required=True, nargs='+', type=Path, metavar='RUN_DIRECTORY')
    args = parser.parse_args()
    try:
        total = compare(load_runs(args.combined, 'combined'), load_runs(args.install, 'install'),
                        load_runs(args.in_place, 'in-place'))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'FAIL: {exc}\n')
    print(f'PASS: {total} acceptance assertions retained across the splits (baseline checks excluded)')


if __name__ == '__main__':
    main()
