"""Small controlled checks for trial archives, secret-free diagnostics and lossless failed upgrades."""

import contextlib
import io
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

import lifecycle
import trial


def rejected(action):
    try:
        action()
    except (RuntimeError, OSError, ValueError, tarfile.TarError):
        return
    raise AssertionError("Unsafe operation was accepted")


def main():
    with tempfile.TemporaryDirectory(prefix="plango trial check ") as temporary:
        base = Path(temporary)
        root = base / "installed"
        root.mkdir()
        for name in trial.PAYLOAD:
            (root / name).write_text("old " + name)
        (root / ".env").write_text("PLANGO_BACKEND_TOKEN=fixture-never-print\nPLANGO_POSTGRES_PASSWORD=fixture-password\n")
        (root / ".env").chmod(0o600)
        (root / "trial-install.json").write_text(json.dumps({"project": "plango-trial-check", "profile": str(root / "profile")}))
        (root / "profile/harness").mkdir(parents=True)
        receipt = root / "profile/harness/browser-receipts.json"
        receipt.write_text('{"fixture":{"status":"UNKNOWN","idempotency_key":"keep"}}')
        initial = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        archive = base / "release.tar"
        with tarfile.open(archive, "w") as tar:
            for name in trial.PAYLOAD:
                member = tarfile.TarInfo(name)
                data = ("new " + name).encode()
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        bad = base / "bad.tar"
        for name in ("../escaped", "/tmp/escaped", ".env", "profile/Cookies"):
            with tarfile.open(bad, "w") as tar:
                tar.addfile(tarfile.TarInfo(name))
            rejected(lambda: trial.extract(bad, base / "unpacked"))
        rejected(lambda: trial.release(root))
        rejected(lambda: trial.project_name("planora"))
        with patch.object(trial, "containers", return_value=[{"State": "running"}]):
            rejected(lambda: trial.install(archive, base / "conflict", "plango-trial-check", 28011))

        def backup(_, destination):
            destination.mkdir()

        release = {"platform": "linux-x64", "electron": "33.4.11"}
        rename = os.rename
        with patch.object(trial, "backup", backup), patch.object(trial, "release", return_value=release):
            # A failure halfway through the swap must restore the exact previous application.
            def fail_swap(source, target):
                if Path(source).name == "backend" and Path(source).parent.name.startswith(".plango-upgrade-"):
                    raise OSError("controlled swap failure")
                return rename(source, target)

            with patch.object(trial.os, "rename", fail_swap):
                rejected(lambda: trial.upgrade(root, archive, base / "backup-failure"))
            assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()} == initial
            # A second failure during rollback must retain all displaced originals outside the temporary stage.
            def fail_rollback(source, target):
                if Path(source).name == "backend" and Path(source).parent.name.startswith(".plango-upgrade-"):
                    raise OSError("controlled swap failure")
                if Path(source) == root / "electron" and Path(target).parent.name.startswith(".plango-upgrade-"):
                    raise OSError("controlled rollback failure")
                return rename(source, target)

            with patch.object(trial.os, "rename", fail_rollback):
                rejected(lambda: trial.upgrade(root, archive, base / "backup-double-failure"))
            previous, = base.glob(".plango-previous-*")
            assert (previous / "electron").read_text() == "old electron"
            assert receipt.read_bytes() == initial["profile/harness/browser-receipts.json"]
            with patch.object(lifecycle, "ROOT", root):
                rejected(lifecycle.start)
            shutil.copy2(previous / "electron", root / "electron")
            rejected(lambda: trial.upgrade(root, archive, base / "must-not-upgrade"))
            (root / "trial-upgrade-in-progress.json").unlink()
            trial.upgrade(root, archive, base / "backup-success")
            assert (root / "out").read_text() == "new out"
            assert (base / "backup-success/application/out").read_text() == "old out"
            for name in (".env", "trial-install.json", "profile/harness/browser-receipts.json"):
                assert (root / name).read_bytes() == initial[name]

        # Diagnostic checks never print credential values, even when other preconditions fail.
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(trial.shutil, "which", return_value=None):
            rejected(lambda: trial.doctor(root))
        assert "fixture-never-print" not in output.getvalue() and "fixture-password" not in output.getvalue()
        assert json.loads(output.getvalue())["checks"]["private_config"]
        with (root / ".env").open("a") as env:
            env.write("OPENAI_API_KEY=fixture-never-print\nOPENAI_MODEL=fixture\nOPENAI_BASE_URL=http://user:fixture-never-print@invalid／host\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(trial.shutil, "which", return_value=None):
            rejected(lambda: trial.doctor(root))
        assert "fixture-never-print" not in output.getvalue()
        assert not json.loads(output.getvalue())["features"]["backend_model_configured"]
        executable = str(root / "electron/electron")
        profile = str(root / "profile")
        with patch.object(lifecycle, "ELECTRON", Path(executable)), patch.object(lifecycle, "INSTALL", {"profile": profile}):
            assert lifecycle.is_electron({"args": [executable, ".", "--user-data-dir=" + profile]})
            assert lifecycle.is_electron({"args": [executable + " . --user-data-dir=" + profile]})
            assert lifecycle.is_electron({"args": [executable + " --user-data-dir=" + profile + " ."]})
            assert not lifecycle.is_electron({"args": [executable + " . --user-data-dir=" + profile + "-other"]})
            assert not lifecycle.is_electron({"args": [executable, "."]})
        print("Trial controlled checks passed: paths, ownership, private diagnostics, upgrade rollback/double failure and UNKNOWN preservation.")


if __name__ == "__main__":
    main()
