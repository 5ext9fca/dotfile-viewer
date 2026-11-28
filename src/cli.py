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
from .text_codec import decode_candidate, encode_text
from .install_schema import DeclarationError


def nonempty_version(value):
    if not value:
        raise argparse.ArgumentTypeError('expected a non-empty plan version')
    return value


def parser():
    p = argparse.ArgumentParser(prog='dotfile-viewer')
    p.add_argument('--catalog', type=Path, help='Catalog root containing software/items and workflow/profiles')
    sub = p.add_subparsers(dest='command', required=True)
    for name, handler in [('list', _list), ('files', _files), ('show', _show), ('edit', _edit), ('write', _write)]:
        s = sub.add_parser(name)
        s.set_defaults(handler=handler)
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
    export.set_defaults(handler=_export)
    choices = export.add_mutually_exclusive_group(required=True)
    choices.add_argument('--profile'); choices.add_argument('--software', nargs='+')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--dry-run', action='store_true')
    export.add_argument('--json', action='store_true', help='Print the snapshot manifest to stdout')
    for name, handler in [('inspect', _inspect), ('apply', _apply)]:
        cmd = snap.add_parser(name)
        cmd.set_defaults(handler=handler)
        cmd.add_argument('--snapshot', required=True, type=Path)
        cmd.add_argument('--json', action='store_true')
        if name == 'apply':
            cmd.add_argument('--expected-version', metavar='VERSION', type=nonempty_version,
                             help='Require the plan version returned by snapshot apply --dry-run')
            cmd.add_argument('--dry-run', action='store_true')
            cmd.add_argument('--detach-hardlink', action='store_true')
    return p


def run(args):
    return args.handler(args)


def _export(args):
    snapshot = api.export_snapshot(args.output, catalog=args.catalog, profile=args.profile,
                                   software=args.software, dry=args.dry_run)
    return snapshot.document() if args.json else None


def _inspect(args):
    return api.verify_archive(args.snapshot).document()


def _apply(args):
    return api.apply_snapshot(args.snapshot, dry=args.dry_run, detach=args.detach_hardlink,
                              expected_version=args.expected_version)


def _list(args):
    return api.Editor(args.catalog).configurations()


def _files(args):
    return api.Editor(args.catalog).files(args.software, args.config)


def _write(args):
    editor = api.Editor(args.catalog)
    raw = sys.stdin.buffer.read() if args.source == '-' else Path(args.source).read_bytes()
    content = decode_candidate(raw)
    return editor.save(args.software, args.config, args.file, content, args.expected_version, detach=args.detach_hardlink)


def _show(args):
    current = api.Editor(args.catalog).read(args.software, args.config, args.file)
    if args.json:
        return current
    if not current['editable']:
        raise api.CoreError(current['reason'])
    sys.stdout.buffer.write(encode_text(current['content'], bom=current['bom'], newline=current['newline']))
    return None


def _edit(args):
    editor = api.Editor(args.catalog)
    current = editor.read(args.software, args.config, args.file)
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
            content = decode_candidate(file.read_bytes())
            return editor.save(args.software, args.config, args.file, content, current['version'], detach=args.detach_hardlink)
        except (api.CoreError, OSError, UnicodeError) as exc:
            retain = True
            failure = api.ConflictError if isinstance(exc, api.ConflictError) else api.CoreError
            raise failure(f'{exc}; editor draft directory retained at {directory.name}') from exc
    finally:
        if not retain:
            directory.cleanup()


def _print_error(exc):
    detail = '; '.join([str(exc), *getattr(exc, '__notes__', ())])
    print(f'error: {detail}', file=sys.stderr)


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
        _print_error(exc)
        return 2
    except api.ConflictError as exc:
        _print_error(exc)
        return 4
    except (api.CoreError, OSError, ValueError) as exc:
        _print_error(exc)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
