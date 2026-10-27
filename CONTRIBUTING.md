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
