"""Pin public Planora source and report upstream drift without changing its workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'vendor' / 'planora'


def selected(name: str) -> bool:
    path = Path(name)
    return not path.is_absolute() and '..' not in path.parts and (
        (name.startswith('backend/planora/') and path.suffix in {'.py', '.md', '.mako'})
        or name in {'backend/app.py', 'pyproject.toml', 'uv.lock', 'alembic.ini'}
    )


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(archive: Path, source: Path) -> None:
    if (DEST / 'SNAPSHOT.json').exists():
        raise SystemExit('Snapshot already exists; use check to review upstream changes.')
    with tarfile.open(archive, 'r:gz') as tar:
        blobs = {}
        for member in tar.getmembers():
            if member.isfile() and selected(member.name):
                stream = tar.extractfile(member)
                assert stream is not None
                blobs[member.name] = stream.read()
    if 'backend/planora/runtime.py' not in blobs:
        raise SystemExit('Archive has no Planora runtime.')
    DEST.mkdir(parents=True, exist_ok=True)
    for name, data in blobs.items():
        target = DEST / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    # Keep an exact base for a later reviewed three-way merge, including local patches.
    with tarfile.open(DEST / 'upstream-base.tar.gz', 'w:gz') as tar:
        for name in sorted(blobs):
            tar.add(DEST / name, arcname=name)
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    manifest = {
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'source_repository': str(source.resolve()),
        'source_git_head': head,
        'source_archive': str(archive.resolve()),
        'source_archive_sha256': digest(archive.read_bytes()),
        'note': 'Frozen public archive, includes uncommitted source; never a live sibling import.',
        'files': {name: digest(data) for name, data in sorted(blobs.items())},
    }
    (DEST / 'SNAPSHOT.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'snapshot_files': len(blobs), 'source_archive_sha256': manifest['source_archive_sha256']}))


def check(source: Path) -> None:
    manifest = json.loads((DEST / 'SNAPSHOT.json').read_text())
    base = manifest['files']
    upstream = set(base)
    upstream.update(str(p.relative_to(source)) for p in (source / 'backend' / 'planora').rglob('*') if p.is_file() and selected(str(p.relative_to(source))))
    changed, deleted, local, conflicts = [], [], [], []
    for name in sorted(upstream):
        src, dst = source / name, DEST / name
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
    parser.add_argument('command', choices=['snapshot', 'check'])
    parser.add_argument('--source', type=Path, required=True, help='Explicit optional upstream checkout; never used by the application')
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.command == 'snapshot':
        if args.archive is None:
            parser.error('snapshot requires --archive (public frozen source archive)')
        snapshot(args.archive, args.source)
    else:
        check(args.source)
