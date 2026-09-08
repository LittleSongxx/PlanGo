"""Report explicit upstream Planora drift against the preserved source baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'vendor' / 'plango_harness'


def selected(name: str) -> bool:
    path = Path(name)
    return not path.is_absolute() and '..' not in path.parts and (
        (name.startswith('backend/planora/') and path.suffix in {'.py', '.md', '.mako'})
        or name in {'backend/app.py', 'pyproject.toml', 'uv.lock', 'alembic.ini'}
    )


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(source: Path) -> None:
    manifest = json.loads((DEST / 'SNAPSHOT.json').read_text())
    base = manifest['files']
    upstream = set(base)
    upstream.update(str(p.relative_to(source)) for p in (source / 'backend' / 'planora').rglob('*') if p.is_file() and selected(str(p.relative_to(source))))
    changed, deleted, local, conflicts = [], [], [], []
    for name in sorted(upstream):
        src = source / name
        dst = DEST / name.replace('backend/planora/', 'backend/plango_harness/', 1)
        upstream_hash = digest(src.read_bytes()) if src.is_file() else None
        local_hash = digest(dst.read_bytes()) if dst.is_file() else None
        if upstream_hash != base.get(name):
            (changed if upstream_hash else deleted).append(name)
        if name in base and local_hash != base[name]:
            local.append(name)
            if upstream_hash != base[name] and upstream_hash != local_hash:
                conflicts.append(name)
    print(json.dumps({'upstream_changed_or_added': changed, 'upstream_deleted': deleted,
                      'integration_patches': local, 'needs_three_way_review': conflicts}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check'])
    parser.add_argument('--source', type=Path, required=True, help='Explicit optional upstream checkout; never used by the application')
    args = parser.parse_args()
    check(args.source)
