"""One cooperative lease implementation for Python, Bash and PowerShell."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import socket
import tempfile
import uuid
from .errors import CoreError


def default_path():
    if os.name == 'nt':
        return Path(tempfile.gettempdir()) / 'dotfiles-deployment.lock'
    return Path(tempfile.gettempdir()) / f'dotfiles-deployment-{os.getuid()}.lock'


def acquire(path=None, token=None, owner_pid=None):
    path = Path(path or os.environ.get('DOTFILES_DEPLOYMENT_LOCK') or default_path()).absolute()
    token = token or os.environ.get('DOTFILES_DEPLOYMENT_TOKEN')
    if path.is_symlink():
        raise CoreError('Deployment lease must not be a link')
    owner_path = path / 'owner'
    if token and owner_path.is_file() and not owner_path.is_symlink():
        if owner_path.read_text().strip().split('|')[-1] == token:
            return dict(path=str(path), token=token, owned=False)
    token = uuid.uuid4().hex
    owner = f'{owner_pid or os.getpid()}|{socket.gethostname()}|{token}'
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise CoreError(f'Deployment lock exists: {path}; inspect related writers, never reclaim automatically') from exc
    try:
        owner_path.write_text(owner, encoding='utf-8')
    except BaseException:
        # No action has started; a failed owner write is safe to clean here.
        owner_path.unlink(missing_ok=True)
        path.rmdir()
        raise
    return dict(path=str(path), token=token, owned=True)


def release(path, token):
    path = Path(path)
    owner = path / 'owner'
    if not path.is_symlink() and not owner.is_symlink() and owner.is_file() and owner.read_text().strip().split('|')[-1] == token:
        owner.unlink()
        path.rmdir()


@contextmanager
def deployment_lease(*, path=None, token=None, dry=False):
    if dry:
        yield None
        return
    lease = acquire(path, token)
    try:
        yield dict(path=lease['path'], token=lease['token'])
    finally:
        if lease['owned']:
            release(lease['path'], lease['token'])


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['acquire', 'release'])
    parser.add_argument('--owner-pid', type=int)
    parser.add_argument('--path'); parser.add_argument('--token'); parser.add_argument('--tsv', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'release':
            if not args.path or not args.token:
                parser.error('release requires path and token')
            release(args.path, args.token)
        else:
            result = acquire(args.path, args.token, args.owner_pid)
            print('\t'.join([result['path'], result['token'], str(int(result['owned']))]) if args.tsv else json.dumps(result))
        return 0
    except (CoreError, OSError) as exc:
        import sys
        print('error: ' + str(exc), file=sys.stderr)
        return 73


if __name__ == '__main__':
    raise SystemExit(main())
