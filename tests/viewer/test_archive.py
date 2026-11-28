import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import warnings
import zipfile
from dotfile_viewer import api
from viewer_fixture import Fixture


class ArchiveTest(Fixture):
    def test_macos_zsh_snapshot(self):
        source = self.root / 'source'
        destination = self.root / 'destination'
        def roots(directory):
            return {'home': str(directory), 'config_home': str(directory / '.config')}

        tracked = ['.zshenv', '.config/atuin/config.toml', '.config/starship.toml',
                   '.config/zsh/regenerate.zsh', '.zshrc', '.zprofile', '.zsh_plugins.txt', '.config/zsh/functions/demo',
                   '.config/zsh-abbr/user-abbreviations']
        private = ['.atuin/data/history.db', '.config/atuin/key', '.zsh_history', '.zsh_sessions/session', '.zsh_plugins.zsh']
        for relative in tracked + private:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        api.export_snapshot(self.archive, software=['zsh', 'atuin', 'starship'], platform=self.platform, roots=roots(source))
        with api.SnapshotSession(self.archive, platform=self.platform, roots=roots(destination),
                                 backup=self.root / 'backup') as session:
            self.assertEqual(session.apply()['status'], 'applied')
            self.assertEqual(session.apply()['status'], 'unchanged')
        for relative in tracked:
            self.assertEqual((destination / relative).read_text(), relative)
        for relative in private:
            self.assertFalse((destination / relative).exists())

    def test_linux_shell_and_toolbox_capture(self):
        platform = api.Platform('linux.dnf', 'x86_64', 'native')
        source = self.root / 'home'
        config = source / '.config'
        toolbox = '.local/share/JetBrains/Toolbox/'
        tracked = ['.zshenv', '.zprofile', '.zshrc', '.zsh_plugins.txt',
                   '.config/zsh/regenerate.zsh', '.config/zsh-abbr/user-abbreviations',
                   '.config/starship.toml', '.config/atuin/config.toml', '.config/fish/config.fish',
                   toolbox + '.settings.json']
        skipped = ['.zsh_history', '.zsh_plugins.zsh', '.config/atuin/key',
                   '.config/fish/fish_variables', '.config/opencode/service.json',
                   toolbox + 'accounts.json', toolbox + 'apps/example']
        for name in tracked + skipped:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic')
        api.export_snapshot(self.archive, software=['zsh', 'starship', 'atuin', 'fish', 'opencode', 'jetbrains-toolbox'],
                            platform=platform, roots={'home': str(source), 'config_home': str(config)})
        dest = self.root / 'destination'
        with api.SnapshotSession(self.archive, platform=platform,
                                 roots={'home': str(dest), 'config_home': str(dest / '.config')},
                                 backup=self.root / 'backup') as session:
            session.apply()
        for name in tracked:
            self.assertEqual((dest / name).read_text(), 'synthetic')
        for name in skipped:
            self.assertFalse((dest / name).exists())
        self.assertNotIn('jetbrains-toolbox', api.select(api.load_catalog(), 'linux.dnf', 'wsl', ['all']))

    def test_exclusions_are_scoped_and_case_sensitive(self):
        from dotfile_viewer.snapshot import _inventory
        (self.config / 'demo/cache').mkdir()
        (self.config / 'demo/cache/leaf').touch()
        configs = {('demo', 'a'): {'exclude': ['cache']}, ('demo', 'b'): {'exclude': ['Cache']}}
        locations = dict.fromkeys(configs, self.config / 'demo')
        entries = dict(_inventory(configs, locations))
        self.assertNotIn(('demo', 'a', 'cache/leaf'), entries)
        self.assertIn(('demo', 'b', 'cache/leaf'), entries)

    def test_capture_reuses_index(self):
        from dotfile_viewer import manifest, snapshot
        for count in (1, 8):
            configs = []
            for i in range(count):
                name = f'item-{i}'
                (self.config / name).write_text(name)
                configs.append(dict(id=name, platforms=['macos'], root='config_home', path=name))
            catalog = {'demo': dict(schema_version=1, config=configs)}
            output = self.root / f'{count}.zip'
            with (patch.object(snapshot, 'configurations', wraps=snapshot.configurations) as index,
                  patch.object(manifest, 'configurations', wraps=manifest.configurations) as parsed_index):
                preview = snapshot.capture(catalog, self.platform, ['demo'], self.roots, output, dry=True)
                index.assert_called_once()
                parsed_index.assert_called_once()
            saved = snapshot.capture(catalog, self.platform, ['demo'], self.roots, output)
            self.assertEqual(saved.entries, preview.entries)
            self.assertEqual(api.verify_archive(output).entries, preview.entries)

    def test_snapshot_resolves_shared_root_once_per_mapping(self):
        from dotfile_viewer.snapshot import targets
        configs = [dict(id=str(i), root='home', path=f'tool-{i}') for i in range(128)]
        catalog = {'demo': {'config': configs}}
        with patch.object(Path, 'resolve', autospec=True, side_effect=Path.resolve) as resolve:
            locations = targets(catalog, self.roots)
            self.assertEqual(len(locations), 128)
            resolve.assert_called_once_with(self.root)
        for roots in ({}, {'home': 'relative'}):
            with self.assertRaisesRegex(api.SnapshotError, 'absolute configuration root'):
                targets(catalog, roots)
        (self.root / 'tool-127').symlink_to(self.config)
        with self.assertRaisesRegex(api.SnapshotError, 'Links/reparse'):
            targets(catalog, self.roots)

    def test_configuration_destination_overlaps(self):
        from dotfile_viewer.snapshot import targets

        def resolve(*paths):
            configs = [dict(id=str(i), root='home', path=path) for i, path in enumerate(paths)]
            return targets({'demo': {'config': configs}}, self.roots)

        for paths in [('tool', 'tool'), ('Straße', 'STRASSE'), ('tool', 'TOOL/nested'),
                      ('tool/nested', 'tool')]:
            with self.subTest(paths=paths), self.assertRaisesRegex(api.SnapshotError, 'Overlapping'):
                resolve(*paths)
        siblings = ['tools/a', 'tools/b', 'tools/b-old']
        self.assertEqual(list(resolve(*siblings).values()), [self.root / path for path in siblings])

    def test_overlapping_roots_in_export_and_apply_preview(self):
        declaration = self.catalog / 'software/items/demo.toml'
        with declaration.open('a') as stream:
            stream.write('[[config]]\nid="other"\nplatforms=["macos"]\nroot="home"\npath="demo"\n')
        separate = self.roots.copy()
        self.roots['home'] = self.roots['config_home']
        with self.assertRaisesRegex(api.SnapshotError, 'Overlapping'):
            self.capture()
        self.assertFalse(self.archive.exists())

        (self.root / 'demo').write_bytes(b'source')
        api.export_snapshot(self.archive, catalog=self.catalog, software=['demo'],
                            platform=self.platform, roots=separate)
        destination = self.root / 'destination'
        with self.assertRaisesRegex(api.SnapshotError, 'Overlapping'):
            api.apply_snapshot(self.archive, dry=True, platform=self.platform,
                               roots=dict(home=destination, config_home=destination), backup=self.editor.backup)
        self.assertFalse(destination.exists())
        self.assertFalse(self.editor.backup.exists())

    def test_capture_rechecks_tree_and_root(self):
        from dotfile_viewer import snapshot
        for dry in (False, True):
            for change in ('add', 'delete', 'replace', 'type', 'redirect'):
                with self.subTest(dry=dry, change=change):
                    base = self.root / f'{dry}-{change}'
                    directory = base / 'demo'
                    directory.mkdir(parents=True)
                    leaf = directory / 'leaf'
                    leaf.touch()
                    roots = dict(config_home=str(base))
                    targets = snapshot._targets
                    calls = 0
                    def resolve(configs, mapping):
                        nonlocal calls
                        calls += 1
                        if calls == 2:
                            if change == 'add':
                                (directory / 'new').touch()
                            elif change == 'redirect':
                                roots['config_home'] = str(self.config)
                            elif change == 'replace':
                                replacement = directory / 'replacement'
                                replacement.touch()
                                replacement.replace(leaf)
                            else:
                                leaf.unlink()
                                if change == 'type':
                                    leaf.mkdir()
                        return targets(configs, mapping)
                    with patch.object(snapshot, '_targets', side_effect=resolve), self.assertRaises(api.SnapshotError):
                        api.export_snapshot(self.archive, catalog=self.catalog, software=['demo'],
                                            platform=self.platform, roots=roots, dry=dry)
                    self.assertFalse(self.archive.exists())

    def test_capture_missing_required_path(self):
        missing = self.config / 'demo'
        missing.rename(self.config / 'away')
        with self.assertRaises(FileNotFoundError) as failure:
            self.capture()
        self.assertEqual(Path(failure.exception.filename), missing)
        self.assertFalse(self.archive.exists())

    def test_export_dry(self):
        api.export_snapshot(self.archive, catalog=self.catalog, profile='test', platform=self.platform, roots=self.roots, dry=True)
        self.assertFalse(self.archive.exists())

    def test_snapshot_roundtrip(self):
        (self.config / 'demo/binary').write_bytes(b'\x00\xff')
        snapshot = self.capture()
        self.assertEqual(snapshot.schema_version, 2)
        with self.assertRaises(api.CoreError):
            self.capture()
        (self.config / 'demo/settings').write_text('changed')
        (self.config / 'demo/extra').write_text('preserve')
        result = api.apply_snapshot(self.archive, platform=self.platform, roots=self.roots, backup=self.root / 'backups')
        self.assertEqual(result['status'], 'applied')
        self.assertEqual((self.config / 'demo/settings').read_bytes(), b'hello\r\n')
        self.assertEqual((self.config / 'demo/extra').read_text(), 'preserve')
        self.assertEqual(api.apply_snapshot(self.archive, platform=self.platform, roots=self.roots, backup=self.root / 'backups')['status'], 'unchanged')

    def test_payload_boundaries(self):
        base = self.config / 'demo'
        for size in (0, 2**20, 2**20 + 1):
            (base / str(size)).write_bytes(b'x' * size)
        dry = api.export_snapshot(self.archive, catalog=self.catalog, software=['demo'],
                                  platform=self.platform, roots=self.roots, dry=True)
        snapshot = self.capture()
        staged = self.root / 'unpacked'
        self.assertEqual(snapshot.software, dry.software)
        self.assertEqual(snapshot.entries, dry.entries)
        for destination in (None, staged):
            self.assertEqual(api.verify_archive(self.archive, destination), snapshot)
        for entry in snapshot.entries:
            if entry['path'].isdigit():
                data = (base / entry['path']).read_bytes()
                self.assertEqual((entry['size'], entry['sha256']), (len(data), hashlib.sha256(data).hexdigest()))
                self.assertEqual((staged / 'payload/demo/settings/root' / entry['path']).read_bytes(), data)

    def test_streamed_manifest_bytes(self):
        from dotfile_viewer.snapshot import _write_manifest
        for entries in ([], [{'path': '中文"' + chr(92) + str(i)} for i in range(8192)]):
            document = dict(created_at='2026-01-01T00:00:00+00:00', entries=entries)
            with zipfile.ZipFile(self.archive, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                _write_manifest(archive, document)
            expected = json.dumps(document, ensure_ascii=False, indent=2).encode()
            with zipfile.ZipFile(self.archive) as archive:
                self.assertEqual(archive.read('manifest.json'), expected)

    def test_manifest_write_failure_cleans_archive(self):
        def fail(document, stream, **kwargs):
            stream.write('{')
            raise OSError('manifest failed')
        with patch('dotfile_viewer.snapshot.json.dump', side_effect=fail), self.assertRaisesRegex(OSError, 'manifest failed'):
            self.capture()
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob('.dotfiles-snapshot-*')), [])

    def test_export_publication_failures(self):
        close = zipfile.ZipFile.close

        def fail_close(archive):
            writing = archive.mode == 'w' and archive.fp is not None
            close(archive)
            if writing:
                raise OSError('close failed')

        def competing_output(source, target):
            Path(target).write_bytes(b'other')
            raise FileExistsError(target)

        for stage, failure in [
            ('close', patch.object(zipfile.ZipFile, 'close', fail_close)),
            ('verify', patch('dotfile_viewer.snapshot.verify_archive', side_effect=api.SnapshotError('verify'))),
            ('publish', patch('dotfile_viewer.snapshot.os.link', side_effect=competing_output)),
        ]:
            with self.subTest(stage=stage), failure, self.assertRaises((api.SnapshotError, OSError)):
                self.capture()
            self.assertEqual(list(self.root.glob('.dotfiles-snapshot-*')), [])
            if stage == 'publish':
                self.assertEqual(self.archive.read_bytes(), b'other')
            else:
                self.assertFalse(self.archive.exists())

    def test_export_stream_write_failure(self):
        original = zipfile.ZipFile.open
        def broken_open(archive, name, mode='r', **kwargs):
            stream = original(archive, name, mode, **kwargs)
            if mode == 'w':
                def fail(data):
                    raise OSError('payload write failed')
                stream.write = fail
            return stream
        with patch.object(zipfile.ZipFile, 'open', broken_open), self.assertRaisesRegex(OSError, 'payload write failed'):
            self.capture()
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob('.dotfiles-snapshot-*')), [])

    def test_manifest_model_imports_and_validation(self):
        from dotfile_viewer import manifest, snapshot
        for module in (snapshot, api):
            self.assertIs(module.Snapshot, manifest.Snapshot)
            self.assertIs(module.Platform, manifest.Platform)
        document = self.capture().document()
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(json.loads(archive.read('manifest.json')), document)
        parsed = manifest.Snapshot.parse(document)
        self.assertEqual(parsed, api.verify_archive(self.archive))
        self.assertEqual(parsed.document(), document)
        document['software']['demo']['config'][0]['platforms'] = ['linux.apt']
        with self.assertRaisesRegex(api.SnapshotError, 'another platform'):
            api.Snapshot.parse(document)

    def test_old_manifest_rejected(self):
        self.capture()
        with zipfile.ZipFile(self.archive) as original:
            data = {n: original.read(n) for n in original.namelist()}
        manifest = json.loads(data['manifest.json']); manifest['schema_version'] = 1
        data['manifest.json'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(self.archive, 'w') as out:
            for name, content in data.items():
                out.writestr(name, content)
        with self.assertRaises(api.CoreError):
            api.verify_archive(self.archive)

    def test_reordered_snapshot(self):
        (self.config / 'demo/empty/child').write_bytes(b'nested')
        self.capture()
        reordered = self.root / 'reordered.zip'
        with zipfile.ZipFile(self.archive) as original, zipfile.ZipFile(reordered, 'w') as out:
            manifest = json.loads(original.read('manifest.json'))
            manifest['entries'].reverse()
            out.writestr('manifest.json', json.dumps(manifest))
            for name in original.namelist():
                if name != 'manifest.json':
                    out.writestr(name, original.read(name))
        dest = self.root / 'destination'
        options = dict(platform=self.platform, roots={'config_home': str(dest)}, backup=self.root / 'backups')
        with api.SnapshotSession(reordered, **options) as session:
            preview = session.preview()
            paths = [row['file'] for row in preview['items']]
            self.assertLess(paths.index(''), paths.index('empty'))
            self.assertLess(paths.index('empty'), paths.index('empty/child'))
            self.assertEqual(session.apply(expected_version=preview['version'])['status'], 'applied')
            self.assertEqual((dest / 'demo/empty/child').read_bytes(), b'nested')
            extra = dest / 'demo/extra'
            extra.write_text('keep')
            self.assertEqual(session.apply()['status'], 'unchanged')
            self.assertEqual(extra.read_text(), 'keep')

    def test_manifest_parent_requires_exact_directory_in_same_configuration(self):
        manifest = self.capture().document()
        directory = next(e for e in manifest['entries'] if e['path'] == 'empty')
        file = next(e for e in manifest['entries'] if e['path'] == 'settings')
        for path in ('absent/child', 'settings/child', 'Empty/child'):
            with self.subTest(path=path), self.assertRaisesRegex(api.SnapshotError, 'Missing directory'):
                api.Snapshot.parse(dict(manifest, entries=[*manifest['entries'], dict(file, path=path)]))
        with self.assertRaisesRegex(api.SnapshotError, 'case-conflicting'):
            api.Snapshot.parse(dict(manifest, entries=[*manifest['entries'], dict(file, path='Straße'),
                                                       dict(file, path='STRASSE')]))
        configs = manifest['software']['demo']['config']
        configs.append(dict(configs[0], id='second', path='other'))
        entries = [*manifest['entries'], dict(directory, config='second', path=''),
                   dict(file, config='second', path='empty/child')]
        with self.assertRaisesRegex(api.SnapshotError, 'Missing directory'):
            api.Snapshot.parse(dict(manifest, entries=entries))

    def test_invalid_archive_members(self):
        self.capture()
        with zipfile.ZipFile(self.archive) as original:
            contents = {name: original.read(name) for name in original.namelist()}
        payload = next(name for name in contents if name.startswith('payload/'))
        for case in ('traversal', 'duplicate', 'case conflict', 'digest', 'special type', 'missing', 'extra', 'substituted'):
            with self.subTest(case=case):
                archive = self.root / 'invalid.zip'
                with zipfile.ZipFile(archive, 'w') as out:
                    for name, data in contents.items():
                        if name == payload and case in ('missing', 'substituted'):
                            continue
                        info = zipfile.ZipInfo(name)
                        if name == payload and case == 'special type':
                            info.external_attr = 0o120777 << 16
                        if name == payload and case == 'digest':
                            data = b'x' * len(data)
                        out.writestr(info, data)
                    if case in ('extra', 'substituted'):
                        out.writestr('extra', b'')
                    elif case == 'traversal':
                        out.writestr('../outside', b'')
                    elif case == 'duplicate':
                        with warnings.catch_warnings():
                            warnings.simplefilter('ignore', UserWarning)
                            out.writestr(payload, contents[payload])
                    elif case == 'case conflict':
                        out.writestr(payload.upper(), contents[payload])
                with self.assertRaises(api.SnapshotError):
                    api.verify_archive(archive)
                if case == 'digest':
                    with self.assertRaisesRegex(api.SnapshotError, 'digest mismatch'):
                        api.apply_snapshot(archive, dry=True, platform=self.platform, roots=self.roots)
