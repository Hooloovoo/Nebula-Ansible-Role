"""Interrupted cleanup must remain idempotent without deleting foreign VMs."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_pki_selection import runner


class SavedCleanupTests(unittest.TestCase):
    def test_already_deleted_instances_do_not_block_owned_instance_cleanup(self):
        run_id = 'nebtest-0123456789'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            manifest = path / 'manifest.json'
            manifest.write_text(json.dumps({'remote': 'test', 'project': 'test', 'run_id': run_id,
                                           'instances': [run_id + '-lh', run_id + '-client']}))
            owned = subprocess.CompletedProcess([], 0, stdout=run_id + '\n', stderr='')
            with patch.object(runner.subprocess, 'check_output', return_value=json.dumps([{'name': run_id + '-lh'}])), \
                 patch.object(runner.subprocess, 'run', return_value=owned) as command:
                runner.cleanup_saved(path)
            self.assertEqual(len(command.call_args_list), 2)
            self.assertEqual(command.call_args_list[1].args[0][:3], ['lxc', 'delete', 'test:' + run_id + '-lh'])
            self.assertEqual(json.loads(manifest.read_text())['instances'], [])

    def test_existing_instance_with_wrong_marker_is_never_deleted(self):
        run_id = 'nebtest-0123456789'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'manifest.json').write_text(json.dumps({'remote': 'test', 'project': 'test', 'run_id': run_id,
                                                          'instances': [run_id + '-lh']}))
            foreign = subprocess.CompletedProcess([], 0, stdout='someone-else\n', stderr='')
            with patch.object(runner.subprocess, 'check_output', return_value=json.dumps([{'name': run_id + '-lh'}])), \
                 patch.object(runner.subprocess, 'run', return_value=foreign) as command:
                with self.assertRaisesRegex(RuntimeError, 'ownership marker'):
                    runner.cleanup_saved(path)
            self.assertEqual(len(command.call_args_list), 1)
            self.assertEqual(command.call_args.args[0][1:3], ['config', 'get'])


if __name__ == '__main__':
    unittest.main()
