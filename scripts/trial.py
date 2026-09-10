"""Build/install Linux trials; cold backups preserve SQL, Redis, receipts and the browser profile."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from migrate_config import parse_config

ROOT = Path(__file__).resolve().parents[1]
VOLUMES = ("postgres-data", "redis-data", "runtime-data")
PAYLOAD = ("out", "electron", "node_modules", "backend", "vendor", "skills", "deploy", "scripts",
           "package.json", "package-lock.json", "pyproject.toml", "uv.lock", "alembic.ini", "README.md",
           "docker-compose.yml", ".dockerignore", ".env.example", "start.sh", "stop.sh", "docs", "trial-release.json", "THIRD_PARTY_NOTICES.txt")


def call(command, **kwargs):
    result = subprocess.run(command, capture_output=True, **kwargs)
    if result.returncode:
        # Docker/Compose and provider errors can echo credential-bearing configuration.
        raise RuntimeError(f"{command[0]} operation failed (exit {result.returncode}); no configuration was printed")
    return result.stdout


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def metadata(root):
    path = root / "trial-install.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"project": "plango", "profile": str(Path.home() / ".config/plango")}


def project_name(value):
    if not re.fullmatch(r"plango(?:-[a-z0-9][a-z0-9-]{0,40})?", value):
        raise ValueError("Project must be plango or plango-<lowercase trial name>")
    return value


def containers(project):
    data = call(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                 "--format", '{{json .}}'], text=True)
    return [json.loads(line) for line in data.splitlines()]


def volume_names(project):
    result = []
    for suffix in VOLUMES:
        name = f"{project}_{suffix}"
        info = subprocess.run(["docker", "volume", "inspect", name, "--format", '{{json .Labels}}'], capture_output=True, text=True)
        if info.returncode:
            raise RuntimeError(f"Required volume is missing: {name}; no empty replacement was made")
        labels = json.loads(info.stdout) or {}
        if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.volume") != suffix:
            raise RuntimeError(f"Volume ownership is not confirmed: {name}")
        if call(["docker", "ps", "-q", "--filter", f"volume={name}"], text=True).strip():
            raise RuntimeError(f"Volume is in use: {name}; stop the owned services before maintenance")
        result.append(name)
    return result


def cold(root, config):
    spec = importlib.util.spec_from_file_location("plango_trial_lifecycle", root / "scripts/lifecycle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RUN.mkdir(parents=True, exist_ok=True)
    module.check_compose_owner()
    if module.desktop_processes():
        raise RuntimeError("The owned desktop must be closed before maintenance")
    for item in containers(config["project"]):
        if item["State"] != "exited":
            raise RuntimeError("The owned Docker services must be stopped before maintenance")
    profile = Path(config["profile"])
    lock = profile / "SingletonLock"
    if lock.is_symlink():
        host, _, pid = os.readlink(lock).rpartition("-")
        if host != platform.node() or not pid.isdigit() or Path(f"/proc/{pid}").exists():
            raise RuntimeError("The browser profile has an active or unverified owner")


def backup(root, destination):
    config = metadata(root)
    if destination.is_relative_to(Path(config["profile"]).resolve()):
        raise RuntimeError("Backup must be outside the browser profile")
    project_name(config["project"])
    cold(root, config)
    volumes = volume_names(config["project"])
    if destination.exists():
        raise RuntimeError("Backup destination already exists; prior evidence was preserved")
    source_release = release(root) if (root / "trial-release.json").exists() else {}
    destination.mkdir(parents=True, mode=0o700)
    try:
        for name, suffix in zip(volumes, VOLUMES):
            with (destination / f"{suffix}.tar").open("wb") as file:
                result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                    "--mount", f"type=volume,src={name},dst=/source,readonly", "redis:7-alpine",
                    "tar", "-C", "/source", "-cpf", "-", "."], stdout=file, stderr=subprocess.DEVNULL)
            if result.returncode:
                raise RuntimeError("Cold volume backup failed; incomplete directory retained")
        profile = Path(config["profile"])
        # Tar preserves Chromium's symlinks and every receipt; it does not dereference external targets.
        with tarfile.open(destination / "profile.tar", "w") as archive:
            if profile.exists():
                archive.add(profile, arcname="profile")
        shutil.copy2(root / ".env", destination / ".env")
        (destination / ".env").chmod(0o600)
        manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(), "source": str(root),
                    **config, "postgres_major": 16, "redis_major": 7,
                    "electron": source_release.get("electron"), "platform": source_release.get("platform"),
                    "source_version_unknown": not bool(source_release.get("electron")),
                    "files": {p.name: digest(p) for p in destination.iterdir() if p.is_file()}}
        write_json(destination / "backup.json", manifest)
    except BaseException:
        print("Incomplete backup retained; backup.json is written only after all files succeed", file=sys.stderr)
        raise
    print(f"Cold backup saved: {destination}")
    return manifest


def verify_backup(directory):
    manifest = json.loads((directory / "backup.json").read_text())
    expected = {"profile.tar", ".env", *(f"{name}.tar" for name in VOLUMES)}
    if manifest.get("format") != 1 or set(manifest["files"]) != expected:
        raise RuntimeError("Unsupported or incomplete backup manifest")
    for name, checksum in manifest["files"].items():
        if digest(directory / name) != checksum:
            raise RuntimeError(f"Backup checksum mismatch: {name}")
    return manifest


def extract(archive_path, destination, profile=False):
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if not parts or Path(member.name).is_absolute() or ".." in parts or member.isdev() or member.isfifo():
                raise RuntimeError("Archive contains an unsafe path or special file")
            if profile:
                if parts[0] != "profile":
                    raise RuntimeError("Profile archive contains an unexpected root")
                if member.issym() and member.name in ("profile/SingletonLock", "profile/SingletonCookie", "profile/SingletonSocket"):
                    continue  # Stale process locks are excluded; browser data and receipts remain untouched.
            elif parts[0] not in PAYLOAD:
                raise RuntimeError("Release contains a non-payload path")
            archive.extract(member, destination, filter="data")


def release(root):
    value = json.loads((root / "trial-release.json").read_text())
    required = ("electron/electron", "electron/version", "out/main/index.js", "out/preload/index.js", "out/renderer/index.html",
                "backend/plango/app.py", "vendor/plango_harness/SNAPSHOT.json", "vendor/plango_harness/upstream-base.tar.gz",
                "scripts/lifecycle.py", "scripts/trial.py", "scripts/migrate_config.py", "deploy/Dockerfile",
                "package.json", "docker-compose.yml", ".env.example", "THIRD_PARTY_NOTICES.txt")
    if value.get("format") != 1 or any(not (root / name).is_file() for name in required):
        raise RuntimeError("Incomplete or unsupported release; the existing application was preserved")
    if (root / "electron/version").read_text().strip().removeprefix("v") != value.get("electron"):
        raise RuntimeError("Release Electron version does not match its binary directory")
    return value


def check_electron_transition(old, new, allow=False, allow_unknown=False):
    if old.get("platform") and old["platform"] != new.get("platform"):
        raise RuntimeError("Platform changes are not supported")

    def version(value):
        if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
            raise RuntimeError("A stable Electron version is required")
        return tuple(map(int, value.split(".")))

    target = version(new.get("electron"))
    if not old.get("electron"):
        if allow and allow_unknown:
            return True  # Only an independent restore may accept explicitly unknown source versions.
        raise RuntimeError("Source Electron version is unknown; preserve the backup and use an independent restore with --allow-electron-upgrade")
    source = version(old["electron"])
    if target < source:
        raise RuntimeError("Electron downgrade is not supported; preserve the original backup")
    if target != source and not allow:
        raise RuntimeError("Electron upgrade requires browser compatibility validation and --allow-electron-upgrade")
    return False


def build(destination):
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("This trial currently supports Linux x86_64 / WSLg only")
    if destination.exists():
        raise RuntimeError("Release already exists; choose a new output path")
    from lifecycle import prepare_node

    prepare_node()
    subprocess.run(["npm", "run", "build"], cwd=ROOT, check=True)
    modules = call(["npm", "ls", "--omit=dev", "--all", "--parseable"], cwd=ROOT, text=True).splitlines()[1:]
    with tempfile.TemporaryDirectory(prefix="plango-release-") as temporary:
        stage = Path(temporary)
        paths = ["out", "backend/plango", "vendor/plango_harness/backend/plango_harness", "vendor/plango_harness/SNAPSHOT.json", "vendor/plango_harness/upstream-base.tar.gz",
                 "skills", "deploy", "package.json", "package-lock.json", "pyproject.toml", "uv.lock", "alembic.ini", "README.md",
                 "docker-compose.yml", ".dockerignore", ".env.example", "start.sh", "stop.sh", "docs/试用安装.md", "docs/独立试用验收.md"]
        paths += [f"scripts/{name}" for name in ("trial.py", "lifecycle.py", "migrate_config.py")]
        for relative in paths:
            source, target = ROOT / relative, stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".env.*"))
            else:
                shutil.copy2(source, target)
        shutil.copytree(ROOT / "node_modules/electron/dist", stage / "electron")
        for module in sorted(modules, key=len):
            source = Path(module)
            target = stage / source.relative_to(ROOT)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, symlinks=True)
        package = json.loads((stage / "package.json").read_text())
        package.pop("devDependencies", None)
        package["scripts"] = {"start": "./start.sh"}
        write_json(stage / "package.json", package)
        notices = [f"PlanGo\nAuthor: {package['author']}\nDeclared license: {package['license']}\n",
                   "Fixed Harness source and provenance: vendor/plango_harness/SNAPSHOT.json and upstream-base.tar.gz.\n"]
        # Include notices for compiled renderer dependencies as well as shipped runtime modules.
        for module in call(["npm", "ls", "--all", "--parseable"], cwd=ROOT, text=True).splitlines()[1:]:
            source = Path(module)
            info = json.loads((source / "package.json").read_text())
            notices.append(f"\n--- {info.get('name')} {info.get('version')} ({info.get('license', 'see package notices')}) ---\n")
            for path in sorted(source.iterdir()):
                if path.is_file() and path.name.lower().startswith(("license", "licence", "notice", "copying")):
                    notices.append(path.read_text(errors="replace") + "\n")
        (stage / "THIRD_PARTY_NOTICES.txt").write_text("".join(notices))
        revision = call(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        source_files = [path for path in stage.rglob("*") if path.is_file()]
        content_hash = hashlib.sha256("\n".join(f"{path.relative_to(stage)}:{digest(path)}" for path in sorted(source_files)).encode()).hexdigest()
        dirty = bool(call(["git", "status", "--porcelain", "--", "src", "backend", "vendor", "scripts", "skills", "deploy",
                           "package.json", "package-lock.json", "pyproject.toml", "uv.lock", "docker-compose.yml"], cwd=ROOT, text=True).strip())
        manifest = {"format": 1, "version": package["version"], "revision": revision,
                    "source_dirty": dirty, "implementation_sha256": content_hash, "platform": "linux-x64",
                    "electron": (stage / "electron/version").read_text().strip(),
                    "created_at": datetime.now(timezone.utc).isoformat()}
        write_json(stage / "trial-release.json", manifest)
        compose = stage / "docker-compose.yml"
        compose.write_text(compose.read_text().replace("image: plango-harness:local", f"image: plango-trial-harness:{package['version']}-{content_hash[:16]}"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, "w:gz") as archive:
            for path in sorted(stage.iterdir()):
                archive.add(path, arcname=path.name)
    destination.with_suffix(destination.suffix + ".sha256").write_text(f"{digest(destination)}  {destination.name}\n")
    print(f"Linux trial saved: {destination}")


def install(archive, target, project, port, reuse=None, allow_electron_upgrade=False):
    project_name(project)
    if target.exists():
        raise RuntimeError("Install target already exists; use upgrade for an installed trial")
    if containers(project):
        raise RuntimeError("Project containers already exist; preserve data and close the old deployment first")
    old = verify_backup(reuse) if reuse else None
    if project == "plango" and not old:
        raise RuntimeError("The main project requires --reuse-backup; a fresh trial must have its own project name")
    if old:
        if old["project"] != project:
            raise RuntimeError("Reuse must keep the exact existing project; use restore for an independent copy")
        volume_names(project)
    else:
        existing = call(["docker", "volume", "ls", "--format", "{{.Name}}"], text=True).splitlines()
        if any(f"{project}_{name}" in existing for name in VOLUMES):
            raise RuntimeError("Existing volumes require --reuse-backup; no new identity was created")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".plango-install-", dir=target.parent) as temporary:
        stage = Path(temporary) / "app"
        stage.mkdir()
        extract(archive, stage)
        info = release(stage)
        if info.get("platform") != "linux-x64" or platform.machine() != "x86_64":
            raise RuntimeError("Unsupported release platform")
        if old:
            check_electron_transition(old, info, allow_electron_upgrade)
        profile = old["profile"] if old else str(target / "profile")
        if old:
            shutil.copy2(reuse / ".env", stage / ".env")
        else:
            env = (stage / ".env.example").read_text()
            env = env.replace("PLANGO_BACKEND_URL=http://127.0.0.1:8011", f"PLANGO_BACKEND_URL=http://127.0.0.1:{port}")
            env += f"\nPLANGO_BACKEND_TOKEN={secrets.token_hex(32)}\nPLANGO_POSTGRES_PASSWORD={secrets.token_hex(32)}\nPLANGO_SERVICE_PORT={port}\n"
            (stage / ".env").write_text(env)
        (stage / ".env").chmod(0o600)
        write_json(stage / "trial-install.json", {"format": 1, "project": project, "profile": profile})
        if old:
            # Restored .env preserves passwords and service port. The original profile is never copied or moved.
            lock = Path(profile) / "SingletonLock"
            if lock.is_symlink():
                host, _, pid = os.readlink(lock).rpartition("-")
                if host != platform.node() or not pid.isdigit() or Path(f"/proc/{pid}").exists():
                    raise RuntimeError("Existing profile ownership is unverified; installation refused")
        os.rename(stage, target)
    print(f"Installed: {target}; edit its private .env, then run scripts/trial.py doctor and ./start.sh")


def doctor(root):
    values = parse_config((root / ".env").read_text())
    checks = {"linux_x64": sys.platform == "linux" and platform.machine() == "x86_64",
              "python_3_12": sys.version_info >= (3, 12),
              "docker": shutil.which("docker") is not None,
              "display": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
              "electron": (root / "electron/electron").is_file(),
              "renderer_build": (root / "out/renderer/index.html").is_file(),
              "private_config": (root / ".env").stat().st_mode & 0o077 == 0}
    for key in ("PLANGO_BACKEND_TOKEN", "PLANGO_POSTGRES_PASSWORD"):
        checks[key] = bool(values.get(key))
    if checks["docker"]:
        for name, command in (("docker_engine", ["docker", "info"]), ("compose_v2", ["docker", "compose", "version"])):
            checks[name] = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if checks["electron"]:
        result = subprocess.run(["ldd", str(root / "electron/electron")], capture_output=True, text=True)
        checks["electron_libraries"] = result.returncode == 0 and "not found" not in result.stdout
    checks["postgres_password_url_safe"] = bool(re.fullmatch(r"[A-Za-z0-9_-]+", values.get("PLANGO_POSTGRES_PASSWORD", "")))
    checks["port"] = values.get("PLANGO_SERVICE_PORT", "8011").isdigit() and 1024 <= int(values.get("PLANGO_SERVICE_PORT", "8011")) <= 65535
    checks["no_legacy_config"] = not any(key.startswith(("YOYU_", "XIAONIAN_")) for key in values)
    config = metadata(root)
    project_name(config["project"])
    checks["profile_identity"] = not (Path(config["profile"]) / "harness/browser-receipts.json").exists() or (Path(config["profile"]) / "harness/desktop-identity.json").is_file()
    model = bool(values.get("OPENAI_API_KEY") and values.get("OPENAI_MODEL"))
    try:
        endpoint = urlsplit(values.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
        model = model and endpoint.scheme in ("http", "https") and bool(endpoint.netloc) and not endpoint.username
    except ValueError:
        model = False  # URL parser errors can include the original credential-bearing netloc.
    print(json.dumps({"checks": checks, "features": {"backend_model_configured": model,
        "amap_search_configured": bool(values.get("AMAP_WEBSERVICE_KEY")),
        "amap_map_configured": bool(values.get("AMAP_JS_KEY") and values.get("AMAP_JS_SECURITY"))},
        "note": "Presence/format only; no model/geocoding call was made. Docker model configuration comes from .env."}, indent=2))
    if not all(checks.values()):
        raise RuntimeError("Configuration diagnostics failed; only boolean results were printed")


def restore(root, directory, allow_electron_upgrade=False):
    config = metadata(root)
    cold(root, config)
    manifest = verify_backup(directory)
    if manifest["postgres_major"] != 16 or manifest["redis_major"] != 7:
        raise RuntimeError("Storage major versions differ; restore using the original release")
    source_version_unknown = check_electron_transition(manifest, release(root), allow_electron_upgrade, allow_unknown=True)
    project = project_name(config["project"])
    if project == manifest["project"]:
        raise RuntimeError("Restore requires an independent destination project; original data is preserved")
    if containers(project):
        raise RuntimeError("Restore target must not have containers")
    existing = call(["docker", "volume", "ls", "--format", "{{.Name}}"], text=True).splitlines()
    if any(f"{project}_{name}" in existing for name in VOLUMES):
        raise RuntimeError("Restore target has volumes; no nonempty data will be overwritten")
    profile = Path(config["profile"])
    if profile.exists():
        raise RuntimeError("Restore target profile exists; no browser identity will be replaced")
    # Validate profile archive before creating any durable target resource.
    with tempfile.TemporaryDirectory(prefix=".plango-restore-", dir=root) as temporary:
        stage = Path(temporary)
        extract(directory / "profile.tar", stage, profile=True)
        for suffix in VOLUMES:
            name = f"{project}_{suffix}"
            call(["docker", "volume", "create", "--label", f"com.docker.compose.project={project}",
                  "--label", f"com.docker.compose.volume={suffix}", name])
            with (directory / f"{suffix}.tar").open("rb") as file:
                result = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--read-only",
                    "--mount", f"type=volume,src={name},dst=/target", "redis:7-alpine", "tar", "-C", "/target", "-xpf", "-"],
                    stdin=file, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode:
                raise RuntimeError("Restore failed; partial target retained for inspection, original backup is intact")
        # Keep original backend/password credentials; only the independent installation's port differs.
        current = parse_config((root / ".env").read_text())
        env = (directory / ".env").read_text()
        env = re.sub(r"(?m)^\s*(?:export\s+)?PLANGO_(?:SERVICE_PORT|BACKEND_URL)\s*=.*\n?", "", env)
        port = current.get("PLANGO_SERVICE_PORT", "8011")
        env += f"\nPLANGO_SERVICE_PORT={port}\nPLANGO_BACKEND_URL=http://127.0.0.1:{port}\n"
        (root / ".env").write_text(env)
        (root / ".env").chmod(0o600)
        if (stage / "profile").exists():
            os.rename(stage / "profile", profile)
    print(f"Restored all three cold volumes and the same browser identity/receipts; services remain stopped; source_version_unknown={str(source_version_unknown).lower()}")


def upgrade(root, archive, destination, allow_electron_upgrade=False):
    if not (root / "trial-install.json").exists():
        raise RuntimeError("Use install --reuse-backup to adopt a checkout; in-place upgrade is for installed trials")
    marker = root / "trial-upgrade-in-progress.json"
    if marker.exists():
        raise RuntimeError("A previous upgrade needs recovery; inspect trial-upgrade-in-progress.json")
    with tempfile.TemporaryDirectory(prefix=".plango-upgrade-", dir=root.parent) as temporary:
        stage = Path(temporary)
        extract(archive, stage)
        old = release(root)
        new = release(stage)
        check_electron_transition(old, new, allow_electron_upgrade)
        backup(root, destination)
        # Keep old files outside TemporaryDirectory: a failed rollback or interrupted upgrade must not delete them.
        rollback = Path(tempfile.mkdtemp(prefix=".plango-previous-", dir=root.parent))
        write_json(marker, {"previous_application": str(rollback), "backup": str(destination)})
        moved, replaced = [], []
        try:
            for name in PAYLOAD:
                if (root / name).exists():
                    os.rename(root / name, rollback / name)
                    moved.append(name)
                if (stage / name).exists():
                    os.rename(stage / name, root / name)
                    replaced.append(name)
        except BaseException:
            failed = []
            for name in reversed(replaced):
                try:
                    os.rename(root / name, stage / name)
                except OSError:
                    failed.append(name)
            for name in reversed(moved):
                if name not in failed:
                    try:
                        os.rename(rollback / name, root / name)
                    except OSError:
                        failed.append(name)
            if failed:
                raise RuntimeError(f"Upgrade/rollback incomplete; old application retained at {rollback}; cold backup at {destination}") from None
            rollback.rmdir()
            marker.unlink()
            raise
        try:
            shutil.move(rollback, destination / "application")
        except OSError:
            raise RuntimeError(f"Application upgraded; old application retained at {rollback}; cold backup at {destination}") from None
        marker.unlink()
    print("Application upgraded; original profile, credentials and volumes kept. Start runs Alembic upgrade head.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack")
    pack.add_argument("archive", type=Path)
    add = commands.add_parser("install")
    add.add_argument("archive", type=Path)
    add.add_argument("target", type=Path)
    add.add_argument("--project", default="plango-trial")
    add.add_argument("--port", type=int, default=18021, choices=range(1024, 65536), metavar="PORT")
    add.add_argument("--reuse-backup", type=Path)
    add.add_argument("--allow-electron-upgrade", action="store_true", help="Allow a verified forward Electron version change; never allow downgrade")
    commands.add_parser("doctor")
    save = commands.add_parser("backup")
    save.add_argument("destination", type=Path)
    save.add_argument("--root", type=Path, default=ROOT)
    recover = commands.add_parser("restore")
    recover.add_argument("backup", type=Path)
    recover.add_argument("--allow-electron-upgrade", action="store_true", help="Allow a forward or explicitly unknown source version into an independent empty target")
    update = commands.add_parser("upgrade")
    update.add_argument("archive", type=Path)
    update.add_argument("--backup", type=Path, required=True)
    update.add_argument("--allow-electron-upgrade", action="store_true", help="Allow a verified forward Electron change; cold backup remains mandatory")
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "install":
        install(args.archive.resolve(), args.target.resolve(), args.project, args.port, args.reuse_backup.resolve() if args.reuse_backup else None, args.allow_electron_upgrade)
    elif args.command == "doctor":
        doctor(ROOT)
    else:
        root = args.root.resolve() if args.command == "backup" else ROOT
        lock_dir = root / "output/lifecycle"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / "lifecycle.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another lifecycle/maintenance operation is active") from None
            if args.command == "pack":
                build(args.archive.resolve())
            elif args.command == "backup":
                backup(root, args.destination.resolve())
            elif args.command == "restore":
                restore(root, args.backup.resolve(), args.allow_electron_upgrade)
            else:
                upgrade(root, args.archive.resolve(), args.backup.resolve(), args.allow_electron_upgrade)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"PlanGo trial: {error}", file=sys.stderr)
        sys.exit(1)
