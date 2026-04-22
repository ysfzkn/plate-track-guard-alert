"""GateGuard build script — creates a standalone .exe distribution.

Usage:
  python build.py

Output:
  dist/GateGuard/
    ├── GateGuard.exe
    ├── .env
    ├── static/
    ├── models/
    └── _internal/

Requirements:
  pip install pyinstaller
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DIST_DIR = PROJECT_DIR / "dist" / "GateGuard"

# Only production deps — no torch/easyocr/ultralytics (saves ~4GB)
PRODUCTION_DEPS = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "opencv-python>=4.8.0",
    "numpy>=1.24.0",
    "Pillow>=10.0.0",
    "pyodbc>=5.0.0",
    "httpx>=0.25.0",
    "python-dotenv>=1.0.0",
    "aiosqlite>=0.19.0",
    "python-multipart>=0.0.6",
    "fast-alpr[onnx]>=0.1.0",
    "pyinstaller",
]


def run(cmd, **kwargs):
    """Run a command. Accepts string or list. Handles paths with spaces."""
    if isinstance(cmd, str):
        print(f"  $ {cmd}")
    else:
        print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"  [FAILED] Exit code {result.returncode}")
        sys.exit(1)
    return result


def main():
    print("=" * 60)
    print("  GateGuard — EXE Build")
    print("=" * 60)

    # Step 1: Install pyinstaller in current env
    print("\n[1/5] Installing PyInstaller...")
    run(["uv", "pip", "install", "pyinstaller"])

    # Step 2: Run PyInstaller
    print("\n[2/5] Building exe with PyInstaller...")

    hidden_imports = [
        "fast_alpr",
        "fast_plate_ocr",
        "open_image_models",
        "onnxruntime",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "multipart",
        "cv2",
        "aiosqlite",
        "httpx",
        "dotenv",
    ]

    collect_all = [
        "fast_alpr",
        "fast_plate_ocr",
        "open_image_models",
        "onnxruntime",
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "GateGuard",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--icon", str(PROJECT_DIR / "static" / "favicon.ico"),
    ]
    for h in hidden_imports:
        cmd += ["--hidden-import", h]
    for c in collect_all:
        cmd += ["--collect-all", c]
    cmd.append(str(PROJECT_DIR / "main.py"))

    run(cmd)

    # Step 3: Copy static files and config
    print("\n[3/5] Copying project files to dist...")

    dist_path = PROJECT_DIR / "dist" / "GateGuard"
    if not dist_path.exists():
        print(f"  [ERROR] PyInstaller output not found at {dist_path}")
        sys.exit(1)

    # Static files
    static_src = PROJECT_DIR / "static"
    static_dst = dist_path / "static"
    if static_dst.exists():
        shutil.rmtree(static_dst)
    shutil.copytree(static_src, static_dst, ignore=shutil.ignore_patterns("screenshots"))
    (static_dst / "screenshots").mkdir(exist_ok=True)
    print(f"  Copied static/ ({sum(1 for _ in static_dst.rglob('*') if _.is_file())} files)")

    # Config
    env_example = PROJECT_DIR / ".env.example"
    env_dst = dist_path / ".env"
    if env_example.exists() and not env_dst.exists():
        shutil.copy2(env_example, env_dst)
        print(f"  Copied .env.example -> .env")

    # Models directory
    models_src = PROJECT_DIR / "models"
    models_dst = dist_path / "models"
    models_dst.mkdir(exist_ok=True)
    if models_src.exists():
        for f in models_src.glob("*.pt"):
            shutil.copy2(f, models_dst / f.name)
            print(f"  Copied model: {f.name}")

    # App source files (needed because main.py imports app.*)
    app_src = PROJECT_DIR / "app"
    app_dst = dist_path / "app"
    if app_dst.exists():
        shutil.rmtree(app_dst)
    shutil.copytree(app_src, app_dst, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"  Copied app/ package")

    # config.py
    shutil.copy2(PROJECT_DIR / "config.py", dist_path / "config.py")
    print(f"  Copied config.py")

    # Create runtime directories
    for d in ["data", "logs", "moonwel_db"]:
        (dist_path / d).mkdir(exist_ok=True)

    # Step 4: Calculate size
    print("\n[4/5] Calculating distribution size...")
    total_size = sum(f.stat().st_size for f in dist_path.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)
    print(f"  Total: {size_mb:.0f} MB")

    # Step 5: Done
    print("\n[5/5] Build complete!")
    print()
    print("=" * 60)
    print(f"  Output: {dist_path}")
    print(f"  Size:   {size_mb:.0f} MB")
    print("=" * 60)
    print()
    print("  Next steps:")
    print(f"  1. cd {dist_path}")
    print(f"  2. Edit .env (set RTSP_URL, ESP32_IP, MOCK_MODE)")
    print(f"  3. Double-click GateGuard.exe")
    print(f"  4. Open http://localhost:8000 in browser")
    print()
    print("  To distribute:")
    print(f"  Zip the entire dist/GateGuard/ folder")
    print(f"  and copy to the target PC. No Python needed.")


if __name__ == "__main__":
    main()
