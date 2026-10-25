"""Writes THIRD-PARTY-LICENSES.txt: the licenses of the third-party software
and data that FreeDATA builds ship besides FreeDATA itself (GPL-3.0, see LICENSE).

It reads the Python packages that pyproject.toml needs (as installed) and the GUI
packages in freedata_gui/node_modules, so run it in the build environment,
after `pip install .` and `npm install`:

    python tools/third_party_licenses.py [-o THIRD-PARTY-LICENSES.txt]

Python packages are listed as installed on the platform it runs on (some
dependencies differ between Windows and Linux). The texts of the native
libraries (codec2, Hamlib, PortAudio, ...) are kept in tools/licenses/.
"""

import argparse
import hashlib
import importlib.metadata as md
import json
import re
import sys
import sysconfig
import tomllib
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:  # build environments without packaging: use pip's copy
    from pip._vendor.packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
LICENSES = ROOT / "tools" / "licenses"
GUI = ROOT / "freedata_gui"

# license texts for packages that don't ship one in their wheel or npm package
PACKAGE_TEXTS = {
    "pyserial": "tools/licenses/pyserial-LICENSE.txt",
}

LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice|unlicense)([-._].*)?$", re.IGNORECASE)

# Native libraries, programs and data that are not Python or npm packages.
# "texts" are files in tools/licenses/ (or the repository) printed in full.
NATIVE = [
    {
        "name": "codec2",
        "shipped": "all builds: freedata_server/lib/codec2 (libcodec2.dll / .so / .dylib)",
        "license": "LGPL-2.1",
        "copyright": "Copyright (C) David Rowe and the codec2 contributors",
        "source": "https://github.com/drowe67/codec2",
        "texts": ["tools/licenses/LGPL-2.1.txt"],
    },
    {
        "name": "Hamlib (libhamlib-4.dll, rigctld.exe and the other Hamlib programs)",
        "shipped": "Windows installer: freedata-server/lib/hamlib/bin. Linux and macOS use the system's Hamlib",
        "license": "LGPL-2.1-or-later (library), GPL-2.0-or-later (programs)",
        "copyright": "Copyright (C) Frank Singleton, Stephane Fillod and the Hamlib Group",
        "source": "https://github.com/Hamlib/Hamlib (release hamlib-w64 zip)",
        "texts": ["tools/licenses/hamlib-LICENSE.txt", "tools/licenses/LGPL-2.1.txt", "tools/licenses/GPL-2.0.txt"],
    },
    {
        "name": "libusb (libusb-1.0.dll, from the Hamlib Windows release)",
        "shipped": "Windows installer: freedata-server/lib/hamlib/bin",
        "license": "LGPL-2.1-or-later",
        "copyright": "Copyright (C) the libusb authors",
        "source": "https://github.com/libusb/libusb",
        "texts": ["tools/licenses/LGPL-2.1.txt"],
    },
    {
        "name": "GCC runtime (libgcc_s_seh-1.dll, from the Hamlib Windows release)",
        "shipped": "Windows installer: freedata-server/lib/hamlib/bin",
        "license": "GPL-3.0-or-later with the GCC Runtime Library Exception 3.1",
        "copyright": "Copyright (C) Free Software Foundation, Inc.",
        "source": "https://gcc.gnu.org",
        "texts": ["tools/licenses/gcc-runtime-library-exception.txt", "LICENSE"],
    },
    {
        "name": "mingw-w64 winpthreads (libwinpthread-1.dll, from the Hamlib Windows release)",
        "shipped": "Windows installer: freedata-server/lib/hamlib/bin",
        "license": "MIT and BSD-3-Clause style (see text)",
        "copyright": "Copyright (c) 2011 mingw-w64 project",
        "source": "https://github.com/mingw-w64/mingw-w64",
        "texts": ["tools/licenses/winpthreads-COPYING.txt"],
    },
    {
        "name": "PortAudio",
        "shipped": (
            "Windows and macOS: bundled by the sounddevice package (_sounddevice_data/portaudio-binaries). "
            "Linux uses the system's libportaudio"
        ),
        "license": "MIT",
        "copyright": "Copyright (c) 1999-2006 Ross Bencina and Phil Burk",
        "source": "https://github.com/PortAudio/portaudio",
        "texts": ["tools/licenses/portaudio-LICENSE.txt"],
    },
    {
        "name": "Waterfall display (spectrum.js)",
        "shipped": "GUI: freedata_gui/src/assets/waterfall",
        "license": "MIT",
        "copyright": "Copyright (c) 2019 Jeppe Ledet-Pedersen",
        "source": "https://github.com/jledet/waterfall",
        "texts": ["freedata_gui/src/assets/waterfall/LICENSE"],
    },
    {
        "name": "World map data (countries-50m.json)",
        "shipped": "GUI: freedata_gui/src/assets/countries-50m.json",
        "license": "ISC (world-atlas); the map data itself is public domain (Natural Earth)",
        "copyright": "Copyright 2013-2019 Michael Bostock (world-atlas); Natural Earth (public domain)",
        "source": "https://github.com/topojson/world-atlas, https://www.naturalearthdata.com",
        "texts": ["tools/licenses/world-atlas-LICENSE.txt"],
        "note": "The file matches world-atlas 2 countries-50m.json; the repository does not record where it was taken from.",
    },
    {
        "name": "Airport list (airports.json)",
        "shipped": "GUI: freedata_gui/src/assets/airports.json",
        "license": "unknown",
        "copyright": "unknown",
        "source": "not recorded in the repository",
        "texts": [],
        "note": "Used to show airport names for METAR messages. Its origin and license still have to be checked.",
    },
]


def python_runtime():
    """The license of the Python interpreter that Nuitka builds embed."""
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt",
    ]
    text_file = next((p for p in candidates if p.is_file()), None)
    return {
        "name": f"Python {sys.version.split()[0]} runtime",
        "shipped": "Windows installer and AppImage: embedded by Nuitka",
        "license": "PSF-2.0",
        "copyright": "Copyright (c) Python Software Foundation",
        "source": "https://www.python.org",
        "texts": [text_file] if text_file else [],
        "note": None if text_file else "See https://docs.python.org/3/license.html",
    }


def package_text(name):
    path = PACKAGE_TEXTS.get(name)
    return [ROOT / path] if path else []


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def python_license(dist):
    meta = dist.metadata
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(classifiers)
    text = (meta.get("License") or "").strip().splitlines()
    return text[0][:80] if text else "unknown"


def python_license_texts(dist):
    texts = []
    for f in dist.files or []:
        parts = Path(str(f)).parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        if "licenses" in parts or LICENSE_FILE_RE.match(Path(str(f)).name):
            path = Path(dist.locate_file(f))
            if path.is_file():
                texts.append(path)
    return sorted(set(texts))


def python_packages():
    """The runtime dependencies listed in pyproject.toml, with their extras and
    dependencies, as installed on this platform."""
    found = {}
    done = set()

    def walk(name, extras=frozenset()):
        if (norm(name), extras) in done:
            return
        done.add((norm(name), extras))
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            return
        found[norm(name)] = dist
        for requirement in dist.requires or []:
            req = Requirement(requirement)
            if req.marker:
                envs = [{"extra": e} for e in extras] or [{"extra": ""}]
                if not any(req.marker.evaluate(env) for env in envs):
                    continue
            walk(req.name, frozenset(req.extras))

    # pyproject.toml, not the installed metadata, which can be stale
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for requirement in project["dependencies"]:
        req = Requirement(requirement)
        if not req.marker or req.marker.evaluate({"extra": ""}):
            walk(req.name, frozenset(req.extras))
    return [
        {
            "name": d.metadata["Name"],
            "version": d.version,
            "license": python_license(d),
            "texts": python_license_texts(d) or package_text(d.metadata["Name"]),
        }
        for d in sorted(found.values(), key=lambda d: norm(d.metadata["Name"]))
    ]


def npm_packages():
    """The GUI packages that end up in the built GUI (not the build tools)."""
    lock = json.loads((GUI / "package-lock.json").read_text(encoding="utf-8"))
    packages = []
    for path, info in lock["packages"].items():
        if not path or info.get("dev"):
            continue
        folder = GUI / path
        name = path.split("node_modules/")[-1]
        package_json = folder / "package.json"
        meta = json.loads(package_json.read_text(encoding="utf-8")) if package_json.is_file() else {}
        license_ = info.get("license") or meta.get("license") or "unknown"
        if isinstance(license_, dict):
            license_ = license_.get("type", "unknown")
        texts = (
            sorted(p for p in folder.iterdir() if p.is_file() and LICENSE_FILE_RE.match(p.name))
            if folder.is_dir()
            else []
        )
        texts = texts or package_text(name)
        packages.append({"name": name, "version": info.get("version", "?"), "license": license_, "texts": texts})
    return sorted(packages, key=lambda p: p["name"].lower())


def read_text(path):
    path = path if Path(path).is_absolute() else ROOT / path
    return Path(path).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", default=str(ROOT / "THIRD-PARTY-LICENSES.txt"))
    args = parser.parse_args()

    native = NATIVE[:6] + [python_runtime()] + NATIVE[6:]
    python = python_packages()
    npm = npm_packages()
    if not python:
        sys.exit("FreeDATA's dependencies are not installed here. Run `pip install .` first.")
    if not (GUI / "node_modules").is_dir():
        sys.exit("freedata_gui/node_modules is missing. Run `npm install` in freedata_gui first.")

    # every distinct license text is printed once, with the components using it
    texts = {}  # sha256 -> {"text": str, "users": [str]}

    def add_texts(user, paths, missing="none published by the project"):
        refs = []
        for path in paths:
            text = read_text(path)
            key = hashlib.sha256(text.encode()).hexdigest()
            entry = texts.setdefault(key, {"text": text, "users": [], "number": len(texts) + 1})
            if user not in entry["users"]:
                entry["users"].append(user)
            refs.append(f"[{entry['number']}]")
        return " ".join(refs) if refs else missing

    out = []
    rule = "=" * 78
    out += [
        "FreeDATA - third-party licenses",
        rule,
        "",
        "FreeDATA itself is licensed under the GNU General Public License v3.0 or",
        "later (see LICENSE). FreeDATA builds also contain the third-party software",
        "and data listed below, each under its own license. The full license texts",
        "follow at the end of this file, numbered [1], [2], ...",
        "",
        "Source code of the LGPL and GPL components is available from the projects",
        "listed under 'Source'.",
        "",
        f"Generated by tools/third_party_licenses.py on {sys.platform}; Python packages",
        "are listed as installed for that platform.",
        "",
        "",
        "1. Native libraries, programs and data",
        rule,
    ]
    for item in native:
        out += [
            "",
            item["name"],
            f"  Shipped in: {item['shipped']}",
            f"  License:    {item['license']}",
            f"  Copyright:  {item['copyright']}",
            f"  Source:     {item['source']}",
            f"  Text:       {add_texts(item['name'], item['texts'], missing='none')}",
        ]
        if item.get("note"):
            out.append(f"  Note:       {item['note']}")

    for title, packages in (
        ("2. Python packages (server, all builds)", python),
        ("3. JavaScript packages (built into the web GUI)", npm),
    ):
        out += ["", "", title, rule, ""]
        for p in packages:
            label = f"{p['name']} {p['version']}"
            out.append(f"{label:<48} {p['license']:<40} text {add_texts(label, p['texts'])}")

    out += ["", "", "4. License texts", rule]
    for entry in sorted(texts.values(), key=lambda e: e["number"]):
        users = ", ".join(entry["users"])
        out += ["", "-" * 78, f"[{entry['number']}] Used by: {users}", "-" * 78, "", entry["text"]]

    Path(args.output).write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(
        f"Wrote {args.output}: {len(native)} native components, {len(python)} Python and {len(npm)} npm packages, "
        f"{len(texts)} license texts"
    )


if __name__ == "__main__":
    main()
