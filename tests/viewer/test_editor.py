import json
import os
from pathlib import Path
from unittest.mock import patch
from dotfile_viewer import api, files
from dotfile_viewer.files import observe
from viewer_fixture import Fixture


class EditorTest(Fixture):
    def test_deep_browsing_uses_iteration(self):
        import sys
        base = self.config / 'demo/empty'
        current = base
        for _ in range(80):
            current = current / 'd'
            current.mkdir()
        limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(64)
            rows = self.editor.files('demo', 'settings')
        finally:
            sys.setrecursionlimit(limit)
        self.assertIn(dict(file=current.relative_to(base.parent).as_posix(),
                           path=str(current), kind='directory'), rows)

    def test_editor_only_probes_selected_configuration(self):
        other = self.catalog / 'software/items/other.toml'
        other.write_text(other.with_name('demo.toml').read_text().replace('path="demo"', 'path="other"'))
        listed = self.editor.configurations()
        self.assertEqual([row['software'] for row in listed], ['demo', 'other'])
        self.assertEqual(listed[1]['configs'][0]['status'], 'missing')
        lstat = Path.lstat

        def check_path(path):
            self.assertFalse(path.is_relative_to(self.config / 'other'), path)
            return lstat(path)

        with patch.object(Path, 'lstat', check_path):
            target, description = self.editor.locate('demo', 'settings', 'settings')
            self.assertEqual(description, listed[0]['configs'][0])
            self.assertEqual(target, self.config / 'demo/settings')
            self.assertIn('settings', [row['file'] for row in self.editor.files('demo', 'settings')])
            version = self.read()['version']
            with patch('dotfile_viewer.editor.load_catalog', wraps=api.load_catalog) as load:
                self.assertEqual(self.save('updated', version)['status'], 'saved')
                load.assert_called_once_with(self.catalog)

        self.editor.backup = self.config / 'other/backups'
        with self.assertRaisesRegex(api.CoreError, 'overlaps managed configuration'):
            self.save('another change')
        self.assertFalse(self.editor.backup.exists())
        self.assertEqual(self.read()['content'], 'updated')

    def test_editor_reloads_declarations_between_reads(self):
        declaration = self.catalog / 'software/items/demo.toml'
        self.assertTrue(self.read()['editable'])
        declaration.write_text(declaration.read_text().replace('exclude=["ignored"]', 'exclude=["settings"]'))
        with self.assertRaisesRegex(api.CoreError, 'excluded'):
            self.read()

    def test_edit_preserves_newlines_and_backup(self):
        result = self.save('updated\n')
        self.assertEqual((self.config / 'demo/settings').read_bytes(), b'updated\r\n')
        self.assertEqual(Path(result['backup']).read_bytes(), b'hello\r\n')

    def test_conflict(self):
        version = self.read()['version']
        (self.config / 'demo/settings').write_text('external')
        with self.assertRaises(api.ConflictError):
            self.save('mine', version)
        self.assertEqual((self.config / 'demo/settings').read_text(), 'external')

    def test_same_content_no_backup(self):
        self.assertEqual(self.save('hello\n')['status'], 'unchanged')
        self.assertFalse((self.root / 'backups').exists())

    def test_hardlink_detach(self):
        other = self.root / 'other'
        os.link(self.config / 'demo/settings', other)
        with self.assertRaises(api.ConfirmationRequired):
            self.save('changed')
        self.save('changed', detach=True)
        self.assertEqual(other.read_bytes(), b'hello\r\n')

    def test_exclusion_and_traversal(self):
        for name in ('ignored', '../outside', '/absolute'):
            with self.assertRaises(api.CoreError):
                self.editor.read('demo', 'settings', name)

    def test_excluded_subtree_across_editor_and_snapshot(self):
        declaration = self.catalog / 'software/items/demo.toml'
        declaration.write_text(declaration.read_text().replace('["ignored"]', '["ignored", "cache"]'))
        for dirname in ('cache', 'cache-old'):
            path = self.config / 'demo' / dirname
            path.mkdir()
            (path / 'item').write_text('content')

        expected = {'', 'empty', 'settings', 'cache-old', 'cache-old/item'}
        self.assertEqual({row['file'] for row in self.editor.files('demo', 'settings')}, expected)
        self.assertEqual(self.editor.read('demo', 'settings', 'cache-old/item')['content'], 'content')
        snapshot = self.capture()
        self.assertEqual({entry['path'] for entry in snapshot.entries}, expected)
        for path in ('cache', 'cache/item'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(api.CoreError, 'excluded'):
                    self.editor.read('demo', 'settings', path)
                entry = next(e for e in snapshot.entries if e['path'] == path.replace('cache', 'cache-old'))
                manifest = snapshot.document()
                manifest['entries'].append(dict(entry, path=path))
                with self.assertRaisesRegex(api.SnapshotError, 'Excluded content'):
                    api.Snapshot.parse(manifest)

    def test_configuration_overview_leaf_probes(self):
        base = self.config / 'demo'
        leaves = {base / f'f{i}' for i in range(128)}
        for path in leaves:
            path.touch()
        (base / 'link').symlink_to(base / 'missing')
        paths = [*sorted(leaves), base, base / 'missing', base / 'link', base / 'denied']
        statuses = ['file'] * 128 + ['directory', 'missing', 'unavailable', 'unavailable']
        if hasattr(os, 'mkfifo'):
            os.mkfifo(base / 'fifo')
            paths.append(base / 'fifo')
            statuses.append('unsupported')
        configs = [dict(id=f'c{i}', root='config_home', path=p.relative_to(self.config).as_posix(),
                        platforms=['macos']) for i, p in enumerate(paths)]
        probes = []
        original = Path.lstat
        def checked(path):
            if path in leaves:
                probes.append(path)
            if path == base / 'denied':
                raise PermissionError('denied')
            return original(path)
        with patch('dotfile_viewer.editor.load_catalog', return_value={'demo': {'config': configs}}), patch.object(Path, 'lstat', checked):
            rows = self.editor.configurations()[0]['configs']
        self.assertEqual([r['status'] for r in rows], statuses)
        self.assertEqual(['error' in r for r in rows], [s == 'unavailable' for s in statuses])
        self.assertCountEqual(probes, leaves)

    def test_files_classifies_checked_leaf(self):
        base = self.config / 'demo'
        (base / 'broken').symlink_to(base / 'absent')
        (base / 'denied').touch()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(base / 'pipe')
        original = Path.lstat
        def checked(path):
            if path == base / 'denied':
                raise PermissionError('denied')
            return original(path)
        with patch.object(Path, 'lstat', checked):
            rows = self.editor.files('demo', 'settings')
        expected = {'': 'directory', 'broken': 'unavailable', 'denied': 'unavailable',
                    'empty': 'directory', 'settings': 'file'}
        if hasattr(os, 'mkfifo'):
            expected['pipe'] = 'unsupported'
        self.assertEqual([(r['file'], r['kind']) for r in rows], sorted(expected.items()))
        base.rename(self.config / 'away')
        self.assertEqual(self.editor.files('demo', 'settings'), [dict(file='', path=str(base), kind='missing')])

    def test_links_rejected(self):
        (self.config / 'demo/link').symlink_to(self.root)
        with self.assertRaises(api.CoreError):
            self.capture()
        self.assertIn('unavailable', [x['kind'] for x in self.editor.files('demo', 'settings')])

    def test_editor_reads_target_once(self):
        target = self.config / 'demo/settings'
        limit = 2 * 1024 * 1024
        for data in (b'', b'x' * 64, b'x' * 8192, b'\xef\xbb\xbfhello\r\n', b'x' * limit, b'x' * (limit + 1)):
            with self.subTest(size=len(data)):
                target.write_bytes(data)
                expected = observe(target)
                with patch.object(Path, 'open', autospec=True, side_effect=Path.open) as opened:
                    result = self.read()
                self.assertEqual(sum(call.args[0] == target for call in opened.call_args_list), 1)
                self.assertEqual(result['version'], expected['version'])
                self.assertEqual(result['editable'], len(data) <= limit)
                if result['editable']:
                    self.assertEqual(result['content'], data.decode('utf-8-sig').replace('\r\n', '\n'))
                    self.assertEqual(result['bom'], data.startswith(b'\xef\xbb\xbf'))
                    self.assertEqual(result['newline'], 'crlf' if b'\r\n' in data else 'lf')

    def test_observe_continues_after_short_reads(self):
        import hashlib
        path = self.config / 'demo/settings'
        data = bytes(range(64))
        path.write_bytes(data)
        with path.open('rb') as source:
            read = source.read
            with patch.object(source, 'read', side_effect=lambda size: read(min(size, 3))) as reads:
                with patch.object(Path, 'open', return_value=source):
                    result = observe(path)
                self.assertEqual(reads.call_args.args, (8192,))
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())

    def test_editor_rechecks_file_after_read(self):
        target = self.config / 'demo/settings'
        original_stat = Path.stat
        for replace in (False, True):
            for save in (False, True):
                with self.subTest(replace=replace, save=save):
                    target.write_bytes(b'original')
                    version = self.read()['version']
                    def change_before_final_check(path, *, follow_symlinks=True):
                        if path == target and follow_symlinks:
                            if replace:
                                other = self.root / 'replacement'
                                other.write_bytes(b'external')
                                os.replace(other, target)
                            else:
                                target.write_bytes(b'external edit')
                        return original_stat(path, follow_symlinks=follow_symlinks)

                    with patch.object(Path, 'stat', change_before_final_check), self.assertRaises(api.ConflictError):
                        if save:
                            self.save('mine', version)
                        else:
                            self.read()
                    self.assertEqual(target.read_bytes(), b'external' if replace else b'external edit')
                    self.assertFalse(self.editor.backup.exists())

    def test_binary_and_large_text(self):
        p = self.config / 'demo/settings'
        p.write_bytes(b'\x00binary')
        self.assertFalse(self.read()['editable'])
        p.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        self.assertFalse(self.read()['editable'])

    def test_edit_writes_candidate_directly_to_staging(self):
        target = self.config / 'demo/settings'
        for content in ('x' * (2 * 1024 * 1024), ''):
            old = target.read_bytes()
            with patch.object(Path, 'write_bytes', autospec=True, side_effect=Path.write_bytes) as write:
                saved = self.save(content)
            write.assert_called_once()
            path, data = write.call_args.args
            self.assertEqual(path.parent, target.parent)
            self.assertTrue(path.name.startswith('.dotfile-viewer-'))
            self.assertEqual(data, content.encode())
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(Path(saved['backup']).read_bytes(), old)
        with patch.object(Path, 'write_bytes', side_effect=OSError('stage failed')), self.assertRaises(OSError):
            self.save('new')
        self.assertEqual(target.read_bytes(), b'')
        self.assertEqual(list(target.parent.glob('.dotfile-viewer-*')), [])

    def test_backup_failure_leaves_original(self):
        before = (self.config / 'demo/settings').read_bytes()
        with patch('dotfile_viewer.files.shutil.copy2', side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):
                self.save('changed')
        self.assertEqual((self.config / 'demo/settings').read_bytes(), before)

    def test_prepared_report_failure_preserves_target(self):
        target = self.config / 'demo/settings'
        original = target.read_bytes()
        write_report = files.atomic_json
        def fail_prepared(path, record):
            if record['status'] == 'prepared':
                raise OSError('prepared report failed')
            write_report(path, record)
        self.capture()
        target.write_bytes(b'local')
        with patch.object(files, 'atomic_json', side_effect=fail_prepared):
            for apply in (False, True):
                with self.assertRaisesRegex(OSError, 'prepared report failed'):
                    if apply:
                        api.apply_snapshot(self.archive, platform=self.platform, roots=self.roots, backup=self.editor.backup)
                    else:
                        self.save(original.decode())
                self.assertEqual(target.read_bytes(), b'local')
                self.assertFalse(list(target.parent.glob('.dotfile-viewer-*')))
        backups = list(self.editor.backup.glob('*/*.original'))
        self.assertEqual([p.read_bytes() for p in backups], [b'local', b'local'])

    def test_publish_failure_leaves_original(self):
        from dotfile_viewer import files
        replace = files.os.replace
        def fail(source, target):
            if Path(target) == self.config / 'demo/settings':
                raise OSError('replace failed')
            return replace(source, target)
        with patch('dotfile_viewer.files.os.replace', side_effect=fail):
            with self.assertRaises(OSError):
                self.save('changed')
        self.assertEqual((self.config / 'demo/settings').read_bytes(), b'hello\r\n')

    def test_first_post_replace_read_failure(self):
        source = self.root / 'candidate'
        source.write_bytes(b'candidate')
        for case in ('existing', 'new', 'changed', 'replaced', 'unreadable'):
            with self.subTest(case=case):
                session = self.root / case
                session.mkdir()
                target = session / 'target'
                if case != 'new':
                    target.write_bytes(b'original')
                expected = observe(target)['version']
                replace = os.replace
                just_replaced = False

                def track_replace(src, dst):
                    nonlocal just_replaced
                    replace(src, dst)
                    if dst == target:
                        just_replaced = True

                def fail_read(path):
                    nonlocal just_replaced
                    if path == target and just_replaced:
                        just_replaced = case == 'unreadable'
                        if case == 'changed':
                            target.write_bytes(b'external')
                        if case == 'replaced':
                            other = session / 'other'
                            other.write_bytes(source.read_bytes())
                            replace(other, target)
                        raise OSError('injected read failure')
                    return observe(path)

                with (patch.object(files.os, 'replace', side_effect=track_replace),
                      patch.object(files, 'observe', side_effect=fail_read),
                      self.assertRaisesRegex(api.CoreError, 'injected read failure')):
                    files.publish_file(target, source, expected, session)
                report = json.loads(next(session.glob('*.json')).read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['recovery'], {'existing': 'restored', 'new': 'removed'}.get(case, 'manual'))
                if case == 'new':
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual(Path(report['backup']).read_bytes(), b'original')
                    self.assertEqual(target.read_bytes(), {'existing': b'original', 'changed': b'external'}.get(case, b'candidate'))

    def test_saved_report_failure(self):
        write_report = files.atomic_json

        def fail_saved(path, record):
            if record['status'] == 'saved':
                raise OSError('injected report failure')
            write_report(path, record)

        with patch.object(files, 'atomic_json', side_effect=fail_saved), self.assertRaisesRegex(api.CoreError, 'report failure'):
            self.save('changed')
        self.assertEqual((self.config / 'demo/settings').read_bytes(), b'hello\r\n')
        report = json.loads(next((self.root / 'backups').glob('*/*.json')).read_text())
        self.assertEqual(report['recovery'], 'restored')
