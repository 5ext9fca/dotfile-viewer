"""Installation declarations, normalization and deterministic dependency graphs."""

from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from pathlib import Path
import re

from dotfile_viewer.errors import CoreError

from importlib.resources import files

ROOT = Path(str(files("dotfile_viewer").joinpath("resources")))
PLATFORMS = ('linux.apt', 'linux.dnf', 'linux.pacman', 'macos', 'windows')
COMMON = set('order methods preferred_method depends_on environments provides wsl_provider scope'.split())


class DeclarationError(CoreError):
    pass


def rows(path: Path) -> list[list[str]]:
    return [line.split('\t') for line in path.read_text(encoding='utf-8-sig').splitlines()
            if line and not line.startswith('#')]


def words(value: str) -> list[str]:
    return value.split() if value else []


@dataclass(frozen=True)
class Method:
    platforms: list[str]
    required: list[str]
    kinds: list[str]
    optional: list[str]


class InstallSchema:
    def __init__(self, directory: Path = ROOT / 'install/schema'):
        self.patterns = dict(rows(directory / 'fields.tsv'))
        self.methods = {r[0]: Method(words(r[1]), words(r[2]), words(r[3]),
                                    [] if r[4] == '-' else words(r[4]))
                        for r in rows(directory / 'methods.tsv')}
        self.sources = {r[0]: '' if r[1] == '-' else r[1] for r in rows(directory / 'sources.tsv')}
        self.providers = {r[0]: r[1] for r in rows(directory / 'runtime-providers.tsv')}
        self.capabilities = {r[0] for r in rows(directory / 'capabilities.tsv')}
        if any(not re.fullmatch('[a-z][a-z0-9-]*', value) for value in self.capabilities):
            raise DeclarationError('Invalid runtime capability')
        self.roots = {r[0]: (words(r[1]), words(r[2])) for r in rows(directory / 'discovery-roots.tsv')}

    def validate(self, entry: dict[str, str], platform: str) -> None:
        method_name, kind = entry.get('method', ''), entry.get('source_kind', '')
        method = self.methods.get(method_name)
        if method is None or platform not in method.platforms:
            raise DeclarationError(f'Unsupported method {method_name} for {platform}')
        if not entry.get('order') or len(entry['order']) > 6:
            raise DeclarationError('order must be 0..999999')
        if kind not in self.sources or kind not in method.kinds:
            raise DeclarationError(f'Unsupported source_kind {kind} for {method_name}')
        required = ['order', 'method', 'source_kind', *method.required, self.sources[kind]]
        for key in required:
            if key and not entry.get(key):
                raise DeclarationError(f'Missing {key}')
        allowed = set('order method source_kind scope check depends_on install_depends_on environments provides wsl_provider methods preferred_method detect_files detect_dirs'.split())
        allowed.update(method.required + method.optional + [self.sources[kind]])
        if self.sources[kind] == 'source_url':
            allowed.add('mirror_url')
        for key, value in entry.items():
            if key not in allowed:
                raise DeclarationError(f'Unsupported field {key} for {method_name}/{kind}')
            if value and not re.fullmatch(self.patterns[key], value):
                raise DeclarationError(f'Invalid {key}')
        if method_name in ('winget', 'msvc', 'rustup', 'pipx') and ' ' in entry.get('packages', ''):
            raise DeclarationError(f'{method_name} requires one package/toolchain')
        if kind != 'cargo-index' and any(entry.get(k, '').startswith('sparse') for k in ('source_url', 'mirror_url')):
            raise DeclarationError('sparse+ URLs require cargo-index')
        if method_name == 'texlive-iso' and any(
            not entry[key].startswith('https://') for key in ('source_url', 'mirror_url') if key in entry
        ):
            raise DeclarationError('texlive-iso requires HTTPS image URLs')
        if method_name == 'font-download':
            if not entry['source_url'].startswith('https://') or entry.get('mirror_url'):
                raise DeclarationError('font-download requires HTTPS and does not support mirrors')
            fonts = words(entry['font_files'])
            if len(fonts) != len(set(fonts)):
                raise DeclarationError('Duplicate font filename')
        if entry.get('provides') and entry['provides'] not in self.capabilities:
            raise DeclarationError('Unknown runtime capability')
        if entry.get('scope') and entry.get('environments'):
            raise DeclarationError('scope and environments cannot be combined')
        if entry.get('wsl_provider'):
            if not platform.startswith('linux.') or entry.get('environments') != 'native':
                raise DeclarationError('WSL provider requires native-only Linux installation')
            if entry['wsl_provider'] not in self.providers or entry.get('provides') != self.providers[entry['wsl_provider']]:
                raise DeclarationError('Unknown or mismatched runtime provider')

    def dependencies(self, common: str, specific: str) -> str:
        result: list[str] = []
        seen = set()
        for text in (common, specific):
            if text and not re.fullmatch(self.patterns['depends_on'], text):
                raise DeclarationError('Invalid depends_on')
            ids = words(text)
            if len(ids) != len(set(ids)):
                raise DeclarationError('Duplicate dependency')
            result.extend(name for name in ids if name not in seen)
            seen.update(ids)
        return ' '.join(result)

    def discovery(self, method: str, kind: str, text: str, platform: str) -> list[str]:
        result = []
        if not text:
            return result
        for item in text.split(';'):
            root, separator, path = item.partition(':')
            if not separator or root not in self.roots:
                raise DeclarationError('Unknown discovery root')
            platforms, kinds = self.roots[root]
            if platform not in platforms or kind not in kinds:
                raise DeclarationError('Unsupported discovery root for platform/type')
            if re.search(r'[\\|;:*?<>"\t]', path) or any(p in ('', '.', '..') for p in path.split('/')):
                raise DeclarationError('Invalid discovery path')
            if root == 'path' and (kind != 'file' or '/' in path):
                raise DeclarationError('path discovery requires a filename')
            result.append(f'{method}:{kind}:{item}')
        return result

    def normalize(self, sections: dict[str, dict[str, str]], platform: str) -> dict[str, str]:
        common = sections[platform]
        if not common.get('methods'):
            if common.get('preferred_method'):
                raise DeclarationError('preferred_method requires methods')
            selected = dict(common)
            self.validate(selected, platform)
            selected['depends_on'] = self.dependencies(common.get('depends_on', ''), common.get('install_depends_on', ''))
            specific_dependencies = common.get('install_depends_on', '')
            discovery = self.discovery(selected['method'], 'file', selected.get('detect_files', ''), platform)
            discovery += self.discovery(selected['method'], 'directory', selected.get('detect_dirs', ''), platform)
            if discovery and selected.get('check'):
                raise DeclarationError('check cannot be combined with discovery')
        else:
            if set(common) - COMMON:
                raise DeclarationError('Method-specific field in common section')
            methods = common['methods'].split(' ')
            preferred = common.get('preferred_method', '')
            if not preferred or preferred == 'manual' or preferred not in methods:
                raise DeclarationError('Invalid preferred_method')
            if len(methods) != len(set(methods)):
                raise DeclarationError('Duplicate installation method')
            discovery = []
            selected = {}
            for method in methods:
                subsection = sections.get(f'{platform}/{method}')
                if subsection is None:
                    raise DeclarationError(f'Missing method section {method}')
                forbidden = (COMMON | {'method', 'check', 'install_depends_on'}) - {'depends_on'}
                if set(subsection) & forbidden:
                    raise DeclarationError('Invalid method subsection field')
                if method == 'manual' and 'depends_on' in subsection:
                    raise DeclarationError('manual cannot declare installation dependencies')
                entry = {**common, **subsection, 'method': method}
                entry['depends_on'] = self.dependencies(common.get('depends_on', ''), subsection.get('depends_on', ''))
                if not entry.get('detect_files') and not entry.get('detect_dirs'):
                    raise DeclarationError('Installation method requires discovery paths')
                if method == 'manual':
                    if set(entry) - (COMMON | {'method', 'detect_files', 'detect_dirs'}):
                        raise DeclarationError('manual is discovery-only')
                else:
                    self.validate(entry, platform)
                discovery += self.discovery(method, 'file', entry.get('detect_files', ''), platform)
                discovery += self.discovery(method, 'directory', entry.get('detect_dirs', ''), platform)
                if method == preferred:
                    selected = entry
            specific_dependencies = sections[f'{platform}/{preferred}'].get('depends_on', '')
        common_dependencies = set(words(common.get('depends_on', '')))
        selected['install_depends_on'] = ' '.join(i for i in words(specific_dependencies) if i not in common_dependencies)
        selected['discovery'] = ';'.join(discovery)
        # Compatibility is resolved once, before any planning consumer sees it.
        selected['environments'] = selected.get('environments') or ('native' if selected.get('scope') == 'desktop' else 'native wsl')
        selected['probe_command'] = selected.pop('check', '')
        selected.pop('scope', None)
        selected['methods'] = selected.get('methods') or selected['method']
        selected['preferred_method'] = selected.get('preferred_method') or selected['method']
        selected['order'] = str(int(selected['order']))
        return selected


def sort_items(items: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    environments_by_name = {}
    def environments(name: str) -> set[str]:
        if name not in environments_by_name:
            item = items[name]
            allowed = set(words(item['environments']))
            if item.get('wsl_provider'):
                allowed.add('wsl')
            environments_by_name[name] = allowed
        return environments_by_name[name]

    dependents = {name: [] for name in items}
    remaining = {}
    for name, entry in items.items():
        dependencies = words(entry.get('depends_on', ''))
        remaining[name] = len(dependencies)
        for dependency in dependencies:
            if dependency not in items:
                raise DeclarationError(f'Missing dependency {dependency} for {name}')
            if not environments(name) <= environments(dependency):
                raise DeclarationError('Dependency unavailable in selected environment')
            dependents[dependency].append(name)

    ready = [(int(items[name]['order']), name) for name, count in remaining.items() if count == 0]
    heapify(ready)
    result = []
    while ready:
        _, name = heappop(ready)
        result.append(items[name])
        for dependent in dependents[name]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heappush(ready, (int(items[dependent]['order']), dependent))
    if len(result) != len(items):
        raise DeclarationError('Dependency cycle')
    return result
