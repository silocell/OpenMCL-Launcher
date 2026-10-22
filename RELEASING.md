# Releasing

The download links on the website and in the README point at
`/releases/latest/download/<file>`. GitHub resolves `latest` to the newest
published release, so **a published release with no files attached breaks
every download link** until its build finishes. Creating the release as a
draft avoids that window entirely.

## Steps

1. Bump `__version__` in `openmcl/__init__.py`. Nothing else holds a version —
   `pyproject.toml`, the Windows resource, the macOS bundle and the installer
   all read it from there, and a test fails if a second copy appears.

2. Commit and push to `main`. Wait for the build to go green.

3. Go to **Releases → Draft a new release**.
   - Tag: `vX.Y.Z`, matching `__version__`
   - Target: `main`
   - **Tick "Set as a draft".** This is the important part.
   - Publish the draft.

4. Creating the tag starts the build. When it finishes, it attaches
   `OpenMCL-Setup.exe`, `OpenMCL-Portable-Windows.zip`, `OpenMCL.dmg`,
   `OpenMCL-x86_64.AppImage` and `SHA256SUMS`, then flips the draft to
   published and makes it `latest`.

   If any expected file is missing the job fails and publishes nothing, so a
   half-finished release never becomes `latest`.

5. Check a download: open the website and click one.

## If you published a release before its build finished

`latest` now points at an empty release and downloads 404. Either wait for the
build, or tick **Set as a pre-release** on it — `latest` falls back to the
previous release until the build attaches the files, at which point untick it.

## What a release contains

`packaging/release_files.txt` is the list. Tests check that every download
link names a file on it and that the workflow builds each one, so adding a
download means updating all three together.
