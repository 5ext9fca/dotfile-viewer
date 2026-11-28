from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from dotfile_viewer import api, cli
from dotfile_viewer.files import observe
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
                    self.assertTrue(session.scratch.is_dir())
                session.close()
                self.assertFalse(session.scratch.exists())
                self.assertEqual(session.snapshot.document(), snapshot.document())
                with (patch.object(session, '_apply_lease') as acquire,
                      patch('dotfile_viewer.application.observe') as observed,
                      patch('dotfile_viewer.application.atomic_json') as report):
                    for operation in (session.__enter__, session.preview, lambda: session.apply(dry=True), session.apply):
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
