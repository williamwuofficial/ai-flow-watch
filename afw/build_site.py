"""Assemble the deployable site: site/* + data/derived/* → _site/. Usage: python -m afw.build_site

Script and stylesheet links get a content hash (?v=...) so a new index.html is never paired with
a stale cached app.js (GitHub Pages serves everything with max-age=600).
"""
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"
VERSIONED = ["app.js", "style.css"]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT)
    shutil.copytree(ROOT / "data" / "derived", OUT / "data")
    index = OUT / "index.html"
    html = index.read_text()
    for name in VERSIONED:
        digest = hashlib.sha256((OUT / name).read_bytes()).hexdigest()[:10]
        ref = f'"{name}"'
        if ref not in html:
            raise SystemExit(f"build_site: {ref} not referenced in index.html")
        html = html.replace(ref, f'"{name}?v={digest}"')
    index.write_text(html)
    (OUT / ".nojekyll").touch()
    print(f"built {OUT}")


if __name__ == "__main__":
    main()
