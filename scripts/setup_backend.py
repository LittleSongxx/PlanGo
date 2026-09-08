"""Install PlanGo's locked dependencies into the named conda environment."""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

from migrate_config import migrate_config, parse_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Also install locked test/lint tools")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    migrate_config(root / ".env")
    envs = json.loads(subprocess.check_output(["conda", "env", "list", "--json"]))["envs"]
    prefix = next((Path(p) for p in envs if Path(p).name == "plango"), None)
    if prefix is None:
        subprocess.run(
            [
                "conda",
                "create",
                "--yes",
                "--name",
                "plango",
                "--override-channels",
                "--channel",
                "conda-forge",
                "python=3.12",
            ],
            check=True,
        )
        envs = json.loads(subprocess.check_output(["conda", "env", "list", "--json"]))["envs"]
        prefix = next(Path(p) for p in envs if Path(p).name == "plango")
    python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            str(python),
            "-c",
            "import sys; assert sys.version_info[:2] == (3, 12), 'plango requires Python 3.12'",
        ],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="plango-setup-") as directory:
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
        # Install this project lock into its independent environment.
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
    lines = [line for line in lines if not line.startswith("PLANGO_PYTHON=")]
    values = parse_config("\n".join(lines))
    for key in ("PLANGO_BACKEND_TOKEN", "PLANGO_POSTGRES_PASSWORD"):
        if not values.get(key):
            lines = [line for line in lines if not line.startswith(key + "=")]
            lines.append(f"{key}={secrets.token_hex(32)}")
    env_file.write_text("\n".join([*lines, f"PLANGO_PYTHON={python}"]) + "\n")
    env_file.chmod(0o600)
    print("PlanGo is configured to use the plango conda environment.")


if __name__ == "__main__":
    main()
