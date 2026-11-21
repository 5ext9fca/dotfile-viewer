#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
python="${DOTFILES_PYTHON:-$root/.venv/bin/python}"
for arg in "$@";do
  case "$arg" in --strict) ;; *) printf 'Usage: tests/check.sh [--strict]\n' >&2;exit 2 ;; esac
done
"$python" -c 'import sys; assert sys.version_info[:2] == (3, 14), "Validation requires Python 3.14"'
cd "$root"
"$python" -m unittest discover -s tests/viewer -v
