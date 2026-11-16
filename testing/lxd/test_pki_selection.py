"""Fast checks for PKI run selection and lossless split coverage accounting."""
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('nebula_lxd_runner', 'run.py')
coverage = load('nebula_pki_coverage', 'compare-pki-coverage.py')


class SelectionTests(unittest.TestCase):
    def test_safe_pki_profile_keeps_both_existing_scenarios(self):
        self.assertEqual(runner.select_scenarios('safe-pki', list(runner.PKI_SCENARIOS)),
                         ['safe-apply', 'safe-apply-ip-change'])

    def test_auto_remains_ordinary_and_all_has_each_acceptance_once(self):
        supported = ['certificates', 'firewall', 'ca-host', *runner.PKI_SCENARIOS]
        self.assertEqual(runner.select_scenarios('auto', supported), supported[:3])
        all_scenarios = runner.select_scenarios('all', supported)
        for scenario in runner.PKI_SCENARIOS:
            self.assertEqual(all_scenarios.count(scenario), 1)

    def test_list_mode_is_explicit_and_never_creates_a_suite(self):
        # Use the checked-in fixture files rather than requiring a feature worktree.
        with patch.object(runner.yaml, 'safe_load', return_value={'nebula_cert_groups': [], 'nebula_ca_host': 'lh'}), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(runner, 'Suite') as suite:
            for mode in ('install', 'in-place', 'combined'):
                output = io.StringIO()
                argv = ['run.py', '--scenario', 'safe-pki', '--pki-mode', mode, '--list-tests',
                        '--role-path', str(runner.ROOT), '--nebula-version', '1.8.0']
                with patch.object(sys, 'argv', argv), redirect_stdout(output):
                    runner.main()
                runs = json.loads(output.getvalue())
                self.assertEqual([r['scenario'] for r in runs], list(runner.PKI_SCENARIOS))
                self.assertTrue(all(r['pki_mode'] == mode for r in runs))
            suite.assert_not_called()

    def test_pki_mode_cannot_silently_select_ordinary_tests(self):
        with patch.object(sys, 'argv', ['run.py', '--scenario', 'auto', '--pki-mode', 'install', '--list-tests']), \
             redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            runner.main()
        self.assertEqual(caught.exception.code, 2)


class CoverageTests(unittest.TestCase):
    def test_partial_run_with_only_passing_assertions_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'manifest.json').write_text(json.dumps({'pki_mode': 'install', 'status': 'running',
                                                            'version': '1.8.0', 'scenario': 'safe-apply'}))
            (path / 'assertions.json').write_text(json.dumps([{'name': 'early check', 'passed': True}]))
            with self.assertRaisesRegex(ValueError, 'did not finish'):
                coverage.load_runs([path], 'install')

    def test_repeated_helper_assertions_are_not_deduplicated(self):
        key = ('1.8.0', 'safe-apply')
        self.assertEqual(coverage.compare({key: Counter(stream=3)}, {key: Counter(stream=2)},
                                          {key: Counter(stream=1)}), 3)

    def test_lost_or_uncombined_cases_are_rejected(self):
        key = ('1.8.0', 'safe-apply')
        for combined, install, in_place in ((Counter(stream=3), Counter(stream=1), Counter(stream=1)),
                                           (Counter(stream=1), Counter(stream=1), Counter(stream=1))):
            with self.assertRaises(ValueError):
                coverage.compare({key: combined}, {key: install}, {key: in_place})

    def test_different_release_or_scenario_sets_are_rejected(self):
        with self.assertRaises(ValueError):
            coverage.compare({('1.8.0', 'safe-apply'): Counter(a=1)}, {}, {})


if __name__ == '__main__':
    unittest.main()
