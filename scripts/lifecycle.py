"""Linux launch/stop helpers for this checkout; never signal a whole process group."""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "output" / "lifecycle"
STATE = RUN / "desktop.json"
LOG = RUN / "desktop.log"
SETUP_LOG = RUN / "setup.log"


def is_electron(p):
    executable = str(ROOT / "node_modules/electron/dist/electron")
    # Electron can collapse its process title into one /proc cmdline entry.
    return p["args"][:2] == [executable, "."] or p["args"] == [executable + " ."]


def is_launcher(p):
    args = p["args"]
    vite = len(args) == 3 and args[1] in (
        str(ROOT / "node_modules/.bin/electron-vite"),
        str(ROOT / "node_modules/electron-vite/bin/electron-vite.js"),
    ) and args[2] in ("dev", "preview")
    npm = args in (["npm run dev"], ["npm start"], ["npm run start"])
    return p["cwd"] == str(ROOT) and (vite or is_electron(p) or npm)


def process(pid):
    try:
        directory = Path("/proc") / str(pid)
        if directory.stat().st_uid != os.getuid():
            return None
        stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        try:
            cwd = os.readlink(directory / "cwd")
        except OSError:
            cwd = None  # Sandboxed descendants can hide cwd; ancestry still identifies them.
        return {
            "pid": int(pid), "parent": int(stat[1]), "started": stat[19],
            "cwd": cwd,
            "args": (directory / "cmdline").read_bytes().decode(errors="replace").rstrip("\0").split("\0"),
        }
    except (OSError, ValueError, IndexError):
        return None


def desktop_processes():
    processes = {p["pid"]: p for entry in Path("/proc").iterdir()
                 if entry.name.isdigit() and (p := process(entry.name))}
    saved = {}
    try:
        state = json.loads(STATE.read_text())
        if (state["root"] == str(ROOT)
                and state["boot"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                and isinstance(state["processes"], dict)):
            saved = state["processes"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    selected = set()
    for pid, p in processes.items():
        if saved.get(str(pid)) == p["started"] or is_launcher(p):
            selected.add(pid)
    # Children may change cwd; ancestry still ties them to this exact desktop.
    while True:
        children = {pid for pid, p in processes.items() if p["parent"] in selected}
        if children <= selected:
            return {pid: processes[pid] for pid in selected}
        selected |= children


def remember(processes):
    STATE.write_text(json.dumps({"root": str(ROOT),
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "processes": {
        str(pid): p["started"] for pid, p in processes.items()
    }}) + "\n")


def signal_process(p, sig):
    # A stale/reused PID is never enough to authorize a signal.
    current = process(p["pid"])
    if current and current["started"] == p["started"]:
        try:
            os.kill(p["pid"], sig)
        except ProcessLookupError:
            pass


def stop_desktop():
    owned = desktop_processes()
    remember(owned)
    for p in owned.values():
        signal_process(p, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not any((p := process(pid)) and p["started"] == old["started"]
                   for pid, old in owned.items()):
            break
        time.sleep(0.2)
    for p in owned.values():
        signal_process(p, signal.SIGKILL)
    STATE.unlink(missing_ok=True)


def compose():
    return ["docker", "compose", "--project-name", "plango", "--project-directory", str(ROOT),
            "--env-file", str(ROOT / ".env") if (ROOT / ".env").exists() else "/dev/null",
            "--file", str(ROOT / "docker-compose.yml")]


def check_compose_owner():
    template = ('{"root":{{json (.Label "com.docker.compose.project.working_dir")}},'
                '"files":{{json (.Label "com.docker.compose.project.config_files")}}}')
    with SETUP_LOG.open("a") as log:
        result = subprocess.run(["docker", "ps", "--all", "--filter",
                                 "label=com.docker.compose.project=plango", "--format", template],
                                stdout=subprocess.PIPE, stderr=log, text=True)
    if result.returncode:
        raise ConnectionError(f"无法检查 Docker；请确认 Docker 已运行，详见 {SETUP_LOG}")
    for line in result.stdout.splitlines():
        labels = json.loads(line)
        if (not labels.get("root") or Path(labels["root"]).resolve() != ROOT
                or labels.get("files") != str(ROOT / "docker-compose.yml")):
            raise RuntimeError("Compose 项目 plango 已被其他目录使用或无法确认归属；未修改容器。")


def run_setup(command, label, **kwargs):
    print(label, flush=True)
    with SETUP_LOG.open("a") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=log, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{label}失败；请检查 {SETUP_LOG}")


def start():
    existing = desktop_processes()
    check_compose_owner()
    if any(is_launcher(p) for p in existing.values()):
        run_setup([*compose(), "up", "--detach", "--wait", "--wait-timeout", "180"], "检查并准备已有桌面的 PlanGo Docker 服务…")
        existing = desktop_processes()
        if any(is_launcher(p) for p in existing.values()):
            remember(existing)
            print(f"PlanGo 桌面已运行，已纳管（PID {min(existing)}），未重复启动。")
            return
    if existing:
        stop_desktop()
    for command in ("node", "npm", "conda", "uv", "docker"):
        if not shutil.which(command):
            raise RuntimeError(f"缺少 {command}；请先安装并加入 PATH。")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("未发现桌面显示环境；请在图形桌面的终端运行 start.sh。")
    run_setup(["node", "-e", "const [a,b]=process.versions.node.split('.').map(Number); process.exit(a>20||(a===20&&b>=11)?0:1)"], "检查 Node.js 20.11+…")
    lock_hash = hashlib.sha256((ROOT / "package.json").read_bytes() + (ROOT / "package-lock.json").read_bytes()).hexdigest()
    stamp = RUN / "node-dependencies.sha256"
    if (not stamp.exists() or stamp.read_text() != lock_hash
            or not (ROOT / "node_modules/.bin/electron-vite").exists()
            or not (ROOT / "node_modules/electron/dist/electron").exists()):
        run_setup(["npm", "ci"], "安装本仓库 Node 依赖…")
        stamp.write_text(lock_hash)
    run_setup([sys.executable, str(ROOT / "scripts/setup_backend.py")], "准备 plango Python 环境与本项目配置…")
    with SETUP_LOG.open("a") as log:
        result = subprocess.run([*compose(), "config", "--format", "json"], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=log, text=True)
    if result.returncode:
        raise RuntimeError(f"Docker 配置检查失败；请检查 {SETUP_LOG}")
    api = json.loads(result.stdout)["services"]["api"]
    port = api["ports"][0]["published"]
    environment = os.environ.copy()
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    environment.update(PLANGO_BACKEND_AUTOSTART="false", PLANGO_BACKEND_URL=f"http://127.0.0.1:{port}",
                       PLANGO_BACKEND_TOKEN=api["environment"]["PLANGO_BACKEND_TOKEN"])
    run_setup([*compose(), "up", "--build", "--detach", "--wait", "--wait-timeout", "180"], "启动 PlanGo Docker 服务并等待健康检查…")
    print(f"启动桌面，日志：{LOG}", flush=True)
    with LOG.open("a") as log:
        log_start = log.tell()
        child = subprocess.Popen(["npm", "run", "dev"], cwd=ROOT, env=environment,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    ready = False
    try:
        initial = process(child.pid)
        if initial:
            remember({child.pid: initial})
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and child.poll() is None:
            owned = desktop_processes()
            remember(owned)
            if (any(is_electron(p) for p in owned.values())
                    and b"[plango] Desktop ready" in LOG.read_bytes()[log_start:]):
                time.sleep(2)
                if child.poll() is None and any((live := process(pid)) and is_electron(live) for pid in owned):
                    remember(desktop_processes())
                    ready = True
                    print(f"PlanGo 已启动：{environment['PLANGO_BACKEND_URL']}（PID {child.pid}）。")
                    return
            time.sleep(0.3)
        raise RuntimeError(f"桌面启动失败；请检查 {LOG}。Docker 服务保留运行，可用 stop.sh 停止。")
    finally:
        if not ready:
            stop_desktop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("这些脚本需要 Linux /proc；其他系统请使用 README 中的 npm 命令。")
    os.umask(0o077)
    RUN.mkdir(parents=True, exist_ok=True)
    # ponytail: one checkout lock; separate locks only if independent profiles are added.
    with (RUN / "lifecycle.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一个 PlanGo 启停操作正在执行，请稍后重试。") from None
        if args.action == "start":
            start()
        else:
            try:
                check_compose_owner()
            except (ConnectionError, FileNotFoundError):
                stop_desktop()
                raise
            stop_desktop()
            # down only needs project identity; it must work even if .env was removed.
            environment = dict(os.environ, PLANGO_BACKEND_TOKEN="unused-for-stop", PLANGO_POSTGRES_PASSWORD="unused-for-stop")
            run_setup([*compose(), "down"], "停止本项目 Docker 服务（保留数据卷）…", env=environment)
            print("PlanGo 已停止，数据卷已保留。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("PlanGo 启停操作已中断；可用 stop.sh 停止本项目剩余服务。", file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(f"PlanGo: {error}", file=sys.stderr)
        sys.exit(1)
