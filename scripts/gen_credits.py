#!/usr/bin/env python3
"""Write docs/credits.md and static/credits/python.json from static/credits/attributions.json.

    uv run python scripts/gen_credits.py [--fonts]

attributions.json is the one list of everything Tesserae ships or builds on that someone else
made: libraries, icons, fonts, data, the services widgets read from, and the protocols it speaks.
This adds the Python packages the server runs on (``uv export --no-dev``), with each one's
licence read from its installed metadata, and renders the lot as the docs' Credits page.
Tesserae Cloud's Credits page is built from the same file (tools/gen_credits.mjs there).

``--fonts`` re-reads every bundled font's copyright from the font file itself; it needs
fontTools and brotli (``uv run --with fonttools --with brotli ...``). Run it after adding a font.
"""

from __future__ import annotations

import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "credits" / "attributions.json"
PYTHON = ROOT / "static" / "credits" / "python.json"
DOC = ROOT / "docs" / "credits.md"


def refresh_fonts(doc: dict[str, Any]) -> None:
    """Every family in fonts_core, with the copyright its own font file carries."""
    from fontTools.ttLib import TTFont

    plugin = json.loads((ROOT / "plugins/fonts_core/plugin.json").read_text(encoding="utf-8"))
    static = ROOT / "plugins/fonts_core/static"
    out = []
    for f in plugin["fonts"]:
        want = f["id"].replace("-", "_")
        folder = next(
            d for d in static.iterdir() if d.is_dir() and d.name.replace("-", "_") == want
        )
        name = TTFont(sorted(folder.glob("*.woff2"))[0])["name"]
        out.append(
            {
                "family": f["name"],
                "dir": f"plugins/fonts_core/static/{folder.name}",
                "copyright": (name.getDebugName(0) or "").strip(),
                "licence": "OFL-1.1",
                "licence_url": "https://openfontlicense.org",
            }
        )
    # Families bundled by Tesserae Cloud rather than fonts_core stay as written.
    out += [f for f in doc["fonts"] if not f["dir"].startswith("plugins/fonts_core/static/")]
    doc["fonts"] = out


# Packages whose metadata names no licence precisely, with what their project says.
LICENCE_OVERRIDES = {"python-dateutil": "Apache-2.0 OR BSD-3-Clause"}


def licence_of(dist: metadata.Distribution) -> str:
    """The licence a package declares: its SPDX expression, else a short License field, else its classifier."""
    meta = dist.metadata
    if (meta["Name"] or "").lower() in LICENCE_OVERRIDES:
        return LICENCE_OVERRIDES[meta["Name"].lower()]
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) <= 60 and text.upper() != "UNKNOWN":
        return text
    for c in (str(x) for x in meta.get_all("Classifier") or []):
        if c.startswith("License :: OSI Approved :: "):
            return c.removeprefix("License :: OSI Approved :: ").removesuffix(" License").strip()
        if c.startswith("License :: "):
            return c.split("::")[-1].strip()
    return "see project"


def python_packages() -> list[dict[str, str]]:
    """The packages the server runs on (no dev tools), each with its licence and home page."""
    out = subprocess.run(
        [
            "uv",
            "export",
            "--no-dev",
            "--format",
            "requirements-txt",
            "--no-hashes",
            "--no-emit-project",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pkgs = []
    for line in out.splitlines():
        if not line or not line[0].isalpha():
            continue
        spec = line.split(";")[0].strip()
        name, _, version = spec.partition("==")
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            pkgs.append(
                {
                    "name": name,
                    "version": version,
                    "licence": "see project",
                    "url": f"https://pypi.org/project/{name}/",
                }
            )
            continue
        urls = dict(
            u.split(", ", 1) for u in (dist.metadata.get_all("Project-URL") or []) if ", " in u
        )
        home = (
            dist.metadata.get("Home-page")
            or urls.get("Homepage")
            or urls.get("Source")
            or urls.get("Repository")
        )
        pkgs.append(
            {
                "name": dist.metadata["Name"],
                "version": version,
                "licence": licence_of(dist),
                "url": home or f"https://pypi.org/project/{name}/",
            }
        )
    # A package locked twice (a different pin per Python version) is listed once, at the newer pin.
    seen: dict[str, dict[str, str]] = {}
    for p in pkgs:
        key = p["name"].lower()
        if key not in seen or p["version"] > seen[key]["version"]:
            seen[key] = p
    return sorted(seen.values(), key=lambda p: p["name"].lower())


def cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render(doc: dict[str, Any], py: list[dict[str, str]]) -> str:
    lines = [
        "# Credits",
        "",
        "<!-- Generated by scripts/gen_credits.py from static/credits/attributions.json. Edit that file, not this one. -->",
        "",
        "Tesserae uses the licensed work listed below. If something is wrong or missing,",
        "[open an issue](https://github.com/dmellok/tesserae/issues).",
        "",
        "## Libraries",
        "",
        "These libraries draw pages and run the editor. Each bundled file keeps its licence header.",
        "",
        "| Project | Used for | Version | Licence |",
        "|---|---|---|---|",
    ]
    for lib in doc["libraries"] + doc["icons"]:
        lines.append(
            f"| [{cell(lib['name'])}]({lib['url']}) | {cell(lib['used_for'])} | {lib['version']} | {cell(lib['licence'])} |"
        )
    lines += [
        "",
        "## Fonts",
        "",
        f"All {len(doc['fonts'])} font families bundled with Tesserae Server or Cloud use the [SIL Open Font License 1.1](https://openfontlicense.org).",
        "Each font file keeps its copyright and licence. The notices are listed below.",
        "",
        "| Family | Copyright |",
        "|---|---|",
    ]
    for f in doc["fonts"]:
        lines.append(f"| {cell(f['family'])} | {cell(f['copyright'])} |")
    lines += [
        "",
        "## Data",
        "",
        "| Source | Used for | Licence |",
        "|---|---|---|",
    ]
    for d in doc["data"]:
        lines.append(
            f"| [{cell(d['name'])}]({d['url']}) | {cell(d['used_for'])} | {cell(d['licence'])} |"
        )
    lines += [
        "",
        "## Services widgets read from",
        "",
        "Widget data stays under its provider's terms. Required credits appear here and on the widget.",
        "",
        "| Service | Used for | Licence | Credit |",
        "|---|---|---|---|",
    ]
    for s in doc["services"]:
        lines.append(
            f"| [{cell(s['name'])}]({s['url']}) | {cell(s['used_for'])} | {cell(s['licence'])} | {cell(s.get('credit', ''))} |"
        )
    lines += ["", "## Protocols and reference clients", ""]
    for p in doc["protocols"]:
        lines.append(f"- **[{p['name']}]({p['url']})**: {p['used_for']}.")
    lines += [
        "",
        "## Python packages",
        "",
        f"The server uses these {len(py)} packages, pinned in `uv.lock`. Each lists its declared licence.",
        "",
        "| Package | Version | Licence |",
        "|---|---|---|",
    ]
    for p in py:
        lines.append(f"| [{cell(p['name'])}]({p['url']}) | {p['version']} | {cell(p['licence'])} |")
    lines += [
        "",
        "## Tesserae itself",
        "",
        "Tesserae is free software under the [GNU AGPL 3.0 or later](https://github.com/dmellok/tesserae/blob/main/LICENSE).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    if "--fonts" in sys.argv:
        refresh_fonts(doc)
        SRC.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    py = python_packages()
    PYTHON.write_text(json.dumps(py, indent=1) + "\n", encoding="utf-8")
    DOC.write_text(render(doc, py), encoding="utf-8")
    print(
        f"wrote {DOC.relative_to(ROOT)} and {PYTHON.relative_to(ROOT)}: {len(doc['fonts'])} fonts, {len(py)} Python packages"
    )


if __name__ == "__main__":
    main()
