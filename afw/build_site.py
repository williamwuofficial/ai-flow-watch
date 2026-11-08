"""Assemble the deployable site: site/* + data/derived/* → _site/. Usage: python -m afw.build_site"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT)
    shutil.copytree(ROOT / "data" / "derived", OUT / "data")
    (OUT / ".nojekyll").touch()
    print(f"built {OUT}")


if __name__ == "__main__":
    main()
