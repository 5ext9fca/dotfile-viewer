"""Snapshot v2 document model and validation."""
from dataclasses import dataclass
from datetime import datetime
import re

from .errors import SnapshotError
from .install_schema import InstallSchema
from .paths import is_excluded
from .software import PLATFORMS, _installation, _select, relative, validate


@dataclass(frozen=True)
class Platform:
    branch: str
    architecture: str
    environment: str

    def validate(self):
        if (self.branch not in PLATFORMS or self.environment not in ('native', 'wsl')
                or self.architecture not in ('x86_64', 'arm64', 'x86', 'armv7l', 'ppc64le', 's390x', 'riscv64')
                or (self.environment == 'wsl' and not self.branch.startswith('linux.'))):
            raise SnapshotError('Unsupported snapshot platform')
        return self

    def document(self):
        return dict(branch=self.branch, architecture=self.architecture, environment=self.environment)


@dataclass(frozen=True)
class Snapshot:
    platform: Platform
    created_at: str
    selection: tuple[str, ...]
    software: dict
    entries: tuple[dict, ...]
    schema_version: int = 2

    def document(self):
        return dict(schema_version=self.schema_version, platform=self.platform.document(), created_at=self.created_at,
                    selection=list(self.selection), software=self.software, entries=list(self.entries))

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != {'schema_version', 'platform', 'created_at', 'selection', 'software', 'entries'} or type(data['schema_version']) is not int or data['schema_version'] != 2:
            raise SnapshotError('Unsupported snapshot manifest; export a new v2 snapshot')
        if not isinstance(data['platform'], dict) or set(data['platform']) != {'branch', 'architecture', 'environment'}:
            raise SnapshotError('Invalid snapshot platform')
        platform = Platform(**data['platform']).validate()
        if not isinstance(data['created_at'], str) or not datetime.fromisoformat(data['created_at']).tzinfo:
            raise SnapshotError('Invalid snapshot timestamp')
        names = data['selection']
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names) or len(names) != len(set(names)):
            raise SnapshotError('Invalid snapshot selection')
        catalog = data['software']
        _validate_software(catalog, platform, names)
        entries = data['entries']
        _validate_entries(entries, catalog)
        return cls(platform, data['created_at'], tuple(names), catalog, tuple(entries))


def _validate_software(catalog, platform, names):
    if not isinstance(catalog, dict) or not catalog:
        raise SnapshotError('Empty snapshot software set')
    schema = InstallSchema()
    for identity, declaration in catalog.items():
        validate(identity, declaration, schema)
        if (any(p != platform.branch and not p.startswith(platform.branch + '/') for p in declaration.get('install', {}))
                or any(c['platforms'] != [platform.branch] for c in declaration.get('config', []))
                or any(p != platform.branch for p in declaration.get('requires', {}))):
            raise SnapshotError('Snapshot contains declarations for another platform')
    selected = _select(catalog, platform.branch, platform.environment, names, schema)
    if selected != catalog:
        raise SnapshotError('Snapshot software differs from selection closure')
    _installation(catalog, platform.branch, schema)


def _validate_entries(entries, catalog):
    if not isinstance(entries, list):
        raise SnapshotError('Invalid snapshot entries')
    configs = configurations(catalog)
    exclusions = {key: set(config.get('exclude', ())) for key, config in configs.items()}
    seen, roots, directories = set(), set(), set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'software', 'config', 'path', 'kind', 'size', 'sha256', 'executable'}:
            raise SnapshotError('Invalid snapshot entry')
        key = (entry['software'], entry['config'])
        if key not in configs or not isinstance(entry['path'], str) or (entry['path'] and not relative(entry['path'])):
            raise SnapshotError('Invalid snapshot entry path')
        if is_excluded(entry['path'], exclusions[key]):
            raise SnapshotError('Excluded content in snapshot')
        member = member_name(entry)
        folded = member.casefold()
        if folded in seen:
            raise SnapshotError('Duplicate or case-conflicting snapshot path')
        seen.add(folded)
        if entry['path'] == '':
            roots.add(key)
        if type(entry['size']) is not int or entry['size'] < 0 or type(entry['executable']) is not int or entry['executable'] & ~0o111:
            raise SnapshotError('Invalid file size or executable permission')
        if entry['kind'] == 'file':
            if not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
                raise SnapshotError('Invalid content digest')
        elif entry['kind'] != 'directory' or entry['size'] != 0 or entry['sha256'] != '' or entry['executable'] != 0:
            raise SnapshotError('Invalid directory entry')
        else:
            directories.add((*key, entry['path']))
    if roots != set(configs):
        raise SnapshotError('Missing declared configuration roots')
    for entry in entries:
        if entry['path']:
            parent = entry['path'].rsplit('/', 1)[0] if '/' in entry['path'] else ''
            if (entry['software'], entry['config'], parent) not in directories:
                raise SnapshotError('Missing directory or file/directory conflict')


def configurations(catalog):
    return {(identity, config['id']): config for identity, data in catalog.items() for config in data.get('config', [])}


def member_name(entry):
    return f"payload/{entry['software']}/{entry['config']}/root" + ('/' + entry['path'] if entry['path'] else '')
