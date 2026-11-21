"""Unified software declarations and snapshot selection (no host mutation)."""
from pathlib import Path
import re
from copy import deepcopy
import tomllib

from dotfile_viewer.install_config import ROOT, PLATFORMS, InstallSchema, DeclarationError, sort_items

ID = re.compile(r'[a-z0-9][a-z0-9-]*\Z')
ROOTS = {'home', 'config_home', 'app_data', 'local_app_data'}


def relative(value):
    return (isinstance(value, str) and bool(value) and not re.search(r'[\\:|;\x00-\x1f<>"*?]', value)
            and all(p not in ('', '.', '..') and not p.endswith((' ', '.'))
                    and not re.match(r'(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)', p)
                    for p in value.split('/')))


def validate(identity, data, schema=None):
    schema = schema or InstallSchema()
    if identity in ('all', 'none') or not ID.fullmatch(identity) or not isinstance(data, dict) or set(data) - {'schema_version', 'install', 'config', 'requires'}:
        raise DeclarationError('Invalid software declaration')
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise DeclarationError('Unsupported software schema version')
    installs = data.get('install', {})
    if not isinstance(installs, dict):
        raise DeclarationError('install must be a table')
    for section, fields in installs.items():
        platform, _, method = section.partition('/')
        if platform not in PLATFORMS or not isinstance(fields, dict):
            raise DeclarationError('Invalid installation platform')
        if any(k not in schema.patterns or not isinstance(v, str) or re.search(r'[|\t\r\n\x00]', v) for k, v in fields.items()):
            raise DeclarationError('Invalid installation field')
        if method and (platform not in installs or method not in installs[platform].get('methods', '').split()):
            raise DeclarationError('Undeclared installation method')
    for platform in installs:
        if '/' not in platform:
            schema.normalize(installs, platform)
    requires = data.get('requires', {})
    if not isinstance(requires, dict):
        raise DeclarationError('requires must be a table')
    for platform, names in requires.items():
        if platform not in PLATFORMS or not isinstance(names, list) or any(not isinstance(n, str) or not ID.fullmatch(n) for n in names) or len(names) != len(set(names)):
            raise DeclarationError('Invalid software dependencies')
    configs = data.get('config', [])
    if not isinstance(configs, list):
        raise DeclarationError('config must be an array of tables')
    names = set()
    for config in configs:
        if not isinstance(config, dict) or set(config) - {'id', 'platforms', 'root', 'path', 'exclude', 'environments'} or not {'id', 'platforms', 'root', 'path'} <= set(config):
            raise DeclarationError('Invalid configuration declaration')
        name = config['id']
        if not isinstance(name, str) or not ID.fullmatch(name) or name in names:
            raise DeclarationError('Invalid or duplicate configuration ID')
        names.add(name)
        platforms = config['platforms']
        if not isinstance(platforms, list) or not platforms or any(p not in PLATFORMS for p in platforms) or len(platforms) != len(set(platforms)):
            raise DeclarationError('Invalid configuration platforms')
        if config['root'] not in ROOTS or not relative(config['path']):
            raise DeclarationError('Invalid configuration path')
        if ('windows' in platforms and config['root'] == 'config_home') or (any(p != 'windows' for p in platforms) and config['root'] in {'app_data', 'local_app_data'}):
            raise DeclarationError('Configuration root unavailable on platform')
        environments = config.get('environments', ['native', 'wsl'])
        if not isinstance(environments, list) or not environments or any(e not in ('native', 'wsl') for e in environments) or len(environments) != len(set(environments)):
            raise DeclarationError('Invalid configuration environments')
        exclude = config.get('exclude', [])
        if not isinstance(exclude, list) or any(not relative(p) for p in exclude) or len(exclude) != len(set(exclude)):
            raise DeclarationError('Invalid configuration exclusions')
    if not installs and not configs:
        raise DeclarationError('Empty software declaration')
    return data


def read_catalog(directory=ROOT / 'software/items'):
    schema = InstallSchema()
    catalog = {p.stem: validate(p.stem, tomllib.loads(p.read_text(encoding='utf-8')), schema) for p in sorted(Path(directory).glob('*.toml'))}
    if not catalog:
        raise DeclarationError('No software declarations found')
    # Validate all platform dependency graphs, including configuration-only items.
    for platform in PLATFORMS:
        select(catalog, platform, 'native', ['all'], filter_environment=False)
        installation(catalog, platform)
    return catalog


def installation(catalog, platform):
    schema = InstallSchema()
    items = {}
    for identity, data in catalog.items():
        if platform in data.get('install', {}):
            item = schema.normalize(data['install'], platform)
            extra = data.get('requires', {}).get(platform, [])
            item['depends_on'] = schema.dependencies(item.get('depends_on', ''), ' '.join(extra))
            items[identity] = dict(item, id=identity)
    return sort_items(items)


def available(data, platform):
    return platform in data.get('install', {}) or any(platform in c['platforms'] for c in data.get('config', []))


def select(catalog, platform, environment, names, *, filter_environment=True):
    if platform not in PLATFORMS or environment not in ('native', 'wsl'):
        raise DeclarationError('Unsupported platform/environment')
    schema = InstallSchema()
    candidates = {i: d for i, d in catalog.items() if available(d, platform)}
    def eligible(data):
        if platform not in data.get('install', {}):
            return any(platform in c['platforms'] and environment in c.get('environments', ['native', 'wsl']) for c in data.get('config', []))
        item = schema.normalize(data['install'], platform)
        return environment in item['environments'].split() and not (environment == 'wsl' and item.get('wsl_provider'))
    roots = sorted(i for i, d in candidates.items() if not filter_environment or eligible(d)) if names == ['all'] else names
    if not roots and names == ['all']:
        return {}
    if not roots or len(roots) != len(set(roots)):
        raise DeclarationError('Select at least one unique software ID')
    done, visiting, result = set(), set(), {}
    def visit(identity):
        if identity in done:
            return
        if identity in visiting:
            raise DeclarationError('Software dependency cycle')
        if identity not in candidates:
            raise DeclarationError(f'Unknown or unavailable software: {identity}')
        data = candidates[identity]
        if filter_environment and not eligible(data):
            raise DeclarationError(f'Software unavailable in {environment}: {identity}')
        visiting.add(identity)
        installs = data.get('install', {})
        deps = list(data.get('requires', {}).get(platform, []))
        if platform in installs:
            # Capture alternatives' dependencies too: preserve the complete recipe.
            for section, values in installs.items():
                if section == platform or section.startswith(platform + '/'):
                    deps += values.get('depends_on', '').split() + values.get('install_depends_on', '').split()
        for dependency in dict.fromkeys(deps):
            visit(dependency)
        visiting.remove(identity)
        done.add(identity)
        result[identity] = dict(schema_version=1,
            install={k: dict(v) for k, v in installs.items() if k == platform or k.startswith(platform + '/')},
            config=[dict(deepcopy(c), platforms=[platform]) for c in data.get('config', []) if platform in c['platforms'] and (not filter_environment or environment in c.get('environments', ['native', 'wsl']))],
            requires={platform: list(data.get('requires', {}).get(platform, []))})
    for identity in roots:
        visit(identity)
    return result


def profile_selection(path):
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or key in values or key not in {'schema_version', 'software'}:
            raise DeclarationError('Profiles require schema_version=3 and software=...')
        values[key] = value
    names = values.get('software', '').split()
    if values.get('schema_version') != '3' or not names or any(not ID.fullmatch(n) for n in names) or len(names) != len(set(names)) or ('all' in names and names != ['all']):
        raise DeclarationError('Invalid software profile')
    return names


def write_catalog(catalog, directory):
    """Serialize already validated declarations into private staging, never repo state."""
    import json
    directory = Path(directory)
    directory.mkdir(parents=True)
    for identity, data in catalog.items():
        validate(identity, data)
        lines = ['schema_version = 1', '']
        for section, values in data.get('install', {}).items():
            lines += ['[install.' + json.dumps(section) + ']']
            lines += [key + ' = ' + json.dumps(value) for key, value in values.items()]
            lines += ['']
        if data.get('requires'):
            lines += ['[requires]'] + [json.dumps(key) + ' = ' + json.dumps(value) for key, value in data['requires'].items()] + ['']
        for config in data.get('config', []):
            lines += ['[[config]]'] + [key + ' = ' + json.dumps(value) for key, value in config.items()] + ['']
        (directory / (identity + '.toml')).write_text('\n'.join(lines), encoding='utf-8')


def installation_roots(catalog, platform, environment, names):
    """Map configuration-only roots to install roots without eagerly requesting
    installation-only dependencies of an already present application."""
    chosen = select(catalog, platform, environment, names)
    result = []
    def visit(identity):
        data = chosen[identity]
        if platform in data.get('install', {}):
            if identity not in result:
                result.append(identity)
        else:
            for dependency in data.get('requires', {}).get(platform, []):
                visit(dependency)
    for identity in chosen if names == ['all'] else names:
        visit(identity)
    return result
