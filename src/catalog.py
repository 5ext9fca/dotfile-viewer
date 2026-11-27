"""The one catalog boundary used by both applications."""
import os
from pathlib import Path
from .install_config import ROOT, DeclarationError
from .software import read_catalog, profile_selection, ID


def catalog_root(directory=None):
    return Path(directory or os.environ.get('DOTFILES_CATALOG') or ROOT).resolve()


def load_catalog(directory=None):
    try:
        return read_catalog(catalog_root(directory) / 'software/items')
    except (KeyError, TypeError, ValueError) as exc:
        raise DeclarationError('Invalid software declaration: ' + str(exc)) from exc


def selection(directory=None, *, profile=None, software=None):
    if bool(profile) == bool(software):
        raise DeclarationError('Specify exactly one profile or software selection')
    if profile:
        if not ID.fullmatch(profile):
            raise DeclarationError('Invalid profile name')
        return profile_selection(catalog_root(directory) / 'workflow/profiles' / (profile + '.conf'))
    return list(software)
