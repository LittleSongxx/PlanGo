"""One-time, lossless migration of this checkout's configuration keys."""

import os
import re
import shutil
from pathlib import Path

_ASSIGNMENT = re.compile(r'^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$')


def _target(key: str) -> str:
    if key.startswith(('YOYU_', 'XIAONIAN_')):
        return 'PLANGO_' + key.split('_', 1)[1]
    if key in ('LLM_PROVIDER', 'DATA_SOURCE'):
        return 'PLANGO_' + key
    return key


def _value(raw: str) -> str:
    value = raw.strip()
    if value.startswith(('"', "'")):
        quote = value[0]
        match = re.fullmatch(quote + r'((?:\\.|[^' + quote + r'])*)' + quote + r'[ \t]*(?:#.*)?', value)
        if not match:
            raise ValueError('Malformed quoted configuration value; file was not changed')
        escapes = {'\\': '\\', "'": "'"} if quote == "'" else {
            '\\': '\\', '"': '"', "'": "'", 'a': '\a', 'b': '\b', 'f': '\f',
            'n': '\n', 'r': '\r', 't': '\t', 'v': '\v',
        }
        return re.sub(r'\\(.)', lambda m: escapes.get(m[1], m[0]), match[1])
    return re.sub(r'[ \t]+#.*$', '', value).rstrip()


def parse_config(text: str) -> dict[str, str]:
    """Read single-line env assignments without expanding variables or executing code.

    Equal duplicates are harmless; conflicting duplicate keys or legacy aliases
    are rejected before callers can accidentally replace an existing credential.
    """
    names: dict[str, str] = {}
    canonical: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = _ASSIGNMENT.fullmatch(line)
        if not match:
            raise ValueError('Unsupported configuration line; file was not changed')
        key, raw = match.groups()
        value, target = _value(raw), _target(key)
        if target in canonical and canonical[target] != value:
            raise RuntimeError(f'Conflicting configuration keys for {target}; values were not changed')
        names[key] = value
        canonical[target] = value
    return names


def migrate_config(path: Path) -> bool:
    if not path.exists():
        return False
    original = path.read_text()
    parse_config(original)  # Validate all aliases/duplicates before backups or writes.
    migrated, emitted = [], set()
    for line in original.splitlines(keepends=True):
        match = _ASSIGNMENT.fullmatch(line.rstrip('\r\n'))
        if match:
            key, raw = match.groups()
            target = _target(key)
            if target in emitted:
                continue
            emitted.add(target)
            if target != key or target.startswith('PLANGO_'):
                newline = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
                line = target + '=' + raw.lstrip() + newline
        migrated.append(line)
    result = ''.join(migrated)
    if result == original:
        return False
    backup = path.with_name(path.name + '.before-plango')
    if not backup.exists():
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    temporary = path.with_name(path.name + '.migrating')
    with temporary.open('x') as file:
        os.chmod(temporary, path.stat().st_mode & 0o777)
        file.write(result)
    os.replace(temporary, path)
    return True


if __name__ == '__main__':
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / '.env'
        original = (' export YOYU_BACKEND_TOKEN = "unchanged=fixture" # retained comment\n'
                    "PLANGO_CITY = '重庆'\nXIAONIAN_CITY=重庆\nOPENAI_API_KEY=fixture\n")
        path.write_text(original)
        path.chmod(0o600)
        assert migrate_config(path)
        assert parse_config(path.read_text()) == {
            'PLANGO_BACKEND_TOKEN': 'unchanged=fixture', 'PLANGO_CITY': '重庆',
            'OPENAI_API_KEY': 'fixture',
        }
        assert path.with_name('.env.before-plango').read_text() == original
        assert path.stat().st_mode & 0o777 == 0o600
        assert not migrate_config(path)
        for conflict in (
            'YOYU_BACKEND_TOKEN=old\nPLANGO_BACKEND_TOKEN=new\n',
            'YOYU_CITY=甲\nXIAONIAN_CITY=乙\n',
            'YOYU_BACKEND_TOKEN=one\nYOYU_BACKEND_TOKEN=two\n',
            'PLANGO_BACKEND_TOKEN=one\nPLANGO_BACKEND_TOKEN=two\n',
        ):
            path.write_text(conflict)
            try:
                migrate_config(path)
            except RuntimeError:
                assert path.read_text() == conflict
            else:
                raise AssertionError('Conflicting credentials must not be overwritten')
        assert parse_config('PLANGO_BACKEND_TOKEN=unchanged=a=b\n')['PLANGO_BACKEND_TOKEN'] == 'unchanged=a=b'
        assert parse_config('export OPENAI_BASE_URL="https://fixture.invalid/path#fragment"\n')['OPENAI_BASE_URL'].endswith('#fragment')
        assert parse_config("PLANGO_CITY=重庆 # comment\n")['PLANGO_CITY'] == '重庆'
    print('Configuration migration: whitespace/export/quotes, aliases, duplicates, equality, permissions and idempotence passed')
