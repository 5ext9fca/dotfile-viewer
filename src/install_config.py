"""Read installation declarations from TOML catalogs or legacy .conf files."""
from pathlib import Path
import re

from .install_schema import COMMON, PLATFORMS, ROOT, DeclarationError, InstallSchema, Method, rows, sort_items, words
from .software import _read_catalog, installation, read_catalog


def parse_file(path: Path, schema: InstallSchema) -> dict[str, dict[str, str]]:
    if not re.fullmatch('[a-z0-9][a-z0-9-]*', path.stem) or path.stat().st_size == 0:
        raise DeclarationError(f'Invalid or empty item: {path}')
    sections: dict[str, dict[str, str]] = {}
    section = None
    for number, raw in enumerate(path.read_text(encoding='utf-8-sig').split('\n'), 1):
        line = raw.removesuffix('\r').strip(' \t')
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'\[((?:macos|windows|linux\.(?:apt|dnf|pacman))(?:/[a-z][a-z-]*)?)\]', line)
        if match:
            section = match[1]
            if section in sections:
                raise DeclarationError(f'Duplicate section: {path}:{number}')
            sections[section] = {}
            continue
        key, separator, value = line.partition('=')
        if not separator or section is None:
            raise DeclarationError(f'Expected key=value inside a section: {path}:{number}')
        if key not in schema.patterns or key in sections[section] or re.search('[|\t\r]', value):
            raise DeclarationError(f'Unknown/duplicate field or invalid value: {path}:{number}')
        sections[section][key] = value
    for name in sections:
        if '/' in name:
            platform, method = name.split('/')
            if platform not in sections or method not in words(sections[platform].get('methods', '')):
                raise DeclarationError(f'Undeclared installation method section {name}')
    return sections


def read_items(directory: Path, platform: str, schema_directory: Path = ROOT / 'install/schema') -> list[dict[str, str]]:
    if platform not in PLATFORMS:
        raise DeclarationError('Unknown target platform')
    schema = InstallSchema(schema_directory)
    if next(directory.glob('*.toml'), None) is not None:
        _, items = _read_catalog(directory, schema, platform)
        return items
    files = sorted(directory.glob('*.conf'))
    if not files:
        raise DeclarationError('No item configurations found')
    all_items: dict[str, dict[str, dict[str, str]]] = {p: {} for p in PLATFORMS}
    for path in files:
        sections = parse_file(path, schema)
        for target in sections:
            if '/' not in target:
                all_items[target][path.stem] = {**schema.normalize(sections, target), 'id': path.stem}
    # Validate all declarations, including graphs outside the requested platform,
    # before publishing any records.
    sorted_platforms = {target: sort_items(items) for target, items in all_items.items()}
    return sorted_platforms[platform]
