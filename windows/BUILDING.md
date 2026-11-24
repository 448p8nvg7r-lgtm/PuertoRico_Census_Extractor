# Building the Windows installer

The result is **`CensusExtractor-Setup-1.0.1.exe`**, a normal Windows installer.
The people you give it to **don't need Python or anything else installed**.

There are two ways to build it. Pick one.

---

## Option 1: Build on your Windows PC (about 10 minutes the first time)

**You need:**
- Windows 10 or 11, 64-bit.
- **Python 3.9+, 64-bit**, from <https://www.python.org/downloads/windows/>.
  In the installer, tick **"Add python.exe to PATH"**.
  (Or run `winget install Python.Python.3.12` in a terminal.)
- **Inno Setup 6.3+**, the free installer builder. The build script installs it
  automatically with `winget` if it's missing, or you can get it from
  <https://jrsoftware.org/isdl.php>.

**Steps:**
1. Unzip the project somewhere, for example `C:\Users\you\census-extractor`.
2. Open the `windows` folder and **double-click `build_windows.bat`**.
3. Wait. The script:
   - creates an isolated build environment (`.venv-build`)
   - bundles Python, the app and its libraries with PyInstaller
   - tests the bundled program
   - compiles the installer with Inno Setup
4. Your installer is at **`dist\installer\CensusExtractor-Setup-1.0.1.exe`**.

**Just want a single .exe file?** Run this in the `windows` folder:
```
build_windows.bat -OneFile
```
You get **`dist\onefile\census-extract.exe`**, one self-contained file (about
100–200 MB) with no installer. Copy it anywhere, such as your desktop, a USB
stick or a shared drive, and double-click it. Python isn't needed. Each start
takes 10–30 seconds longer because the file unpacks itself to a temp folder
first. The installer version starts faster.

Useful extras:
- **Portable version, no installer:** run `build_windows.bat -SkipInstaller`, then zip
  `dist\census-extract\` and share it. People run `census-extract.exe` inside it.
- **Rebuild from scratch:** delete the `build`, `dist` and `.venv-build` folders first.

## Option 2: Let GitHub build it (no tools on your PC)

1. Create a GitHub repository and upload the whole project folder, including
   the hidden `.github` folder.
2. On the repo page, go to **Actions → Build Windows installer → Run workflow**.
3. After about 10 minutes, open the finished run and download
   **CensusExtractor-Setup** under *Artifacts*. It's a zip containing the `.exe`.

If you push a version tag (for example `git tag v1.0.0 && git push --tags`), the
installer is also attached to a GitHub Release automatically.

---

## What the installer does

- Installs to `%LOCALAPPDATA%\Programs\Census Extractor` **without admin rights**.
  Admins are offered an "install for all users" choice.
- Adds Start-menu shortcuts:
  - **Census Variable Extractor** opens the app in your browser.
  - **…Command Prompt** opens a terminal where `census-extract` commands work.
  - **…Instructions** opens the README.
- Optional: a desktop shortcut, and adding `census-extract` to PATH.
- Optional page: **saves your Census API key** to your Windows user environment,
  so the app fills it in automatically.
- Registers an uninstaller under *Settings → Apps*. Uninstalling also removes the
  PATH entry. Your saved API key and downloaded CSVs are left alone.

When the app is running, a console window shows the server log. **Closing that
window quits the app.** The app only listens on your own computer
(`127.0.0.1`), so it isn't reachable from your network.

## Releasing a new version

1. Change `__version__` in `census_extractor/__init__.py` (and `version` in `pyproject.toml`).
2. Rebuild. The installer file name and Windows "Apps" entry pick up the new version.
3. Installing a new version over an old one upgrades in place, because the `AppId` in
   `installer.iss` stays the same. **Never change the AppId.**

## "Windows protected your PC" (SmartScreen)

New, unsigned installers trigger this warning. Users click **More info → Run anyway**.
To get rid of the warning for everyone, sign the installer and the exe with a
code-signing certificate. Inno Setup supports this through its `SignTool`
setting, and Azure Trusted Signing is a lower-cost option. Some antivirus tools
are also cautious about new PyInstaller apps. Signing helps with that too.

## Troubleshooting the build

| Problem | Fix |
|---|---|
| "64-bit Python 3.9+ not found" | Install Python and tick *Add to PATH*, or pass `-Python "C:\Path\to\python.exe"` |
| "running scripts is disabled" | Use `build_windows.bat`, which bypasses the policy for this script only |
| App window flashes and closes | Run `dist\census-extract\census-extract.exe` from a Command Prompt to see the error |
| Browser shows "No module named …" | Add `"--collect-all", "<module>"` to the PyInstaller list in `build_windows.ps1` and rebuild |
| Inno Setup error about `x64compatible` | Update to Inno Setup 6.3 or newer |
| "Failed when searching source: winget" (0x8a15000f) | winget is broken on that PC. The script now downloads Inno Setup directly instead. Or install it from jrsoftware.org yourself, then run `build_windows.bat -InstallerOnly` |
