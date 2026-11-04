# Security

## Reporting a vulnerability

Please report security problems privately, through GitHub's
[private vulnerability reporting](https://github.com/OH2JRB/FreeDATA/security/advisories/new)
(the **Report a vulnerability** button on the repository's Security tab). Do
not open a public issue for them.

Include what you found, how to reproduce it, and the FreeDATA version and
platform. FreeDATA is maintained in spare time; reports are acknowledged
within 14 days, and fixed problems are published as a GitHub security
advisory with the release that fixes them.

## Supported versions

Only the latest release gets security fixes. Fixes are not backported to
older releases.

| Version | Security fixes |
| --- | --- |
| latest 0.30.x | yes |
| older, and upstream DJ2LS/FreeDATA | no |

## Checking a download

Each release has `SHA256SUMS-windows.txt` and `SHA256SUMS-linux.txt` with the
SHA-256 of every file it ships, and a CycloneDX SBOM per platform. To check a
download:

```bash
sha256sum -c --ignore-missing SHA256SUMS-linux.txt
```

On Windows, compare the output of
`Get-FileHash FreeDATA-Installer.exe -Algorithm SHA256` with the line in
`SHA256SUMS-windows.txt`.

The sums show a download is complete, not who built it. For that, every
release file and the Docker image also have a signed build provenance
attestation: a record, signed through GitHub and Sigstore, that the file was
built by this repository's release workflow and from which commit. Check it
with the [GitHub CLI](https://cli.github.com):

```bash
gh attestation verify FreeDATA-Installer.exe -R OH2JRB/FreeDATA
gh attestation verify oci://ghcr.io/oh2jrb/freedata:latest -R OH2JRB/FreeDATA
```

It fails if the file was changed, or built anywhere else. The output names
the workflow and the commit, which should be the release's tag. Releases
up to 0.30.1 have no attestations. The files are not code-signed, so Windows
may still warn about the installer.

## Security model

FreeDATA is meant to run on the station computer, used by its operator.

- **Network access.** The server's API and GUI have no login. Anyone who can
  reach the server's port can key the transmitter, change the configuration
  and read, send or delete messages. The server therefore listens on
  `127.0.0.1` (this computer only) by default, and logs a warning when
  `NETWORK.modemaddress` makes it reachable from the network. Only do that on
  a network you trust, or put an HTTPS reverse proxy with a login in front of
  it. Traffic to the server is plain HTTP.
- **Web sites.** The server refuses browser requests from other web sites and
  requests that reach it by a host name it does not know (DNS rebinding), so
  a page open in the same browser cannot use the API. Names the GUI is opened
  by, other than IP addresses, `localhost` and `.local` names, are listed in
  `NETWORK.allowed_hosts`.
- **Stored data is not encrypted.** The configuration file (`config.ini`) and
  the message database (`freedata-messages.db`, with the messages,
  broadcasts, attachments, stations and beacons) are plain files in the user's
  FreeDATA folder: `%APPDATA%\FreeDATA` on Windows,
  `~/Library/Application Support/FreeDATA` on macOS, `~/.config/FreeDATA` on
  Linux, or the paths in `FREEDATA_CONFIG` and `FREEDATA_DATABASE`. Messages
  sent or received with per-station encryption are encrypted only over the
  air; they are stored decrypted. Anyone who can read the user's files can
  read them. This is a deliberate choice for a single-user station computer;
  protect the files with the operating system's account and disk encryption
  if needed.
- **Message encryption keys** are kept only in the server's memory and are
  never written to disk.
