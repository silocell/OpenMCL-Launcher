# Building OpenMCL into an executable

PyInstaller does not cross-compile. **A Windows `.exe` has to be built on
Windows**, a `.app` on macOS, a Linux binary on Linux. There are three ways to
get one; pick whichever suits you.

## 1. GitHub Actions — no Windows machine needed

The repository includes `.github/workflows/build.yml`, which builds all three
platforms on GitHub's runners.

1. Push this project to a GitHub repository.
2. Open the **Actions** tab. The workflow runs on every push, and you can also
   start it by hand with **Run workflow**.
3. When it finishes, download **OpenMCL-windows** from the run's *Artifacts*
   section. Inside is `OpenMCL.exe`.

Tag a commit `v0.1.0` and push the tag to get a GitHub Release with all three
binaries attached instead.

The workflow runs the test suite first and refuses to build if it fails, then
smoke-tests each binary by launching it.

## 2. On Windows yourself

Install [Python 3.10+](https://www.python.org/downloads/windows/), ticking
**Add python.exe to PATH** during setup. Then from the project root:

```bat
packaging\build_windows.bat
```

That installs the dependencies, builds, and smoke-tests the result. You get
`dist\OpenMCL.exe` — one self-contained file, no Python needed on the machine
you run it on.

## 3. On macOS or Linux

```bash
packaging/build.sh
```

Produces `dist/OpenMCL.app` or `dist/OpenMCL`.

## One file or one folder?

The default is a single file, which is what people usually want from an
`.exe` — but it unpacks itself to a temporary directory on every launch, so it
takes roughly 5–10 seconds to start. A folder build starts in about a second:

```bat
packaging\build_windows.bat onedir
```
```bash
packaging/build.sh onedir
```

The result is `dist\OpenMCL\` — ship the whole folder, and run `OpenMCL.exe`
inside it.

## Expected size

Around 70–90 MB. Most of that is Qt. The spec file already excludes the Qt
modules a launcher has no use for (WebEngine, Quick, Multimedia, 3D, Charts,
SQL and others), which cuts roughly 300 MB. `QtSvg` is deliberately kept — the
interface draws its combo-box and spin-box arrows from generated SVG files and
they vanish without it.

## Signing

The build is unsigned, so Windows SmartScreen shows "Windows protected your
PC" on first run — *More info* → *Run anyway*. macOS does the same via
Gatekeeper: right-click the app → *Open*. Signing needs a paid certificate
(an Authenticode certificate for Windows, an Apple Developer ID for macOS);
if you have one, add `codesign_identity` to `openmcl.spec` for macOS, or run
`signtool` over `dist\OpenMCL.exe` for Windows.

## Verifying a build

Every binary accepts two flags that need no account and touch no network:

```bat
dist\OpenMCL.exe --version
dist\OpenMCL.exe --selftest
```

`--selftest` starts the interface, builds every page, confirms the icon and the
SVG plugin made it into the bundle, and exits. It is what the build scripts and
CI run, and it catches the usual packaging failures before you find them by
double-clicking.

## Changing the icon

Edit `packaging/make_icon.py` and run it (`pip install pillow` first). It
regenerates the `.ico`, `.icns` and `.png` the builds use.

## Where settings go

A frozen build stores data in the same place as running from source
(`%APPDATA%\OpenMCL` on Windows), so you can switch between them freely. Set
`OPENMCL_HOME` to keep everything next to the executable instead, which makes
it portable on a USB stick:

```bat
set OPENMCL_HOME=%~dp0data
```
