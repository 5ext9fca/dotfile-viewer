import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dotfile_viewer import api


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
