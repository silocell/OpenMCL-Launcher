# OpenMCL

A Minecraft launcher in the Prism/MultiMC mould: isolated instances, Fabric /
Quilt / NeoForge / Forge, a built-in Modrinth and CurseForge browser, modpack
import, and a themeable interface. Sign-in is real Microsoft OAuth against a
Microsoft account that owns Minecraft: Java Edition.

![Instances](docs/shot-instances.png)

## What it does

**Instances** — each one is a self-contained `.minecraft` with its own worlds,
mods, configs, Java path, memory and window settings. Duplicate them, export
them, delete them without touching anything else. Versions, libraries and
assets are shared between instances, so a second instance on the same version
downloads nothing.

**Accounts** — Microsoft device-code sign-in: the launcher shows a code, you
approve it on Microsoft's own page, and no password is ever typed into this
app. Tokens refresh automatically before a launch. Entitlement and profile are
both verified. There is no offline/cracked mode and there won't be one.

**Loaders** — Fabric and Quilt install from their own metadata APIs. NeoForge
and Forge are installed by downloading the official installer and running its
processors locally, the same work the official installer does, so patched jars
end up where the loader expects them.

**Mods** — search Modrinth and CurseForge side by side, filtered to the
instance's Minecraft version and loader. Installing a mod pulls its required
dependencies too. "Check for updates" compares every tracked mod against the
newest build. Mods added to the folder by hand show up in the list and can be
enabled, disabled or removed like any other.

**Modpacks** — import `.mrpack` (Modrinth) and CurseForge `.zip` packs, which
creates the instance, installs the right loader, downloads the files and
applies the overrides. Export an instance back out as a `.mrpack`.

**Appearance** — three themes, any accent colour, adjustable font size.

## Requirements

- Python 3.10+
- A Java runtime. The launcher finds installed ones (`JAVA_HOME`, `PATH`,
  the usual install directories, and runtimes the official launcher installed)
  and picks the one the chosen Minecraft version asks for. Modern versions
  want Java 21; 1.17–1.20.4 want 17; older ones want 8.
- A Microsoft account that owns Minecraft: Java Edition.

## Install and run

```bash
pip install -r requirements.txt
python run.py
```

On first run, open **Settings** and fill in the Microsoft client ID below.

## Building a standalone executable

To get an `OpenMCL.exe` that runs without Python installed:

| You have | Do this | Result |
| --- | --- | --- |
| A Windows machine | `packaging\build_windows.bat` | `dist\OpenMCL.exe` |
| macOS or Linux | `packaging/build.sh` | `dist/OpenMCL.app` or `dist/OpenMCL` |
| Neither, or you want all three | Push to GitHub; the included Actions workflow builds Windows, macOS and Linux and attaches them to the run | download from *Actions → Artifacts* |

PyInstaller cannot cross-compile, so a Windows executable has to be built on
Windows — the GitHub Actions route exists precisely because it gives you a
Windows runner for free. Full details, including the one-file vs one-folder
trade-off and what to expect from unsigned binaries, are in
[`packaging/README.md`](packaging/README.md).

Any build can be checked without signing in:

```bash
dist/OpenMCL --version
dist/OpenMCL --selftest    # builds every page, verifies bundled assets, exits
```

## Microsoft sign-in setup

Minecraft's auth API only accepts registered application IDs, so the launcher
cannot ship with one — you register your own. It is free and takes a couple of
minutes.

1. Go to the [Azure portal](https://portal.azure.com/) and sign in.
2. **Microsoft Entra ID → App registrations → New registration**.
3. Give it any name. Under *Supported account types* choose
   **Personal Microsoft accounts only**. Leave the redirect URI blank — the
   device-code flow does not use one.
4. Register, then copy the **Application (client) ID** from the overview page.
5. Open **Authentication**, scroll to *Advanced settings*, and set
   **Allow public client flows** to **Yes**. Save. Without this the device-code
   flow is rejected.
6. Paste the client ID into OpenMCL's **Settings → Microsoft client ID**.

**Approval.** A newly registered Azure app has to be approved before Minecraft's
API will accept it — until then the API answers `403`. Apply using the form at
<https://aka.ms/mce-reviewappid>. This applies whether the launcher is for you
alone or for other people, so submit it as soon as you have the client ID.

If you are distributing the launcher to others, use an application ID you
registered and had approved yourself. Reusing another project's ID attributes
every one of your users' sign-ins to their app registration, which is both rude
and fragile.

The scope requested is `XboxLive.signin offline_access` — enough to prove you
own the game and to refresh the session later, and nothing more.

## CurseForge setup

Modrinth needs no key. CurseForge does: get one free from the
[CurseForge console](https://console.curseforge.com/) and paste it into
**Settings → CurseForge key**. Using it means accepting CurseForge's
[third-party API terms](https://support.curseforge.com/en/support/solutions/articles/9000207405).

Some CurseForge projects have third-party downloads switched off by their
authors. Those cannot be downloaded through any launcher; OpenMCL tells you
which ones and links to the project page so you can fetch the jar yourself and
drop it in the instance's mods folder.

## Where things live

```
<data>/
  settings.json          launcher settings
  accounts.json          account tokens, mode 0600
  instances/<id>/
    instance.json        version, loader, overrides, mod records
    minecraft/           the game directory: saves, mods, configs, screenshots
  shared/
    versions/            version JSONs and client jars
    libraries/           maven-layout library cache
    assets/              hashed asset store, shared by every instance
    natives/             unpacked native libraries, per version
  cache/                 version manifest, installers, mod icons
  logs/                  launcher log and crash logs
```

`<data>` is `%APPDATA%\OpenMCL` on Windows,
`~/Library/Application Support/OpenMCL` on macOS, and
`~/.local/share/openmcl` on Linux. Set `OPENMCL_HOME` to override it — useful
for keeping a portable copy on a USB stick.

## Notes and limits

- Forge below 1.13 uses the old installer format; it is handled, but those
  versions are much less tested than modern ones.
- The installer-processor step for Forge/NeoForge runs Java locally and can
  take a minute or two on first install. It is idempotent: already-correct
  outputs are skipped on reinstall.
- CurseForge paging stops at 10,000 results, which is their API limit.
- Modpack imports refuse archive entries whose paths escape the instance
  directory.
- Pre-1.7 asset layouts (`virtual` and `map_to_resources`) are materialised
  into loose files so old versions find their sounds and language files.

## Tests

```bash
pip install pytest
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

The suite covers the parts where a wrong answer fails silently: maven
coordinate mapping, rule evaluation, argument substitution, loader profile
inheritance, classpath ordering, launch-command construction for both modern
and legacy argument formats, archive path-traversal refusal, and the provider
response mappings.

## Licence and trademarks

Not affiliated with Mojang, Microsoft, Modrinth or CurseForge. Minecraft is a
trademark of Mojang Synergies AB. This launcher downloads the game from
Mojang's own servers and requires a legitimate account that owns it.
