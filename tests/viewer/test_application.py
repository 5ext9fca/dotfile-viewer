from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from dotfile_viewer import api, cli
from dotfile_viewer.files import atomic_json, observe
from viewer_fixture import Fixture


class ApplicationTest(Fixture):
    @unittest.skipIf(os.name == 'nt', 'POSIX permission bits')
    def test_apply_permissions_and_editor_preservation(self):
        target = self.config / 'demo/settings'
        target.chmod(0o640)
        self.save('edited')
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        target.chmod(0o700)
        self.capture()
        target.write_text('changed')
        target.chmod(0o444)
        api.apply_snapshot(self.archive, platform=self.platform, roots=self.roots, backup=self.root / 'backups')
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_plan_version_compatibility(self):
        from dotfile_viewer.application import _plan_version
        rows = [dict(target='中文"反' + chr(92), index=i) for i in range(10000)]
        for size in (0, 1, 127, 128, 129, 10000):
            batch = rows[:size]
            expected = hashlib.sha256(json.dumps(batch, sort_keys=True).encode()).hexdigest()
            self.assertEqual(_plan_version(batch), expected)
        self.capture()
        options = dict(platform=self.platform, roots=self.roots, backup=self.editor.backup)
        path = self.config / 'demo/settings'
        path.write_text('modified')
        rows = api.apply_snapshot(self.archive, dry=True, **options)['items']
        version = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        self.assertEqual(api.apply_snapshot(self.archive, expected_version=version, **options)['status'], 'applied')
        with self.assertRaises(api.ConflictError):
            api.apply_snapshot(self.archive, expected_version=version, **options)

    def test_one_shot_apply_observes_each_target_once(self):
        for i in range(5):
            (self.config / f'demo/file-{i}').touch()
        self.capture()
        options = dict(platform=self.platform, roots=self.roots, backup=self.root / 'backups')
        with api.SnapshotSession(self.archive, **options) as session:
            expected = session.preview()
        with self.assertRaisesRegex(api.SnapshotError, 'mismatch'):
            api.apply_snapshot(self.archive, dry=True, roots=self.roots, platform=api.Platform('windows', 'x86_64', 'native'))
        for mode in ('api-preview', 'cli-preview', 'apply'):
            observed = []
            def track(target):
                self.assertEqual((self.root / 'lease/owner').exists(), mode == 'apply')
                observed.append(target)
                return observe(target)

            with (patch('dotfile_viewer.application.observe', side_effect=track),
                  patch('dotfile_viewer.application.verify_archive', wraps=api.verify_archive) as verify):
                if mode == 'cli-preview':
                    with (patch('dotfile_viewer.application.host', return_value=self.platform),
                          patch('dotfile_viewer.application.host_roots', return_value=self.roots),
                          patch.dict(os.environ, DOTFILES_BACKUP_DIR=str(options['backup'])),
                          redirect_stdout(io.StringIO()) as output):
                        self.assertEqual(cli.main(['snapshot', 'apply', '--snapshot', str(self.archive), '--dry-run']), 0)
                        result = json.loads(output.getvalue())
                else:
                    result = api.apply_snapshot(self.archive, dry=mode == 'api-preview', **options)
            if mode != 'apply':
                self.assertIsNone(verify.call_args.args[1])
                self.assertEqual(result, dict(status='preview', **expected))
            self.assertEqual(len(set(observed)), 8)
            self.assertEqual(observed, [Path(row['target']) for row in result['items']])
            self.assertEqual(len(observed), 8)
        self.assertFalse(options['backup'].exists())

    def test_session_preflight_and_apply_confirmation(self):
        self.capture()
        options = dict(platform=self.platform, roots=self.roots, backup=self.root / 'backups')
        target = self.config / 'demo/settings'
        target.write_text('local')
        os.link(target, self.root / 'alias')
        with self.assertRaises(api.ConfirmationRequired):
            api.apply_snapshot(self.archive, **options)
        self.assertFalse(options['backup'].exists())
        self.assertEqual(target.read_text(), 'local')
        target.unlink()
        target.mkdir()
        with self.assertRaisesRegex(api.CoreError, 'type conflict'):
            with api.SnapshotSession(self.archive, **options):
                self.fail('Session exposed before preflight')

    def test_closed_session_rejects_operations_before_side_effects(self):
        snapshot = self.capture()
        options = dict(platform=self.platform, roots=self.roots, backup=self.editor.backup)
        target = self.config / 'demo/settings'
        directory = self.config / 'demo/empty'
        for change in ('unchanged', 'replace', 'mkdir'):
            target.write_bytes(b'local' if change == 'replace' else b'hello\r\n')
            if change == 'mkdir':
                directory.rmdir()
            with api.deployment_lease() as lease:
                with api.SnapshotSession(self.archive, lease=lease, **options) as session:
                    self.assertEqual(session.destination_roots, (self.config / 'demo',))
                session.close()
                self.assertFalse(hasattr(session, 'scratch'))
                self.assertEqual(session.snapshot.document(), snapshot.document())
                with (patch.object(session, '_apply_lease') as acquire,
                      patch('dotfile_viewer.application.observe') as observed,
                      patch('dotfile_viewer.application.atomic_json') as report):
                    for operation in (session.__enter__, session.preview, session.preflight, lambda: session.apply(dry=True), session.apply):
                        with self.subTest(change=change, operation=operation), self.assertRaisesRegex(api.CoreError, 'closed'):
                            operation()
                    acquire.assert_not_called()
                    observed.assert_not_called()
                    report.assert_not_called()
                self.assertTrue(Path(lease['path']).is_dir())
                self.assertEqual(directory.exists(), change != 'mkdir')
                self.assertFalse(self.editor.backup.exists())

    def test_construction_failure_removes_staging(self):
        self.capture()
        target = self.config / 'demo/settings'
        target.unlink()
        target.mkdir()
        with patch('tempfile.tempdir', str(self.root)), self.assertRaisesRegex(api.CoreError, 'type conflict'):
            api.SnapshotSession(self.archive, platform=self.platform, roots=self.roots, backup=self.editor.backup)
        self.assertEqual(list(self.root.glob('viewer-snapshot-*')), [])

    def test_preflight_observes_once_and_apply_rechecks(self):
        self.capture()
        target = self.config / 'demo/settings'
        alias = self.root / 'alias'
        os.link(target, alias)
        with api.SnapshotSession(self.archive, platform=self.platform, roots=self.roots, backup=self.editor.backup) as session:
            expected = session.preview()
            with patch('dotfile_viewer.application.observe', wraps=observe) as observed:
                plan = session.preflight()
            self.assertEqual(plan, expected)
            targets = [Path(row['target']) for row in plan['items']]
            self.assertEqual([call.args[0] for call in observed.call_args_list], targets)
            self.assertEqual(len(targets), len(set(targets)))
            target.write_bytes(b'local')
            with self.assertRaises(api.ConflictError):
                session.apply(expected_version=plan['version'])
            with self.assertRaises(api.ConfirmationRequired):
                session.apply()
            with self.assertRaises(api.ConfirmationRequired):
                session.preflight()
            confirmed = session.preflight(detach=True)
            self.assertEqual(session.apply(dry=True), dict(status='preview', **confirmed))
            self.assertEqual(session.apply(detach=True, expected_version=confirmed['version'])['status'], 'applied')
            self.assertEqual(alias.read_bytes(), b'local')
            self.assertEqual(target.read_bytes(), b'hello\r\n')

    def test_session_owned_inputs(self):
        self.capture()
        options = dict(platform=self.platform, roots=self.roots, backup=self.editor.backup)
        target = self.config / 'demo/settings'
        target.write_text('changed')
        with api.deployment_lease() as lease, api.SnapshotSession(self.archive, lease=lease, **options) as session:
            plan = session.preview()
            copy = session.snapshot
            copy.software['demo']['config'][0].update(path='other', exclude=[])
            copy.entries[0]['path'] = 'other'
            with api.snapshot_catalog(session.snapshot) as root, api.SnapshotSession(self.archive, **options) as other:
                self.assertEqual(api.selection(root, profile='snapshot'), ['demo'])
                self.assertEqual(session.preflight(), other.preview())
                self.assertEqual(session.preview(), plan)
                self.assertTrue(target.is_relative_to(session.destination_roots[0]))
                session.apply(expected_version=plan['version'])
        self.assertEqual(target.read_bytes(), b'hello\r\n')
        self.assertEqual(session.destination_roots, (self.config / 'demo',))

    def test_batch_preserves_primary_error(self):
        self.capture()
        target = self.config / 'demo/settings'
        target.write_bytes(b'local')
        conflict = api.ConflictError('item conflict')
        def fail_report(path, result):
            if result['status'] == 'failed':
                raise OSError('report unavailable')
            atomic_json(path, result)

        with (api.SnapshotSession(self.archive, roots=self.roots, platform=self.platform, backup=self.editor.backup) as session,
              patch.object(session, '_apply_item', side_effect=conflict),
              patch('dotfile_viewer.application.atomic_json', side_effect=fail_report),
              self.assertRaises(api.ConflictError) as raised):
            session.apply()
        self.assertIs(raised.exception, conflict)
        report, = self.editor.backup.glob('*/result.json')
        self.assertEqual(json.loads(report.read_text())['status'], 'running')
        self.assertEqual(target.read_bytes(), b'local')
        with patch.object(api, 'apply_snapshot', side_effect=conflict), redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(cli.main(['snapshot', 'apply', '--snapshot', str(self.archive)]), 4)
        for detail in ('item conflict', str(report), 'report unavailable'):
            self.assertIn(detail, errors.getvalue())

    def test_batch_keeps_completed_file_on_later_failure(self):
        first = self.config / 'demo/a'
        first.write_bytes(b'archived')
        self.capture()
        for failure in ('next-file', 'final-report'):
            first.write_bytes(b'local')
            backup = self.root / failure
            def write_report(path, result):
                if failure == 'final-report' and result['status'] == 'applied':
                    raise OSError('final report')
                atomic_json(path, result)
                if failure == 'next-file' and any(row['file'] == 'a' for row in result['items']):
                    (self.config / 'demo/settings').write_bytes(b'external')

            with (patch('dotfile_viewer.application.atomic_json', side_effect=write_report),
                  self.assertRaises(api.ConflictError if failure == 'next-file' else OSError)):
                api.apply_snapshot(self.archive, roots=self.roots, platform=self.platform, backup=backup)
            report, = backup.glob('*/result.json')
            result = json.loads(report.read_text())
            self.assertEqual(result['status'], 'failed')
            saved = next(row for row in result['items'] if row['file'] == 'a')
            self.assertEqual(Path(saved['backup']).read_bytes(), b'local')
            self.assertEqual(first.read_bytes(), b'archived')

    def test_preview_conflict(self):
        self.capture()
        with api.SnapshotSession(self.archive, platform=self.platform, roots=self.roots, backup=self.root / 'backups') as session:
            preview = session.preview()
            (self.config / 'demo/settings').write_text('changed')
            with self.assertRaises(api.ConflictError):
                session.apply(expected_version=preview['version'])

    def test_archive_pinned(self):
        self.capture()
        with api.SnapshotSession(self.archive, platform=self.platform, roots=self.roots, backup=self.root / 'backups') as session:
            self.archive.write_bytes(b'changed archive')
            (self.config / 'demo/settings').write_text('changed config')
            session.apply()
        self.assertEqual((self.config / 'demo/settings').read_bytes(), b'hello\r\n')

    def test_bound_session_lease(self):
        self.capture()
        with api.deployment_lease() as lease:
            options = dict(platform=self.platform, roots=self.roots, backup=self.editor.backup)
            session = api.SnapshotSession(self.archive, lease=lease.copy(), **options)
            self.addCleanup(session.close)
            (self.config / 'demo/settings').write_text('changed')
            with self.assertRaises(api.CoreError):
                session.apply(lease=dict(lease, token='wrong'))
            self.assertEqual(session.apply()['status'], 'applied')
            self.assertEqual(session.apply(lease=lease)['status'], 'unchanged')
            with api.SnapshotSession(self.archive, lease=lease, **options):
                pass
            self.assertTrue(Path(lease['path']).is_dir())
        with patch.object(session, 'preview', side_effect=AssertionError), self.assertRaises(api.CoreError):
            session.apply()
        self.assertFalse(Path(lease['path']).exists())
        self.assertEqual(session.apply(dry=True)['status'], 'preview')
        with api.deployment_lease(), self.assertRaises(api.CoreError):
            session.apply()

    def test_stale_credentials_never_create_a_lease(self):
        import subprocess
        import sys
        self.capture()
        lock = self.root / 'lease'
        options = dict(platform=self.platform, roots=self.roots, backup=self.editor.backup)
        with api.deployment_lease() as credentials:
            with patch.dict(os.environ, DOTFILES_DEPLOYMENT_TOKEN=credentials['token']):
                command = [sys.executable, '-m', 'dotfile_viewer.lease', 'acquire']
                self.assertFalse(json.loads(subprocess.check_output(command))['owned'])
                fields = subprocess.check_output([*command, '--tsv'], text=True).strip().split('\t')
                self.assertEqual(fields, [str(lock), credentials['token'], '0'])
        with api.SnapshotSession(self.archive, **options) as session:
            for apply in (session.apply, lambda **kw: api.apply_snapshot(self.archive, **options, **kw)):
                with patch('dotfile_viewer.application.observe', side_effect=AssertionError):
                    with self.assertRaises(api.CoreError):
                        apply(lease=credentials)
                    for invalid in ({}, {'path': str(lock)}, {'token': 'x'}):
                        with self.assertRaises(api.CoreError):
                            apply(lease=invalid, dry=True)
                self.assertEqual(apply(lease=credentials, dry=True)['status'], 'preview')
        with patch.dict(os.environ, DOTFILES_DEPLOYMENT_TOKEN=credentials['token']):
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 73)
            self.assertFalse(lock.exists())
            lock.mkdir()
            with self.assertRaises(api.CoreError), api.deployment_lease():
                pass
            self.assertEqual(list(lock.iterdir()), [])
            owner = lock / 'owner'
            owner.write_text('1|host|other')
            with self.assertRaises(api.CoreError), api.deployment_lease(**credentials):
                pass
            self.assertEqual(owner.read_text(), '1|host|other')
        self.assertFalse(self.editor.backup.exists())

    def test_lease_nested_and_stale(self):
        with api.deployment_lease() as lease:
            with api.deployment_lease(**lease):
                pass
            with self.assertRaises(api.CoreError):
                with api.deployment_lease():
                    pass
        (self.root / 'lease').mkdir()
        with self.assertRaises(api.CoreError):
            with api.deployment_lease():
                pass
