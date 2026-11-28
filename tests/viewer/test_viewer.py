from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile
from dotfile_viewer import api, cli, files
from dotfile_viewer.files import observe
from dotfile_viewer.install_config import DeclarationError, InstallSchema, sort_items


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / 'config'
        (self.config / 'demo/empty').mkdir(parents=True)
        (self.config / 'demo/settings').write_bytes(b'hello\r\n')
        (self.config / 'demo/ignored').write_text('private')
        self.catalog = self.root / 'catalog'
        (self.catalog / 'software/items').mkdir(parents=True)
        (self.catalog / 'workflow/profiles').mkdir(parents=True)
        (self.catalog / 'software/items/demo.toml').write_text('schema_version = 1\n[[config]]\nid="settings"\nplatforms=["macos"]\nroot="config_home"\npath="demo"\nexclude=["ignored"]\n')
        (self.catalog / 'workflow/profiles/test.conf').write_text('schema_version=3\nsoftware=demo\n')
        self.platform = api.Platform('macos', 'arm64', 'native')
        self.roots = {'config_home': str(self.config), 'home': str(self.root)}
        self.editor = api.Editor(self.catalog, platform=self.platform, roots=self.roots, backup=self.root / 'backups')
        self.env = patch.dict(os.environ, {'DOTFILES_DEPLOYMENT_LOCK': str(self.root / 'lease'), 'DOTFILES_DEPLOYMENT_TOKEN': ''})
        self.env.start()
        self.archive = self.root / 'snapshot.zip'

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def capture(self):
        return api.export_snapshot(self.archive, catalog=self.catalog, software=['demo'], platform=self.platform, roots=self.roots)

    def read(self):
        return self.editor.read('demo', 'settings', 'settings')

    def save(self, text, version=None, **kwargs):
        return self.editor.save('demo', 'settings', 'settings', text, version or self.read()['version'], **kwargs)



class ViewerTest(Fixture):
    def test_dependency_sort_order(self):
        graph = {
            name: dict(id=name, order=order, depends_on=deps, environments='native')
            for name, order, deps in [('other', '10', ''), ('root', '2', ''), ('b', '1', 'root'),
                                      ('a', '1', 'root'), ('end', '0', 'a b')]
        }
        self.assertEqual([row['id'] for row in sort_items(graph)], ['root', 'a', 'b', 'end', 'other'])

    def test_dependency_validation(self):
        graph = {'app': dict(id='app', order='0', depends_on='runtime', environments='native wsl')}
        with self.assertRaisesRegex(DeclarationError, 'Missing dependency runtime for app'):
            sort_items(graph)
        graph['runtime'] = dict(id='runtime', order='1', environments='native')
        with self.assertRaisesRegex(DeclarationError, 'Dependency unavailable in selected environment'):
            sort_items(graph)
        graph['runtime'].update(wsl_provider='windows-docker-desktop', provides='docker')
        self.assertEqual([row['id'] for row in sort_items(graph)], ['runtime', 'app'])
        graph['runtime']['depends_on'] = 'app'
        with self.assertRaisesRegex(DeclarationError, 'Dependency cycle'):
            sort_items(graph)

    def test_shared_installation_roots(self):
        platform = 'linux.apt'
        catalog = {
            name: dict(schema_version=1, install={platform: dict(
                method='apt', packages=name, source_kind='default', order='1')})
            for name in ('alpha', 'zulu', 'runtime')
        }
        catalog['zulu']['install'][platform]['depends_on'] = 'runtime'
        for name, deps in [('shared', ['zulu', 'alpha']), ('left', ['shared']),
                           ('right', ['shared', 'runtime']), ('leaf', [])]:
            catalog[name] = dict(schema_version=1, requires={platform: deps},
                                 config=[dict(id='cfg', path=name, root='home', platforms=[platform])])

        for names, expected in [(['left', 'right'], ['zulu', 'alpha', 'runtime']),
                                (['alpha', 'left'], ['alpha', 'zulu']),
                                (['zulu'], ['zulu']), (['leaf'], []),
                                (['all'], ['alpha', 'runtime', 'zulu'])]:
            with self.subTest(names=names):
                self.assertEqual(api.installation_roots(catalog, platform, 'native', names), expected)

    def test_all_selection_checks_eligibility_once(self):
        platform = 'linux.apt'
        native = dict(method='apt', packages='runtime', source_kind='default', order='1', environments='native')
        catalog = {'runtime': dict(schema_version=1, install={platform: native}),
                   'cfg': dict(schema_version=1, config=[dict(id='cfg', root='home', path='cfg',
                                                            platforms=[platform], environments=['native'])])}
        with patch.object(InstallSchema, 'normalize', autospec=True, side_effect=InstallSchema.normalize) as normalize:
            self.assertEqual(list(api.select(catalog, platform, 'native', ['all'])), ['cfg', 'runtime'])
            normalize.assert_called_once()
        self.assertEqual(api.select(catalog, platform, 'wsl', ['all']), {})
        catalog['cfg']['config'][0]['environments'] = ['wsl']
        catalog['cfg']['requires'] = {platform: ['runtime']}
        for names in (['all'], ['cfg']):
            with self.assertRaisesRegex(DeclarationError, 'unavailable in wsl: runtime'):
                api.select(catalog, platform, 'wsl', names)
        self.assertEqual(list(api.select(catalog, platform, 'wsl', ['all'], filter_environment=False)),
                         ['runtime', 'cfg'])

    def test_catalog_and_profiles(self):
        self.assertEqual(api.selection(self.catalog, profile='test'), ['demo'])
        self.assertIn('fish', api.load_catalog())
        self.assertEqual(len(self.editor.configurations()), 1)

    def test_write_catalog_reuses_schema_per_call(self):
        catalog = api.load_catalog(self.catalog)
        catalog['other'] = catalog['demo']
        with patch.object(InstallSchema, '__init__', autospec=True, side_effect=InstallSchema.__init__) as init:
            empty = self.root / 'empty'
            api.write_catalog({}, empty)
            self.assertEqual(list(empty.iterdir()), [])
            init.assert_not_called()
            for name in ('first', 'second'):
                api.write_catalog(catalog, self.root / name)
                init.assert_called_once()
                init.reset_mock()

        partial = self.root / 'partial'
        with self.assertRaises(DeclarationError):
            api.write_catalog({'demo': catalog['demo'], 'invalid': {}}, partial)
        self.assertEqual([p.name for p in partial.iterdir()], ['demo.toml'])

    def test_schema_reuse_for_catalog_and_manifest(self):
        items = self.catalog / 'software/items'
        for name, order in [('client', 1), ('runtime', 2)]:
            declaration = ('schema_version = 1\n[install."linux.apt"]\nmethod="apt"\n'
                           f'packages="{name}"\nsource_kind="default"\norder="{order}"\n')
            if name == 'client':
                declaration += 'depends_on="runtime"\nenvironments="native"\n'
            (items / f'{name}.toml').write_text(declaration)
        with patch.object(InstallSchema, '__init__', autospec=True, side_effect=InstallSchema.__init__) as init:
            catalog = api.load_catalog(self.catalog)
            init.assert_called_once()
        self.assertEqual([row['id'] for row in api.installation(catalog, 'linux.apt')], ['runtime', 'client'])
        self.assertEqual(list(api.select(catalog, 'linux.apt', 'native', ['client'])), ['runtime', 'client'])
        self.assertEqual(list(api.select(catalog, 'linux.apt', 'wsl', ['all'])), ['runtime'])

        snapshot = api.export_snapshot(self.archive, catalog=self.catalog, software=['client'], dry=True,
                                       platform=api.Platform('linux.apt', 'x86_64', 'native'), roots=self.roots)
        manifest = snapshot.document()
        with patch.object(InstallSchema, '__init__', autospec=True, side_effect=InstallSchema.__init__) as init:
            self.assertEqual(api.Snapshot.parse(manifest), snapshot)
            init.assert_called_once()

        del manifest['software']['runtime']
        with self.assertRaisesRegex(DeclarationError, 'runtime'):
            api.Snapshot.parse(manifest)
        (items / 'runtime.toml').unlink()
        with self.assertRaisesRegex(DeclarationError, 'runtime'):
            api.load_catalog(self.catalog)

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

    def test_unix_shell_defaults(self):
        for name in ('macos', 'linux', 'wsl'):
            selected = api.selection(profile=name)
            self.assertIn('zsh', selected)
            self.assertTrue({'eza', 'fzf', 'starship', 'atuin', 'zoxide'} <= set(selected))
            self.assertTrue({'fish', 'less', 'mise', 'opencode', 'openjdk', 'ruby', 'dotnet', 'texlive'} <= set(selected))
            self.assertEqual('jetbrains-toolbox' in selected, name != 'wsl')
            self.assertEqual('antidote' in selected, name == 'macos')
            self.assertEqual(api.selection(profile=name + '-full'), ['all'])

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

    def test_linux_desktop_font_selection(self):
        catalog = api.load_catalog()
        fonts = {'sf-pro-text', 'pingfang-sc'}
        self.assertEqual(set(api.selection(profile='linux-fonts')), fonts)
        for platform in ('linux.apt', 'linux.dnf', 'linux.pacman'):
            with self.subTest(platform=platform):
                for roots in (api.selection(profile='linux'), ['desktop-fonts']):
                    self.assertTrue(fonts <= api.select(catalog, platform, 'native', roots).keys())
                self.assertTrue(fonts.isdisjoint(api.select(catalog, platform, 'wsl', ['all'])))

    def test_windows_desktop_font_selection(self):
        catalog = api.load_catalog()
        fonts = {'sf-pro-text', 'pingfang-sc'}
        self.assertEqual(set(api.selection(profile='windows-fonts')), fonts)
        self.assertTrue({'eza', 'openjdk', 'ruby', 'dotnet', 'texlive', 'jetbrains-toolbox'} <= set(api.selection(profile='windows')))
        for profile in ('windows', 'windows-full', 'windows-fonts'):
            selected = api.select(catalog, 'windows', 'native', api.selection(profile=profile))
            self.assertTrue(fonts <= selected.keys())
        for name in fonts:
            rules = catalog[name]['install']
            for field in ('method', 'source_url', 'font_files', 'font_family'):
                self.assertEqual(rules['windows'][field], rules['linux.apt'][field])

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
        for data in (b'', b'\xef\xbb\xbfhello\r\n', b'x' * limit, b'x' * (limit + 1)):
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

    def test_cli_export_output(self):
        args = ['--catalog', str(self.catalog), 'snapshot', 'export', '--software', 'demo']
        with patch.object(api, 'host', return_value=self.platform), patch.object(api, 'host_roots', return_value=self.roots):
            for dry in (False, True):
                for as_json in (False, True):
                    with self.subTest(dry=dry, as_json=as_json):
                        archive = self.root / f'{dry}-{as_json}.zip'
                        options = (['--dry-run'] if dry else []) + (['--json'] if as_json else [])
                        output = io.StringIO()
                        with redirect_stdout(output):
                            self.assertEqual(cli.main([*args, '--output', str(archive), *options]), 0)
                        self.assertEqual(archive.exists(), not dry)
                        if as_json:
                            self.assertTrue(json.loads(output.getvalue())['entries'])
                        else:
                            self.assertEqual(output.getvalue(), '')

    def test_capture_reuses_index(self):
        from dotfile_viewer import snapshot
        for count in (1, 8):
            configs = []
            for i in range(count):
                name = f'item-{i}'
                (self.config / name).write_text(name)
                configs.append(dict(id=name, platforms=['macos'], root='config_home', path=name))
            catalog = {'demo': dict(schema_version=1, config=configs)}
            output = self.root / f'{count}.zip'
            with patch.object(snapshot, 'configurations', wraps=snapshot.configurations) as index:
                preview = snapshot.capture(catalog, self.platform, ['demo'], self.roots, output, dry=True)
                self.assertEqual(index.call_count, 2)
            saved = snapshot.capture(catalog, self.platform, ['demo'], self.roots, output)
            self.assertEqual(saved.entries, preview.entries)
            self.assertEqual(api.verify_archive(output).entries, preview.entries)

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
        inventory = snapshot._inventory
        for change in ('add', 'redirect'):
            def scan(configs, locations):
                result = inventory(configs, locations)
                if change == 'add':
                    (self.config / 'demo/new').touch()
                else:
                    self.roots['config_home'] = str(self.root / 'alternate')
                return result

            (self.root / 'alternate/demo').mkdir(parents=True, exist_ok=True)
            with patch.object(snapshot, '_inventory', side_effect=scan), self.assertRaises(api.SnapshotError):
                self.capture()
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

    def test_cli_apply_expected_version(self):
        self.capture()
        target = self.config / 'demo/settings'
        backup = self.root / 'backups'
        args = ['snapshot', 'apply', '--snapshot', str(self.archive)]
        with (patch('dotfile_viewer.application.host', return_value=self.platform),
              patch('dotfile_viewer.application.host_roots', return_value=self.roots),
              patch.dict(os.environ, {'DOTFILES_BACKUP_DIR': str(backup)})):
            def preview_version():
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.main([*args, '--dry-run', '--json']), 0)
                return json.loads(output.getvalue())['version']

            version = preview_version()
            target.write_text('local change')
            with redirect_stderr(io.StringIO()) as error:
                self.assertEqual(cli.main([*args, '--expected-version', version]), 4)
            self.assertIn('preview again', error.getvalue())
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as invalid:
                cli.main([*args, '--expected-version', ''])
            self.assertEqual(invalid.exception.code, 2)
            self.assertEqual(target.read_text(), 'local change')
            self.assertFalse(backup.exists())
            version = preview_version()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--expected-version', version]), 0)
            self.assertEqual(target.read_bytes(), b'hello\r\n')

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

    def test_invalid_archive_members(self):
        self.capture()
        with zipfile.ZipFile(self.archive) as original:
            contents = {name: original.read(name) for name in original.namelist()}
        payload = next(name for name in contents if name.startswith('payload/'))
        for case in ('traversal', 'duplicate', 'case conflict', 'digest', 'special type'):
            with self.subTest(case=case):
                archive = self.root / 'invalid.zip'
                with zipfile.ZipFile(archive, 'w') as out:
                    for name, data in contents.items():
                        info = zipfile.ZipInfo(name)
                        if name == payload and case == 'special type':
                            info.external_attr = 0o120777 << 16
                        if name == payload and case == 'digest':
                            data = b'x' * len(data)
                        out.writestr(info, data)
                    if case == 'traversal':
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

    def test_backup_failure_leaves_original(self):
        before = (self.config / 'demo/settings').read_bytes()
        with patch('dotfile_viewer.files.shutil.copy2', side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):
                self.save('changed')
        self.assertEqual((self.config / 'demo/settings').read_bytes(), before)

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

    def test_edit_retains_original_draft_bytes(self):
        for expected_code, raw in ((3, b'invalid\xff'), (4, b'\xef\xbb\xbfdraft\r\n')):
            with self.subTest(expected_code=expected_code):
                draft = None
                target = self.config / 'demo/settings'
                def edit(arguments, **kwargs):
                    nonlocal draft
                    draft = Path(arguments[-1])
                    draft.write_bytes(raw)
                    if expected_code == 4:
                        target.write_bytes(b'external')
                    return cli.subprocess.CompletedProcess(arguments, 0)
                errors = io.StringIO()
                with (patch.dict(os.environ, VISUAL='editor'), patch.object(tempfile, 'tempdir', str(self.root)),
                      patch.object(api, 'Editor', return_value=self.editor), patch.object(cli.subprocess, 'run', side_effect=edit),
                      redirect_stderr(errors)):
                    code = cli.main(['edit', 'demo', 'settings', 'settings'])
                self.assertEqual(code, expected_code, errors.getvalue())
                self.assertIn(str(draft.parent), errors.getvalue())
                self.assertEqual(draft.read_bytes(), raw)
                self.assertEqual(target.read_bytes(), b'external' if expected_code == 4 else b'hello\r\n')

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

    @unittest.skipIf(os.name == 'nt', 'Unix XDG CLI fixture')
    def test_cli_script_write(self):
        import subprocess
        import sys
        declaration = self.catalog / 'software/items/demo.toml'
        declaration.write_text(declaration.read_text().replace('platforms=["macos"]', 'platforms=["' + api.host().branch + '"]'))
        env = dict(os.environ, XDG_CONFIG_HOME=str(self.config), XDG_STATE_HOME=str(self.root / 'state'), DOTFILES_BACKUP_DIR=str(self.root / 'backups'))
        command = [sys.executable, '-m', 'dotfile_viewer', '--catalog', str(self.catalog)]
        show = subprocess.run([*command, 'show', 'demo', 'settings', 'settings', '--json'], env=env, capture_output=True, text=True)
        self.assertEqual(show.returncode, 0, show.stderr)
        version = json.loads(show.stdout)['version']
        write = subprocess.run([*command, 'write', 'demo', 'settings', 'settings', '--from', '-', '--expected-version', version], input='cli\n', env=env, capture_output=True, text=True)
        self.assertEqual(write.returncode, 0, write.stderr)
        stale = subprocess.run([*command, 'write', 'demo', 'settings', 'settings', '--from', '-', '--expected-version', version], input='stale', env=env, capture_output=True, text=True)
        self.assertEqual(stale.returncode, 4, stale.stderr)


if __name__ == '__main__':
    unittest.main()
