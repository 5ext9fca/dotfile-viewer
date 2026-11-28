from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dotfile_viewer import api, cli
from viewer_fixture import Fixture


class CliTest(Fixture):
    def test_show_and_save_preserve_text_format(self):
        path = self.config / 'demo/settings'
        bom = bytes.fromhex('efbbbf')
        for data in (b'', bom, b'plain', b'a\nb', bom + b'a\r\n\r\nb', '中文📁\n'.encode()):
            path.write_bytes(data)
            current = self.read()
            content = data.decode('utf-8-sig').replace('\r\n', '\n')
            self.assertEqual(current['content'], content)
            self.assertEqual(self.save(content, current['version'])['status'], 'unchanged')
            self.assertEqual(path.read_bytes(), data)
            raw = io.BytesIO()
            with io.TextIOWrapper(raw) as output, redirect_stdout(output), patch.object(api, 'Editor', return_value=self.editor):
                self.assertEqual(cli.main(['show', 'demo', 'settings', 'settings']), 0)
                self.assertEqual(raw.getvalue(), data)
            self.save('c\r\nd')
            expected = b'c\r\nd' if b'\r' in data else b'c\nd'
            self.assertEqual(path.read_bytes(), (bom if data.startswith(bom) else b'') + expected)
        for data in (b'\r', b'a\r\nb\n', b'a\r\r\n', b'\x00', b'\xff'):
            path.write_bytes(data)
            current = self.read()
            self.assertFalse(current['editable'])
            self.assertEqual(current['reason'], 'Not UTF-8' if data == b'\xff' else 'Binary or mixed newline content')

            with (patch.object(api, 'Editor', return_value=self.editor),
                  redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors):
                self.assertEqual(cli.main(['show', 'demo', 'settings', 'settings']), 3)
                self.assertEqual(output.getvalue(), '')
                self.assertIn(current['reason'], errors.getvalue())
            with self.assertRaisesRegex(api.CoreError, current['reason']):
                self.save('candidate', current['version'])
            self.assertEqual(path.read_bytes(), data)

    def test_encoded_size_boundary(self):
        limit = 2 * 1024 * 1024
        path = self.config / 'demo/settings'
        path.write_bytes(b'\xef\xbb\xbf\r\n')
        candidate = '文' + 'a' * (limit - 8) + '\n'
        expected = candidate.replace('\n', '\r\n').encode('utf-8-sig')
        self.assertEqual(len(expected), limit)
        self.save(candidate)
        self.assertEqual(path.read_bytes(), expected)
        current = self.read()
        self.assertEqual(current['content'], candidate)
        with self.assertRaisesRegex(api.CoreError, 'exceeds 2 MiB'):
            self.save(candidate + 'a', current['version'])
        raw = io.BytesIO()
        with io.TextIOWrapper(raw) as output, redirect_stdout(output), patch.object(api, 'Editor', return_value=self.editor):
            self.assertEqual(cli.main(['show', 'demo', 'settings', 'settings']), 0)
            self.assertEqual(raw.getvalue(), expected)

    def test_cli_readonly_handlers(self):
        with patch.object(api, 'Editor', return_value=self.editor):
            for args, expected in [(['list'], self.editor.configurations()),
                                   (['files', 'demo', 'settings'], self.editor.files('demo', 'settings'))]:
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(cli.main(args), 0)
                self.assertEqual(json.loads(output.getvalue()), expected)
        snapshot = self.capture()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['snapshot', 'inspect', '--snapshot', str(self.archive)]), 0)
        self.assertEqual(json.loads(output.getvalue()), snapshot.document())

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

    def test_script_write_reads_candidate_before_save(self):
        target = self.config / 'demo/settings'
        draft = self.root / 'draft'
        for source in (str(draft), '-'):
            for case, raw, code in [('same', b'hello\n', 0), ('save', b'new', 0),
                                    ('invalid', b'\xff', 3), ('stale', b'new', 4)]:
                with self.subTest(source=source, case=case):
                    target.write_bytes(b'hello\r\n')
                    version = 'stale' if case == 'stale' else self.read()['version']
                    draft.write_bytes(raw)
                    args = ['write', 'demo', 'settings', 'settings', '--from', source, '--expected-version', version]
                    with (patch.object(api, 'Editor', return_value=self.editor),
                          patch.object(cli.sys, 'stdin', io.TextIOWrapper(io.BytesIO(raw))),
                          patch('dotfile_viewer.editor.load_catalog', wraps=api.load_catalog) as load,
                          patch.object(self.editor, '_read_file', wraps=self.editor._read_file) as read,
                          redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                        self.assertEqual(cli.main(args), code)
                    self.assertEqual(load.call_count, int(case != 'invalid'))
                    self.assertEqual(read.call_count, int(case != 'invalid'))
                    self.assertEqual(target.read_bytes(), b'new' if case == 'save' else b'hello\r\n')

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
