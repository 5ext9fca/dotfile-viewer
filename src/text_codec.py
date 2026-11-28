"""UTF-8 and newline handling for editable configuration files."""
from codecs import BOM_UTF8
from .errors import CoreError


def decode_text(data):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        return dict(editable=False, reason='Not UTF-8')
    if '\x00' in text:
        return dict(editable=False, reason='Binary or mixed newline content')
    newline = 'lf'
    if '\r' in text:
        pairs = text.count('\r\n')
        if pairs != text.count('\r') or pairs != text.count('\n'):
            return dict(editable=False, reason='Binary or mixed newline content')
        text = text.replace('\r\n', '\n')
        newline = 'crlf'
    return dict(editable=True, content=text, bom=data.startswith(BOM_UTF8), newline=newline)


def normalize_candidate(content):
    content = content.replace('\r\n', '\n')
    if '\r' in content or '\x00' in content:
        raise CoreError('Invalid text content')
    return content


def encode_text(content, *, bom, newline):
    if newline == 'crlf':
        content = content.replace('\n', '\r\n')
    return content.encode('utf-8-sig' if bom else 'utf-8')
