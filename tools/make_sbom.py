"""Writes the SBOM (software bill of materials) of a FreeDATA build, in the
CycloneDX JSON format: the Python packages, the GUI's npm packages and the
native libraries and data files the build ships.

    python tools/make_sbom.py --platform windows [--hamlib-version 4.7.2] [-o FILE]

Run it in the build environment, after `npm install` in freedata_gui. It needs
cyclonedx-bom (`pip install cyclonedx-bom`, also in the dev extras) and the
GUI's dev dependency @cyclonedx/cyclonedx-npm.

The Python packages are read from a fresh virtual environment with only
FreeDATA installed (`pip install .`), so build and test tools are left out;
--python-env uses an existing one instead. The GUI's packages are those the
built GUI contains (npm dev dependencies left out). The native libraries are
listed by hand in NATIVE, as in tools/third_party_licenses.py.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import venv
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "freedata_gui"

# Native libraries and data files, with the builds that ship them.
NATIVE = [
    {
        "name": "codec2",
        "version": "1.2",  # freedata_server/lib/codec2/libcodec2.1.2.*
        "type": "library",
        "license": "LGPL-2.1-only",
        "supplier": "David Rowe and the codec2 contributors",
        "url": "https://github.com/drowe67/codec2",
        "platforms": ["windows", "linux"],
        "note": "prebuilt, in freedata_server/lib/codec2",
    },
    {
        "name": "hamlib",
        "version": "{hamlib}",
        "type": "library",
        "license": "LGPL-2.1-or-later AND GPL-2.0-or-later",
        "supplier": "The Hamlib Group",
        "url": "https://github.com/Hamlib/Hamlib",
        "platforms": ["windows"],
        "note": "libhamlib-4.dll, rigctld.exe and the other Hamlib programs, from the hamlib-w64 release zip",
    },
    {
        "name": "libusb",
        "type": "library",
        "license": "LGPL-2.1-or-later",
        "url": "https://github.com/libusb/libusb",
        "platforms": ["windows"],
        "note": "libusb-1.0.dll, from the Hamlib Windows release",
    },
    {
        "name": "gcc runtime",
        "type": "library",
        "license": "GPL-3.0-or-later WITH GCC-exception-3.1",
        "url": "https://gcc.gnu.org",
        "platforms": ["windows"],
        "note": "libgcc_s_seh-1.dll, from the Hamlib Windows release",
    },
    {
        "name": "mingw-w64 winpthreads",
        "type": "library",
        "license": "MIT AND BSD-3-Clause",
        "url": "https://github.com/mingw-w64/mingw-w64",
        "platforms": ["windows"],
        "note": "libwinpthread-1.dll, from the Hamlib Windows release",
    },
    {
        "name": "portaudio",
        "version": "{portaudio}",
        "type": "library",
        "license": "MIT",
        "url": "https://github.com/PortAudio/portaudio",
        "platforms": ["windows"],
        "note": "bundled by the sounddevice package; Linux uses the system's libportaudio",
    },
    {
        "name": "waterfall (spectrum.js)",
        "type": "library",
        "license": "MIT",
        "url": "https://github.com/jledet/waterfall",
        "platforms": ["windows", "linux"],
        "note": "copied into the GUI: freedata_gui/src/assets/waterfall",
    },
    {
        "name": "world-atlas countries-50m.json",
        "version": "2",
        "type": "data",
        "license": "ISC",
        "url": "https://github.com/topojson/world-atlas",
        "platforms": ["windows", "linux"],
        "note": "GUI map data: freedata_gui/src/assets/countries-50m.json (Natural Earth, public domain)",
    },
    {
        "name": "JSON-Airports airports.json",
        "type": "data",
        "license": "MIT",
        "url": "https://github.com/jbrooksuk/JSON-Airports",
        "platforms": ["windows", "linux"],
        "note": "GUI: freedata_gui/src/assets/airports.json",
    },
]


def freedata_version():
    text = (ROOT / "freedata_server" / "constants.py").read_text(encoding="utf-8")
    return re.search(r'MODEM_VERSION = "([^"]+)"', text).group(1)


def portaudio_version(python):
    """PortAudio's version as the sounddevice package in python reports it, e.g. 19.7.0."""
    out = subprocess.run(
        [python, "-c", "import sounddevice; print(sounddevice.get_portaudio_version()[1])"],
        capture_output=True,
        text=True,
    ).stdout
    match = re.search(r"V(\d+(\.\d+)+)", out)
    return match.group(1) if match else None


def venv_python(env):
    for candidate in (Path(env) / "Scripts" / "python.exe", Path(env) / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"no Python found in {env}")


def make_python_env(directory):
    """A virtual environment with only FreeDATA and its dependencies installed."""
    venv.create(directory, with_pip=True)
    python = venv_python(directory)
    subprocess.run([python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", str(ROOT)], check=True)
    return python


def python_sbom(env, output):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cyclonedx_py",
            "environment",
            "--pyproject",
            str(ROOT / "pyproject.toml"),
            "--mc-type",
            "application",
            "--output-format",
            "JSON",
            "--output-file",
            str(output),
            str(env),
        ],
        check=True,
    )
    return json.loads(Path(output).read_text(encoding="utf-8"))


def gui_sbom(output):
    npx = shutil.which("npx") or "npx"
    subprocess.run(
        [npx, "cyclonedx-npm", "--omit", "dev", "--output-format", "JSON", "--output-file", str(output)],
        cwd=GUI,
        check=True,
    )
    return json.loads(Path(output).read_text(encoding="utf-8"))


def prefix_refs(bom, prefix):
    """Prefix the bom-refs of a BOM, so they cannot clash with another BOM's."""

    def walk(components):
        for component in components:
            if "bom-ref" in component:
                component["bom-ref"] = prefix + component["bom-ref"]
            walk(component.get("components", []))

    walk([bom["metadata"]["component"]])
    walk(bom.get("components", []))
    for dependency in bom.get("dependencies", []):
        dependency["ref"] = prefix + dependency["ref"]
        dependency["dependsOn"] = [prefix + ref for ref in dependency.get("dependsOn", [])]


def native_components(platform, versions):
    components = []
    for entry in NATIVE:
        if platform not in entry["platforms"]:
            continue
        component = {
            "type": entry["type"],
            "bom-ref": "native:" + re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-"),
            "name": entry["name"],
        }
        version = entry.get("version", "").format(**versions)
        if version and version != "None":
            component["version"] = version
        if "supplier" in entry:
            component["supplier"] = {"name": entry["supplier"]}
        component["licenses"] = [{"expression": entry["license"]}]
        component["externalReferences"] = [{"type": "vcs", "url": entry["url"]}]
        component["properties"] = [{"name": "freedata:shipped", "value": entry["note"]}]
        components.append(component)
    return components


def combine(python_bom, gui_bom, natives, version):
    """One BOM: FreeDATA as the main component, depending on the Python
    packages it needs, the GUI and the native components."""
    bom = python_bom
    root = bom["metadata"]["component"]
    root.update({"name": "FreeDATA", "version": version, "type": "application"})

    prefix_refs(gui_bom, "gui:")
    gui = gui_bom["metadata"]["component"]
    gui.update({"type": "application", "name": "freedata-gui", "version": version})
    gui.pop("components", None)

    bom["components"] = bom.get("components", []) + [gui] + gui_bom.get("components", []) + natives
    added = [gui["bom-ref"]] + [n["bom-ref"] for n in natives]
    dependencies = bom.setdefault("dependencies", [])
    for dependency in dependencies:
        if dependency["ref"] == root["bom-ref"]:
            dependency["dependsOn"] = dependency.get("dependsOn", []) + added
            break
    else:
        dependencies.append({"ref": root["bom-ref"], "dependsOn": added})
    dependencies += gui_bom.get("dependencies", [])
    dependencies += [{"ref": n["bom-ref"]} for n in natives]

    bom["serialNumber"] = f"urn:uuid:{uuid.uuid4()}"
    bom["version"] = 1
    bom["metadata"]["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bom["metadata"]["tools"] = {
        "components": bom["metadata"].get("tools", {}).get("components", [])
        + gui_bom["metadata"].get("tools", {}).get("components", [])
    }
    return bom


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--platform", required=True, choices=["windows", "linux"])
    parser.add_argument("--hamlib-version", help="Hamlib release bundled in the Windows build, e.g. 4.7.2")
    parser.add_argument("--python-env", help="virtual environment with only FreeDATA installed (default: make one)")
    parser.add_argument("-o", "--output", help="default: freedata-sbom_<platform>.cdx.json")
    args = parser.parse_args()

    version = freedata_version()
    output = Path(args.output or f"freedata-sbom_{args.platform}.cdx.json")

    with tempfile.TemporaryDirectory() as tmp:
        if args.python_env:
            env, python = args.python_env, venv_python(args.python_env)
        else:
            env = Path(tmp) / "env"
            python = make_python_env(env)
        versions = {"hamlib": args.hamlib_version, "portaudio": portaudio_version(python)}
        python_bom = python_sbom(env, Path(tmp) / "python.cdx.json")
        gui_bom = gui_sbom(Path(tmp) / "gui.cdx.json")

    bom = combine(python_bom, gui_bom, native_components(args.platform, versions), version)
    output.write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8")
    print(f"{output}: FreeDATA {version}, {len(bom['components'])} components")


if __name__ == "__main__":
    main()
