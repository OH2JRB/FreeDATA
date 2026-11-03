# Contribution Guide

## Required Tools and Dependencies

The backend of FreeDATA is built with python, with a couple dependencies written
in C/C++, and the frontend is built with Vue 3 and Vite, so you will need Python
(from 3.12 to 3.13) to work on the backend, and Node.js 22 or newer with npm to
work on the frontend.

System dependencies required:

- python3 (with virtual environment support)
- portaudio
- hamlib (optional, there is a vendored version bundled with the freedata
  server, or you can build it from source)
- Node.js 22 or newer, and npm (the GUI CI tests Node 22, 24 and 26; Node 20 can
  no longer build the GUI)

Example for debian trixie: `sudo apt update && sudo apt install python3
python3-venv portaudio19-dev`. Debian's own `nodejs` package is older than
Node 22, so install Node.js from [NodeSource](https://github.com/nodesource/distributions)
or with [nvm](https://github.com/nvm-sh/nvm) instead, and check it with
`node --version`.

Example for MacOS: `brew install python pyenv-virtualenv npm node@24 portaudio`

To fetch the python dependencies create a virtual environment and next use pip:

- Create a virtual environment: `python -m venv venv`
- Load the virtual environment `source venv/bin/activate`
- There are several sets of dependencies you can install:
  - `pip install .` installs only the libraries required to run freedata
  - `pip install .[test]` installs the libraries required for running and testing
    freedata
  - `pip install .[dev]` installs the libraries required for running, testing
    and linting/checking freedata
  - `pip install .[build]` installs only the libraries required to run and build
    the freedata pip module
  - `pip install .[nuitka]` installs only the libraries required to run and
    build the freedata windows executable

To fetch the `npm` dependencies go inside `./freedata_gui` and run `npm
install`. The GUI's scripts, run inside `./freedata_gui`:

- `npm run serve` starts the Vite development server with hot reload
- `npm run build` installs the dependencies and builds the GUI into
  `freedata_gui/dist`, which the server serves at `/gui`
- `npm run preview` serves the built GUI for a last check
- `npm run lint` runs ESLint, as the GUI CI does
- `npm run sbom` writes the GUI's SBOM to `freedata-gui.cdx.json` (see below)

## Third-party licenses

`THIRD-PARTY-LICENSES.txt` lists the licenses of everything FreeDATA builds
ship besides FreeDATA itself: codec2, Hamlib, PortAudio and the other native
libraries, and the Python and GUI packages with their license texts. The
Windows installer and the AppImage contain it (and `LICENSE.txt`) next to
`freedata-server`; their workflows regenerate it for the packages each build
installs.

After adding or updating a dependency, regenerate it in your environment
(after `pip install .` and `npm install` in `freedata_gui`) and commit it:

```
python tools/third_party_licenses.py
```

The texts of the native libraries live in `tools/licenses/`; add one there
(and an entry in the script) when bundling a new library.

## SBOM

A software bill of materials (SBOM) lists every component a build contains,
with versions; the EU Cyber Resilience Act asks for one. The Windows and
AppImage release workflows write one in the CycloneDX JSON format and attach
it to the release (`freedata-sbom_windows.cdx.json`,
`freedata-sbom_linux.cdx.json`); every workflow run also keeps it as an
artifact.

`tools/make_sbom.py` writes it, with CycloneDX's own tools (`pip install
.[dev]` and `npm install` in `freedata_gui` install them):

```
python tools/make_sbom.py --platform windows --hamlib-version 4.7.2
```

- Python packages: from a fresh virtual environment with only FreeDATA
  installed, so build and test tools are left out (`--python-env` uses an
  existing one).
- GUI: the packages the built GUI contains, npm dev dependencies left out.
  `npm run sbom` inside `./freedata_gui` writes just these, to
  `freedata-gui.cdx.json`.
- Native libraries and data files (codec2, Hamlib, PortAudio, the map data,
  ...): listed by hand in `NATIVE` in the script, for the builds that ship
  them. Update it, as `tools/third_party_licenses.py`, when bundling a new
  one or another version. The versions of the DLLs from the Hamlib release
  (libusb, the gcc runtime, winpthreads) are read from the files, and Hamlib's
  and PortAudio's from the build; the copied waterfall and airports.json
  files have no releases, so their entries name the upstream git commit and
  say that FreeDATA changed them.

Each component gets one SPDX license; free-text duplicates are dropped. A
package whose metadata names no SPDX license (only "BSD License" or nothing)
gets it from `LICENSE_FIXES` in the script, checked against its license file.
The script lists any it still cannot name; add them there.

The generated `*.cdx.json` files are not committed.

## Pinned downloads

Everything the builds and install scripts download from outside PyPI and npm
is fixed to one version and checked, so a changed or replaced file stops the
build instead of ending up in it. When updating one, change the version and
its checksum or commit together:

| What | Where | Pinned by |
| --- | --- | --- |
| Hamlib Windows zip | `build_nsis_bundle.yml`: `HAMLIB_VERSION`, `HAMLIB_SHA256` | SHA-256 from the Hamlib release page |
| Hamlib source | `Dockerfile`: `HAMLIB_VERSION`, `HAMLIB_SHA256`; Linux installer: `hamlibsha256_472` (other versions are checked against the release's `SHA256SUM-<version>`); macOS installer: 4.5.5 | SHA-256 |
| codec2 source | `Dockerfile`: `CODEC2_COMMIT`; Linux and macOS installers: `codec2commit` | git commit (codec2's only release, 1.2.0, predates the modes FreeDATA uses) |
| nvm installer | Linux installer: `nvmsha256` | SHA-256 of `install.sh` |
| appimagetool | `build_appimage_bundle.yml` | release 1.9.1 and its SHA-256 |
| GitHub Actions | all workflows | commit hash, with the version as a comment; Dependabot updates both |

The prebuilt codec2 libraries in `freedata_server/lib/codec2` came from
upstream DJ2LS/FreeDATA; which codec2 commit they were built from is not
recorded. What is known:

| File | Platform | Built with | Added in | SHA-256 |
| --- | --- | --- | --- | --- |
| `libcodec2.dll` | Windows x86-64 | MinGW GCC 13.1.1 (Fedora) | 772ea7a5, 2024-04-18 | `5922b32bede9f137c6d701d5a547be4f823643cade06b98eb6da7346824b569d` |
| `libcodec2.so` | Linux x86-64 | GCC 11.4.0 (Ubuntu 22.04) | 3d2e6457, 2024-05-08 | `18184adbedf5b1b50dfd892c3e53ac43a632bb4cb0a01e17c02800c3ec6ffb29` |
| `libcodec2.1.2.dylib` | macOS x86-64 | | 7fe71445, 2024-09-15 | `1a760ad2baa5cab9d2b861646f770080ea1f5d816c0b85166866c5ee0fd5ce9c` |
| `arm64_libcodec2.1.2.dylib` | macOS arm64 | | fd88a318, 2026-01-09 | `68a5facec7c43765e6926fb4f24e12d667f1dd204335f079fae4204a1e64bb7d` |

Replacing one: build it from the `codec2commit` above, update this table, and
say in the commit message how it was built.

## Linting and Formatting (python-only)

The python sources must be formatted and checked using `ruff`. There is a CI
workflow that blocks all PRs that do not pass the linter/formatter checks.

Ruff is pinned to 0.17.2 (`pip install .[dev]` installs that version), because
ruff versions format differently: with another version a file can pass locally
and fail in CI. Check it with `ruff --version`.

- To check formatting run `ruff format --check --preview` (lists files that need
  formatting; the same check the CI runs)
- To see what the automatic formatter will change run `ruff format --preview
--diff` (shows diff-style changes).
- To automatically format files run `ruff format --preview`

NOTE: always pass `--preview` to `ruff format`. It enables the formatting rules
the CI checks for; without it the formatter changes many files the CI accepts.

- To run the checker/linter execute `ruff check`
- Some errors can be automatically fixed running `ruff check --fix`, but always
  check and test the changes made by ruff. There are errors that ruff cannot safely
  fix automatically, you must fix them manually.

## Testing backend

To run the backend tests, run this from the repository root inside the virtual
environment (as the CI does):

```
python -m unittest discover tests
```

The whole suite takes about 5 minutes. To run one test file, name it with `-p`:

```
python -m unittest discover tests -p "test_arq_session.py"
```

Each run uses its own temporary config file and message database, so the tests
never touch your real FreeDATA settings or messages. See
`freedata_server/docs/testing.md` for how the tests work and how to write new
ones.
