"""Public command line interface."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from . import api
from .install_config import DeclarationError


def nonempty_version(value):
    if not value:
        raise argparse.ArgumentTypeError('expected a non-empty plan version')
    return value


def parser():
    p = argparse.ArgumentParser(prog='dotfile-viewer')
    p.add_argument('--catalog', type=Path, help='Catalog root containing software/items and workflow/profiles')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('list', 'files', 'show', 'edit', 'write'):
        s = sub.add_parser(name)
        s.add_argument('--json', action='store_true')
        if name != 'list':
            s.add_argument('software'); s.add_argument('config')
        if name in ('show', 'edit', 'write'):
            s.add_argument('file', nargs='?', default='')
        if name in ('edit', 'write'):
            s.add_argument('--detach-hardlink', action='store_true')
        if name == 'write':
            s.add_argument('--from', dest='source', required=True)
            s.add_argument('--expected-version', required=True)
    snap = sub.add_parser('snapshot').add_subparsers(dest='action', required=True)
    export = snap.add_parser('export')
    choices = export.add_mutually_exclusive_group(required=True)
    choices.add_argument('--profile'); choices.add_argument('--software', nargs='+')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--dry-run', action='store_true')
    export.add_argument('--json', action='store_true', help='Print the snapshot manifest to stdout')
    for name in ('inspect', 'apply'):
        cmd = snap.add_parser(name)
        cmd.add_argument('--snapshot', required=True, type=Path)
        cmd.add_argument('--json', action='store_true')
        if name == 'apply':
            cmd.add_argument('--expected-version', metavar='VERSION', type=nonempty_version,
                             help='Require the plan version returned by snapshot apply --dry-run')
            cmd.add_argument('--dry-run', action='store_true')
            cmd.add_argument('--detach-hardlink', action='store_true')
    return p


def run(args):
    if args.command == 'snapshot':
        if args.action == 'export':
            snapshot = api.export_snapshot(args.output, catalog=args.catalog, profile=args.profile,
                                           software=args.software, dry=args.dry_run)
            return snapshot.document() if args.json else None
        if args.action == 'inspect':
            return api.verify_archive(args.snapshot).document()
        return api.apply_snapshot(args.snapshot, dry=args.dry_run, detach=args.detach_hardlink,
                                  expected_version=args.expected_version)
    editor = api.Editor(args.catalog)
    if args.command == 'list':
        return editor.configurations()
    if args.command == 'files':
        return editor.files(args.software, args.config)
    current = editor.read(args.software, args.config, args.file)
    if args.command == 'show':
        if args.json:
            return current
        if not current['editable']:
            raise api.CoreError(current['reason'])
        path, _ = editor.locate(args.software, args.config, args.file)
        # Reuse verified content rather than reopening a possibly changed file.
        text = current['content'].replace('\n', '\r\n' if current['newline'] == 'crlf' else '\n')
        sys.stdout.buffer.write((b'\xef\xbb\xbf' if current['bom'] else b'') + text.encode('utf-8'))
        return None
    if args.command == 'write':
        raw = sys.stdin.buffer.read() if args.source == '-' else Path(args.source).read_bytes()
        content = raw.decode('utf-8-sig')
        return editor.save(args.software, args.config, args.file, content, args.expected_version, detach=args.detach_hardlink)

    if not current['editable']:
        raise api.CoreError(current['reason'])
    command = os.environ.get('VISUAL') or os.environ.get('EDITOR')
    if not command:
        raise api.CoreError('Set VISUAL or EDITOR before editing')
    directory = tempfile.TemporaryDirectory(prefix='viewer-editor-', delete=False)
    retain = False
    try:
        file = Path(directory.name) / Path(current['path']).name
        file.write_text(current['content'], encoding='utf-8')
        arguments = shlex.split(command, posix=os.name != 'nt')
        if os.name == 'nt':
            arguments = [value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in ('\"', "'") else value for value in arguments]
        code = subprocess.run([*arguments, str(file)], check=False).returncode
        if code:
            raise api.CoreError(f'Editor exited with code {code}; original file unchanged')
        try:
            content = file.read_text(encoding='utf-8-sig')
            return editor.save(args.software, args.config, args.file, content, current['version'], detach=args.detach_hardlink)
        except (api.CoreError, OSError, UnicodeError) as exc:
            retain = True
            failure = api.ConflictError if isinstance(exc, api.ConflictError) else api.CoreError
            raise failure(f'{exc}; editor draft directory retained at {directory.name}') from exc
    finally:
        if not retain:
            directory.cleanup()


def main(argv=None):
    try:
        if sys.version_info < (3, 14):
            raise api.CoreError('Python >=3.14 is required')
        args = parser().parse_args(argv)
        result = run(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DeclarationError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    except api.ConflictError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 4
    except (api.CoreError, OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
