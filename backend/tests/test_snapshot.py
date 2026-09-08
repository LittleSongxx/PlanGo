"""No secrets/escaping paths enter the optional upstream snapshot utility."""
import importlib.util
from pathlib import Path


def test_snapshot_allowlist():
    script = Path(__file__).resolve().parents[2] / 'scripts/sync_planora.py'
    spec = importlib.util.spec_from_file_location('sync_planora', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in ['.env', '.env.example', '../.env', '/backend/planora/runtime.py',
                 'backend/planora/../../.env', 'eval/results/private.json', 'backend/tests/credentials.key']:
        assert not module.selected(path), path
    assert module.selected('backend/planora/runtime.py')
    assert module.selected('backend/planora/persistence/migrations/versions/0001_planora_base.py')
    assert module.selected('uv.lock')
