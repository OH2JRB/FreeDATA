# FreeDATA

> [!NOTE]
> This is OH2JRB's fork of [DJ2LS/FreeDATA](https://github.com/DJ2LS/FreeDATA). It started from upstream 0.18.2 and continues as version 0.30.

> [!WARNING]
> FreeDATA 0.30 uses radio protocol version 2 and cannot exchange data with stations running a version before 0.30, including upstream 0.18.x. Both stations need 0.30 or newer. Stations on an older version are shown with a red background in the Heard stations list.

> FreeDATA is a versatile, **open-source platform designed specifically for HF communications**, leveraging **codec2** data modes for robust global digital communication. It features a network-based server-client architecture, a REST API, multi-platform compatibility, and a messaging system.

> Please keep in mind, this project is still **under development** with many issues which need to be solved.

[![Modem tests](https://github.com/OH2JRB/FreeDATA/actions/workflows/modem_tests.yml/badge.svg)](https://github.com/OH2JRB/FreeDATA/actions/workflows/modem_tests.yml)
[![GUI tests](https://github.com/OH2JRB/FreeDATA/actions/workflows/gui_tests.yml/badge.svg)](https://github.com/OH2JRB/FreeDATA/actions/workflows/gui_tests.yml)

![FreeDATA_main_screen.png](documentation%2FFreeDATA_main_screen.png)

![FreeDATA_chat_screen.png](documentation%2FFreeDATA_chat_screen.png)

## Installation

Download a release from this fork's [Releases](https://github.com/OH2JRB/FreeDATA/releases) page:

- **Windows:** `FreeDATA-Installer.exe`
- **Linux (x86-64):** `FreeDATA-x86_64.AppImage`

Other ways to install:

- **Linux, including Raspberry Pi:** [tools/Linux/install-freedata-linux.sh](tools/Linux/install-freedata-linux.sh) builds codec2 and Hamlib for the machine and installs from this fork
- **macOS:** [tools/macOS](tools/macOS)
- **Docker:** `ghcr.io/oh2jrb/freedata`, see [README.docker.md](README.docker.md)

The server serves the GUI at `http://127.0.0.1:5000/gui`. For setting up and using FreeDATA, see the upstream [wiki](https://wiki.freedata.app).

## Credits

- DJ2LS and the FreeDATA contributors for the original project - https://github.com/DJ2LS/FreeDATA
- David Rowe and the FreeDV team for developing the modem and libraries -
  FreeDV Codec 2 : https://github.com/drowe67/codec2
