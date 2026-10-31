"""Atomic recovery metadata stored locally, with legacy USB journal migration."""
import json
import os
import re
from pathlib import Path


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(data, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def begin(pointer, staging, data):
    write_json(pointer, {'version': 2, 'session': staging.name, 'data': data})


def read(pointer, root):
    saved = json.loads(pointer.read_text(encoding='utf-8'))
    session = saved['session']
    if not isinstance(session, str) or not re.fullmatch('[a-f0-9]{32}', session):
        raise ValueError('Invalid recovery pointer')
    staging = root / '_staging' / session
    if saved.get('version') == 2:
        data = saved['data']
    else:
        legacy = staging / 'session.json'
        if not legacy.is_file():
            legacy = pointer.parent / 'recovery' / 'legacy' / session / 'session.json'
        data = json.loads(legacy.read_text(encoding='utf-8'))
        begin(pointer, staging, data)
        legacy.unlink()
    return staging, data


def finish(pointer, staging):
    # A complete journal is safe to replay if pointer removal is interrupted.
    pointer.unlink(missing_ok=True)
    try:
        (staging / 'session.json').unlink(missing_ok=True)
    except OSError:
        pass


def migrate_legacy_journals(pointer, root):
    for name in ('session.json', 'session.json.tmp'):
        for source in (root / '_staging').glob('*/' + name):
            if not re.fullmatch('[a-f0-9]{32}', source.parent.name):
                continue
            data = source.read_bytes()
            directory = pointer.parent / 'recovery' / 'legacy' / source.parent.name
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / name
            if target.exists() and target.read_bytes() != data:
                raise ValueError('Conflicting legacy recovery metadata')
            if not target.exists():
                with target.open('xb') as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            if target.read_bytes() != data or source.read_bytes() != data:
                raise ValueError('Recovery metadata changed during migration')
            source.unlink()
