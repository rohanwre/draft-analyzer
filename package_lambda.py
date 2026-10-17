"""
Builds lambda_deployment.zip: requirements-lambda.txt's packages plus the exact
project files the Lambda handler needs (api/, advisor.py, config.py - traced from
api/lambda_handler.py's actual import chain, nothing else from the repo).

Usage:
    python package_lambda.py

Then either upload lambda_deployment.zip directly (console/CLI) or let
terraform/main.tf reference it (it expects the zip at the repo root, same place
this script writes it).
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
BUILD_DIR = ROOT / "lambda_build"
ZIP_PATH = ROOT / "lambda_deployment.zip"

# Traced from api/lambda_handler.py's import chain: main.py -> routers/{drafts,players}.py
# -> deps.py/draft_state.py -> advisor.py -> config.py. Nothing else in the repo is
# imported at runtime by the Lambda.
PROJECT_FILES = ["advisor.py", "config.py"]
PROJECT_DIRS = ["api"]


def run(cmd):
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir()

    # --platform/--only-binary target Lambda's actual runtime (manylinux x86_64,
    # matching the python3.11 runtime in terraform/main.tf) regardless of what OS
    # this script runs on - matters if any dependency ever pulls in a compiled wheel.
    run([
        sys.executable, "-m", "pip", "install",
        "-r", str(ROOT / "requirements-lambda.txt"),
        "--target", str(BUILD_DIR),
        "--platform", "manylinux2014_x86_64",
        "--implementation", "cp",
        "--python-version", "3.11",
        "--only-binary=:all:",
    ])

    for f in PROJECT_FILES:
        shutil.copy(ROOT / f, BUILD_DIR / f)
    for d in PROJECT_DIRS:
        shutil.copytree(
            ROOT / d, BUILD_DIR / d,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in BUILD_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(BUILD_DIR))

    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"\nWrote {ZIP_PATH} ({size_mb:.1f} MB).")
    if size_mb > 50:
        print("WARNING: over Lambda's 50MB direct-upload limit - use S3 upload or a container image instead.")


if __name__ == "__main__":
    main()
