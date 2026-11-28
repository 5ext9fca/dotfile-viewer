from unittest.mock import patch
from dotfile_viewer import api
from dotfile_viewer.install_config import DeclarationError, InstallSchema, sort_items
from viewer_fixture import Fixture


class CatalogTest(Fixture):
    def test_catalog_normalizes_each_installation_once(self):
        with patch.object(InstallSchema, 'normalize', autospec=True, side_effect=InstallSchema.normalize) as normalize:
            catalog = api.load_catalog()
        count = sum('/' not in section for item in catalog.values() for section in item.get('install', {}))
        self.assertEqual(normalize.call_count, count)

    def test_dependency_merge(self):
        schema = InstallSchema()
        self.assertEqual(schema.dependencies('z b', 'a z'), 'z b a')
        for fields in [('b b', ''), ('', 'b b')]:
            with self.assertRaisesRegex(DeclarationError, 'Duplicate dependency'):
                schema.dependencies(*fields)

    def test_sort_parses_environments_once_per_connected_node(self):
        from dotfile_viewer import install_config
        graph = {f'b{i}': dict(id=f'b{i}', order='1', environments='native wsl') for i in range(128)}
        dependencies = ' '.join(graph)
        graph.update({f'a{i}': dict(id=f'a{i}', order='2', depends_on=dependencies, environments='native')
                      for i in range(128)})
        graph['isolated'] = dict(id='isolated', order='0', environments='native')
        with patch.object(install_config, 'words', wraps=install_config.words) as split:
            rows = sort_items(graph)
        self.assertEqual(sum(call.args[0] in ('native', 'native wsl') for call in split.call_args_list), 256)
        self.assertEqual([r['id'] for r in rows], sorted(graph, key=lambda k: (graph[k]['order'], k)))

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

    def test_explicit_selection_only_checks_reachable_items(self):
        from dotfile_viewer import software
        platform = 'linux.apt'
        config = dict(id='c', platforms=[platform], root='home', path='c')
        catalog = {f'x{i}': dict(config=[config]) for i in range(4096)}
        for name in ('x1', 'x2'):
            catalog[name]['requires'] = {platform: ['x0']}
        for names, expected in [(['x0'], ['x0']), (['x2', 'x1'], ['x0', 'x2', 'x1'])]:
            with patch.object(software, 'available', wraps=software.available) as available:
                self.assertEqual(list(api.select(catalog, platform, 'native', names)), expected)
                self.assertEqual(available.call_count, len(expected))
        for target, names in [('macos', ['x0']), (platform, ['unknown'])]:
            with self.assertRaisesRegex(DeclarationError, 'Unknown or unavailable'):
                api.select(catalog, target, 'native', names)

    def test_deep_selection_and_installation_roots(self):
        items = self.catalog / 'software/items'
        names = [f'deep-{i}' for i in range(2000)]
        config = 'schema_version=1\n[[config]]\nid="c"\nplatforms=["linux.apt"]\nroot="home"\npath="c"\n'
        for name, dependency in zip(names[:-1], names[1:], strict=True):
            (items / f'{name}.toml').write_text(config + f'[requires]\n"linux.apt"=["{dependency}"]\n')
        leaf = items / f'{names[-1]}.toml'
        leaf.write_text('schema_version=1\n[install."linux.apt"]\nmethod="apt"\norder="1"\nsource_kind="default"\npackages="demo"\n')
        catalog = api.load_catalog(self.catalog)
        args = catalog, 'linux.apt', 'native', names[:1]
        self.assertEqual(list(api.select(*args)), names[::-1])
        self.assertEqual(api.installation_roots(*args), names[-1:])
        leaf.write_text(config + f'[requires]\n"linux.apt"=["{names[0]}"]\n')
        with self.assertRaisesRegex(DeclarationError, 'cycle'):
            api.load_catalog(self.catalog)

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

    def test_selection_copies_only_returned_declarations(self):
        with patch('dotfile_viewer.software.deepcopy', side_effect=AssertionError):
            catalog = api.load_catalog(self.catalog)
            self.assertEqual(api.installation_roots(catalog, 'macos', 'native', ['demo']), [])
        chosen = api.select(catalog, 'macos', 'native', ['demo'])
        chosen['demo']['config'][0]['exclude'].clear()
        self.assertEqual(catalog['demo']['config'][0]['exclude'], ['ignored'])
        with self.assertRaises(DeclarationError):
            api.installation_roots(catalog, 'unknown', 'native', ['demo'])

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

    def test_unix_shell_defaults(self):
        for name in ('macos', 'linux', 'wsl'):
            selected = api.selection(profile=name)
            self.assertIn('zsh', selected)
            self.assertTrue({'eza', 'fzf', 'starship', 'atuin', 'zoxide'} <= set(selected))
            self.assertTrue({'fish', 'less', 'mise', 'opencode', 'openjdk', 'ruby', 'dotnet', 'texlive'} <= set(selected))
            self.assertEqual('jetbrains-toolbox' in selected, name != 'wsl')
            self.assertEqual('antidote' in selected, name == 'macos')
            self.assertEqual(api.selection(profile=name + '-full'), ['all'])

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
