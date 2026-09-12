"""Bundle gui.py into a standalone Windows build (no Python, no ffmpeg needed).

  python build.py              -> onefile exe on the Desktop
  python build.py --onedir     -> folder build (instant startup, no unpack)

ffmpeg/ffprobe are located on PATH and copied in under bin/, which gui.py
prepends to PATH at startup.
"""
import argparse, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "쇼트스크린샷추출기"
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def need(exe):
    p = shutil.which(exe)
    if not p:
        sys.exit(f"{exe} not found on PATH")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onedir", action="store_true",
                    help="folder build instead of a single exe")
    ap.add_argument("--dest", default=DESKTOP)
    a = ap.parse_args()

    ffmpeg, ffprobe = need("ffmpeg"), need("ffprobe")
    work = os.path.join(HERE, "_build")
    os.makedirs(work, exist_ok=True)

    icon = os.path.join(HERE, "app.ico")
    if not os.path.exists(icon):
        subprocess.run([sys.executable, os.path.join(HERE, "make_icon.py")], check=True)

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir" if a.onedir else "--onefile", "--windowed",
        "--name", NAME, "--icon", icon,
        "--distpath", os.path.join(work, "dist"),
        "--workpath", os.path.join(work, "build"),
        "--specpath", work,
        "--paths", HERE,
        "--add-binary", f"{ffmpeg}{os.pathsep}bin",
        "--add-binary", f"{ffprobe}{os.pathsep}bin",
        "--hidden-import", "shotlib", "--hidden-import", "shotcut",
        # trim what tkinter+numpy drag in but we never touch
        "--exclude-module", "PIL", "--exclude-module", "matplotlib",
        "--exclude-module", "pytest", "--exclude-module", "setuptools",
        os.path.join(HERE, "gui.py"),
    ]
    print(" ".join(cmd), flush=True)
    t0 = time.time()
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"PyInstaller failed ({r.returncode})")

    built = os.path.join(work, "dist", NAME + (".exe" if not a.onedir else ""))
    dest = os.path.join(a.dest, os.path.basename(built))
    if a.onedir:
        built = os.path.join(work, "dist", NAME)
        dest = os.path.join(a.dest, NAME)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(built, dest)
        size = sum(os.path.getsize(os.path.join(dp, f))
                   for dp, _, fs in os.walk(dest) for f in fs)
    else:
        shutil.copy2(built, dest)
        size = os.path.getsize(dest)
    print(f"\n{dest}\n{size/1024/1024:.0f} MB, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
