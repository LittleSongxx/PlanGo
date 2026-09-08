"""Verify a copied YOYU backend starts outside both original project directories."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="yoyu-independent-") as directory:
    target = Path(directory)
    for name in ["pyproject.toml", "uv.lock", "README.md"]:
        shutil.copy2(root / name, target / name)
    for name in ["backend", "vendor/planora"]:
        shutil.copytree(
            root / name,
            target / name,
            ignore=shutil.ignore_patterns("__pycache__", ".venv", "*.pyc"),
        )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("PLANORA_", "YOYU_", "OPENAI_", "AMAP_", "EMBEDDING_", "LONGCAT_", "MINIMAX_")
        )
        and key not in {"DATABASE_URL", "REDIS_URL", "PYTHONPATH"}
    }
    # Use the selected conda interpreter; Docker separately checks a clean environment from our lock.
    env.update(
        PYTHONPATH=os.pathsep.join(
            [str(target / "backend"), str(target / "vendor/planora/backend")]
        ),
        YOYU_DATA_DIR=str(target / "owned-data"),
        PLANORA_RUNTIME_PROFILE="service",
        DATABASE_URL="postgresql://unrelated-upstream/forbidden",
        REDIS_URL="redis://unrelated-upstream:6379",
    )
    executable = sys.executable
    probe = """
from pathlib import Path
from fastapi.testclient import TestClient
import planora
import yoyu
from yoyu.settings import settings_from_env
from yoyu.app import create_app
root = Path.cwd()
assert Path(planora.__file__).resolve().is_relative_to(root / 'vendor/planora')
assert Path(yoyu.__file__).resolve().is_relative_to(root / 'backend/yoyu')
s = settings_from_env()
assert s.runtime_profile == 'desktop'
assert s.world_provider == 'browser'
assert str(root / 'owned-data') in s.database_url
assert not s.model_enabled
with TestClient(create_app(s, token='independent-fixture-only')) as client:
    headers = {'Authorization': 'Bearer independent-fixture-only'}
    assert client.get('/api/v1/health/ready', headers=headers).json()['ready']
    assert client.get('/api/v1/runs', headers=headers).json()['runs'] == []
    assert client.get('/api/v1/reminders', headers=headers).json()['reminders'] == []
print('Copied YOYU code passed independent startup using the selected conda Python and its own data')
"""
    result = subprocess.run(
        [str(executable), "-c", probe],
        cwd=target,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    print(result.stdout.strip())
