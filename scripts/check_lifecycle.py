"""Run isolated lifecycle checks: fake Docker/npm/conda, real disposable process trees."""

import fcntl
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    source = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    assert node, "Node is required for the disposable desktop process"
    with tempfile.TemporaryDirectory(prefix="plango lifecycle ") as directory:
        root = Path(directory) / "checkout"
        root.mkdir()
        (root / "scripts").mkdir()
        for path in ("start.sh", "stop.sh", "scripts/lifecycle.py"):
            shutil.copy2(source / path, root / path)
        (root / "package.json").write_text('{"main":"desktop.cjs"}')
        (root / "package-lock.json").write_text("{}")
        (root / "docker-compose.yml").write_text("name: plango\n")
        (root / "scripts/setup_backend.py").write_text(
            "import pathlib,subprocess\n"
            "subprocess.run(['conda','env','list'],check=True)\n"
            "subprocess.run(['uv','--version'],check=True)\n"
            "pathlib.Path('.env').write_text('NEVER_SOURCE=$(touch should-not-exist)\\n')\n"
        )
        vite = root / "node_modules/.bin/electron-vite"
        vite.parent.mkdir(parents=True)
        vite.write_text("const cp=require('node:child_process'); const path=require('node:path'); "
                        "cp.spawn(path.resolve('node_modules/electron/dist/electron'), ['.','--test-flag'], {stdio:'inherit'}); "
                        "setInterval(()=>{},1000);")
        electron = root / "node_modules/electron/dist/electron"
        electron.parent.mkdir(parents=True)
        electron.symlink_to(node)
        (root / "desktop.cjs").write_text(
            "const cp=require('node:child_process'); const fs=require('node:fs'); "
            "const child=cp.spawn('/bin/sleep',['600'],{cwd:'/tmp',stdio:'ignore'}); "
            "fs.writeFileSync('test-pids.json',JSON.stringify([process.pid,child.pid])); "
            "process.title=require('node:path').resolve('node_modules/electron/dist/electron')+' .'; "
            "console.log('[plango] Desktop ready'); "
            "setInterval(()=>{},1000);"
        )
        binaries = Path(directory) / "bin"
        binaries.mkdir()
        for command in ("docker", "npm", "conda", "uv"):
            binary = binaries / command
            binary.write_text("#!/usr/bin/env python3\n"
                "import json,os,pathlib,sys\n"
                "name=pathlib.Path(sys.argv[0]).name\n"
                "with open('calls.jsonl','a') as f: f.write(json.dumps([name,*sys.argv[1:]])+'\\n')\n"
                "if name=='docker' and sys.argv[1]=='ps':\n"
                "    if pathlib.Path('fail-docker').exists(): sys.exit(1)\n"
                "    print(json.dumps({'root':'/another-checkout' if pathlib.Path('conflict').exists() else str(pathlib.Path.cwd()),'files':str(pathlib.Path.cwd()/'docker-compose.yml')}))\n"
                "elif name=='docker':\n"
                "    assert sys.argv[1:4]==['compose','--project-name','plango']\n"
                "    assert sys.argv[sys.argv.index('--file')+1]==str(pathlib.Path.cwd()/'docker-compose.yml')\n"
                "    assert '--volumes' not in sys.argv and '-v' not in sys.argv\n"
                "    if 'up' in sys.argv and pathlib.Path('fail-services').exists(): sys.exit(1)\n"
                "    if 'config' in sys.argv: print(json.dumps({'services':{'api':{'ports':[{'published':'18011'}],'environment':{'PLANGO_BACKEND_TOKEN':'test-secret'}}}}))\n"
                "if name=='npm' and sys.argv[1:]==['run','dev']:\n"
                "    if pathlib.Path('fail-desktop').exists(): sys.exit(1)\n"
                "    assert 'ELECTRON_RUN_AS_NODE' not in os.environ\n"
                "    assert os.environ['PLANGO_BACKEND_AUTOSTART']=='false'\n"
                "    assert os.environ['PLANGO_BACKEND_URL']=='http://127.0.0.1:18011'\n"
                "    assert os.environ['PLANGO_BACKEND_TOKEN']=='test-secret'\n"
                f"    os.execv({node!r},[{node!r},str(pathlib.Path.cwd()/'node_modules/.bin/electron-vite'),'dev'])\n"
            )
            binary.chmod(0o755)
        environment = dict(os.environ, PATH=f"{binaries}:{os.environ['PATH']}",
                           DISPLAY=":isolated-check", ELECTRON_RUN_AS_NODE="1")

        def invoke(action, success=True):
            result = subprocess.run([str(root / f"{action}.sh")], cwd="/", env=environment,
                                    capture_output=True, text=True, timeout=60)
            assert (result.returncode == 0) == success, result.stdout + result.stderr
            assert "test-secret" not in result.stdout + result.stderr
            return result.stdout + result.stderr

        def calls():
            return [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]

        def alive(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False

        outsiders = []
        try:
            assert "已启动" in invoke("start")
            pids = json.loads((root / "test-pids.json").read_text())
            assert all(alive(pid) for pid in pids)
            assert "已运行" in invoke("start")
            assert calls().count(["npm", "run", "dev"]) == 1
            assert calls().count(["npm", "ci"]) == 1
            (root / "fail-services").touch()
            assert "失败" in invoke("start", success=False)
            assert all(alive(pid) for pid in pids)
            (root / "fail-services").unlink()
            (root / "conflict").touch()
            previous_calls = len(calls())
            assert "其他目录" in invoke("stop", success=False)
            assert len(calls()) == previous_calls + 1
            assert all(alive(pid) for pid in pids)
            (root / "conflict").unlink()
            assert not (root / "should-not-exist").exists()
            assert (root / "output/lifecycle/desktop.json").stat().st_mode & 0o077 == 0
            with (root / "output/lifecycle/lifecycle.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                assert "正在执行" in invoke("start", success=False)
            unrelated = subprocess.Popen(["/bin/sleep", "600"], cwd=root)
            outsiders.append(unrelated)
            foreign = Path(directory) / "another-checkout"
            shutil.copytree(root / "node_modules", foreign / "node_modules", symlinks=True)
            shutil.copy(root / "package.json", foreign)
            shutil.copy(root / "desktop.cjs", foreign)
            other = subprocess.Popen([node, str(foreign / "node_modules/.bin/electron-vite"), "dev"], cwd=foreign)
            outsiders.append(other)
            deadline = time.monotonic() + 5
            while not (foreign / "test-pids.json").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            foreign_pids = json.loads((foreign / "test-pids.json").read_text())
            state_file = root / "output/lifecycle/desktop.json"
            state = json.loads(state_file.read_text())
            state["processes"][str(unrelated.pid)] = "stale-start-time"
            state_file.write_text(json.dumps(state))
            assert "已停止" in invoke("stop")
            assert not any(alive(pid) for pid in pids)
            assert unrelated.poll() is None and other.poll() is None and all(alive(pid) for pid in foreign_pids)
            assert "已停止" in invoke("stop")
            assert calls().count(["npm", "run", "dev"]) == 1
            # An adopted child that changed cwd remains ours after its parent exits.
            manual = subprocess.Popen([node, str(vite), "dev"], cwd=root)
            outsiders.append(manual)
            time.sleep(0.3)
            assert "已运行" in invoke("start")
            assert calls().count(["npm", "run", "dev"]) == 1
            adopted_pids = json.loads((root / "test-pids.json").read_text())
            manual.terminate()
            manual.wait(timeout=3)
            os.kill(adopted_pids[0], signal.SIGTERM)
            time.sleep(0.1)
            assert alive(adopted_pids[1])
            assert "已启动" in invoke("start")
            assert not alive(adopted_pids[1])
            invoke("stop")
            (root / "fail-services").touch()
            previous_launches = calls().count(["npm", "run", "dev"])
            assert "失败" in invoke("start", success=False)
            assert calls().count(["npm", "run", "dev"]) == previous_launches
            (root / "fail-services").unlink()
            (root / "fail-desktop").touch()
            assert "桌面启动失败" in invoke("start", success=False)
            assert not state_file.exists()
            (root / "fail-desktop").unlink()
            (root / "test-pids.json").unlink()
            interrupted = subprocess.Popen([str(root / "start.sh")], cwd="/", env=environment,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 10
            while not (root / "test-pids.json").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            interrupted_pids = json.loads((root / "test-pids.json").read_text())
            interrupted.send_signal(signal.SIGINT)
            interrupted.communicate(timeout=15)
            assert interrupted.returncode == 130
            assert not any(alive(pid) for pid in interrupted_pids)
            assert "已启动" in invoke("start")
            assert calls().count(["npm", "ci"]) == 1
            pids = json.loads((root / "test-pids.json").read_text())
            (root / "fail-docker").touch()
            assert "无法检查 Docker" in invoke("stop", success=False)
            assert not any(alive(pid) for pid in pids)
            (root / "fail-docker").unlink()
            (root / ".env").unlink()
            invoke("stop")
            assert calls()[-1][calls()[-1].index("--env-file") + 1] == "/dev/null"
            print("Lifecycle checks passed: start/reuse, locks, ownership conflicts, stale PID, manual adoption, orphan cleanup, failure/interrupt cleanup, process isolation, retained volumes, missing .env.")
        finally:
            (root / "conflict").unlink(missing_ok=True)
            (root / "fail-docker").unlink(missing_ok=True)
            invoke("stop")
            for child in outsiders:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=3)
            if "foreign_pids" in locals():
                for pid in foreign_pids:
                    if alive(pid):
                        os.kill(pid, 15)


if __name__ == "__main__":
    main()
