"""Install YOYU's locked dependencies into the named conda environment."""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Also install locked test/lint tools")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    envs = json.loads(subprocess.check_output(["conda", "env", "list", "--json"]))["envs"]
    prefix = next((Path(p) for p in envs if Path(p).name == "planora"), None)
    if prefix is None:
        subprocess.run(
            [
                "conda",
                "create",
                "--yes",
                "--name",
                "planora",
                "--override-channels",
                "--channel",
                "conda-forge",
                "python=3.12",
            ],
            check=True,
        )
        envs = json.loads(subprocess.check_output(["conda", "env", "list", "--json"]))["envs"]
        prefix = next(Path(p) for p in envs if Path(p).name == "planora")
    python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            str(python),
            "-c",
            "import sys; assert sys.version_info[:2] == (3, 12), 'planora requires Python 3.12'",
        ],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="yoyu-setup-") as directory:
        requirements = str(Path(directory) / "requirements.txt")
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-hashes",
                "--no-emit-project",
                "--output-file",
                requirements,
                *(["--extra", "dev"] if args.dev else ["--no-dev"]),
            ],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        # install, not sync: retain unrelated packages in an existing shared conda environment.
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), "--requirements", requirements],
            check=True,
        )
    env_file = root / ".env"
    lines = (
        env_file.read_text().splitlines()
        if env_file.exists()
        else (root / ".env.example").read_text().splitlines()
    )
    lines = [line for line in lines if not line.startswith("YOYU_PYTHON=")]
    values = dict(
        line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#")
    )
    for key in ("YOYU_BACKEND_TOKEN", "YOYU_POSTGRES_PASSWORD"):
        if not values.get(key):
            lines = [line for line in lines if not line.startswith(key + "=")]
            lines.append(f"{key}={secrets.token_hex(32)}")
    env_file.write_text("\n".join([*lines, f"YOYU_PYTHON={python}"]) + "\n")
    env_file.chmod(0o600)
    print("YOYU is configured to use the planora conda environment.")


if __name__ == "__main__":
    main()
