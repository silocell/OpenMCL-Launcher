; OpenMCL Windows installer
;
; Built with NSIS (makensis). The compiler runs on Linux as well as Windows,
; so this is checked on every build rather than only on release.
;
;   cd packaging && makensis installer.nsi
;
; The version is read from openmcl/__init__.py automatically. Override it with
; -DVERSION=x.y.z and the source binary with -DSOURCE_EXE=path if needed.

Unicode true

!define APPNAME        "OpenMCL"
!define COMPANY        "OpenMCL"
!define DESCRIPTION    "Minecraft launcher"
!define HELPURL        "https://silocell.github.io/OpenMCL-Launcher/"
!define UPDATEURL      "https://github.com/silocell/OpenMCL-Launcher/releases"
!define ABOUTURL       "https://github.com/silocell/OpenMCL-Launcher"
!define REGKEY         "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"

; Read the version straight out of the source rather than taking it on the
; command line: passing it through a shell meant quoting it correctly in bash,
; PowerShell and cmd, and getting that wrong broke the build twice.
!ifndef VERSION
  !searchparse /file "..\openmcl\__init__.py" '__version__ = "' VERSION '"'
!endif
!ifndef VERSION
  !error "Could not read __version__ from openmcl/__init__.py"
!endif
!ifndef SOURCE_EXE
  !define SOURCE_EXE "..\dist\OpenMCL.exe"
!endif

Name "${APPNAME}"
OutFile "..\dist\OpenMCL-Setup.exe"
; Per-user by default: no administrator prompt, and it keeps the launcher out
; of Program Files where it would need elevation to update itself.
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
InstallDirRegKey HKCU "Software\${APPNAME}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName"     "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} installer"
VIAddVersionKey "FileVersion"     "${VERSION}"
VIAddVersionKey "ProductVersion"  "${VERSION}"
VIAddVersionKey "CompanyName"     "${COMPANY}"
VIAddVersionKey "LegalCopyright"  "GPL-3.0. Not affiliated with Mojang or Microsoft."

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON   "icons\openmcl.ico"
!define MUI_UNICON "icons\openmcl.ico"

; ---- Welcome
!define MUI_WELCOMEPAGE_TITLE "Install ${APPNAME} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "${APPNAME} keeps every Minecraft pack in its own box: separate worlds, mods and Java, so updating one never breaks another.$\r$\n$\r$\nYou will need a Microsoft account that owns Minecraft: Java Edition.$\r$\n$\r$\nThis installs for your account only, so no administrator password is needed."
!insertmacro MUI_PAGE_WELCOME

; ---- Licence
!define MUI_LICENSEPAGE_TEXT_TOP "${APPNAME} is free software."
!define MUI_LICENSEPAGE_TEXT_BOTTOM "${APPNAME} is released under the GNU General Public License v3. You are free to use, study, share and modify it. Click Next to continue."
!define MUI_LICENSEPAGE_BUTTON "Next"
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"

; ---- Components
!insertmacro MUI_PAGE_COMPONENTS

; ---- Location
!insertmacro MUI_PAGE_DIRECTORY

; ---- Install
!insertmacro MUI_PAGE_INSTFILES

; ---- Finish
!define MUI_FINISHPAGE_RUN "$INSTDIR\OpenMCL.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APPNAME} now"
!define MUI_FINISHPAGE_LINK "Set up Microsoft sign-in (needed before you can play)"
!define MUI_FINISHPAGE_LINK_LOCATION "https://github.com/silocell/OpenMCL-Launcher#set-up-sign-in"
!define MUI_FINISHPAGE_NOREBOOTSUPPORT
!insertmacro MUI_PAGE_FINISH

; ---- Uninstaller
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ------------------------------------------------------------------ sections

Section "${APPNAME} (required)" SecCore
  SectionIn RO
  SetOutPath "$INSTDIR"
  File "/oname=OpenMCL.exe" "${SOURCE_EXE}"
  File "/oname=LICENSE.txt" "..\LICENSE"

  WriteRegStr HKCU "Software\${APPNAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${APPNAME}" "Version" "${VERSION}"

  ; Start menu entry, so it is findable by typing its name.
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\OpenMCL.exe" "" "$INSTDIR\OpenMCL.exe" 0
  CreateShortcut "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk" "$INSTDIR\Uninstall.exe"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Add/Remove Programs
  WriteRegStr   HKCU "${REGKEY}" "DisplayName"     "${APPNAME}"
  WriteRegStr   HKCU "${REGKEY}" "DisplayVersion"  "${VERSION}"
  WriteRegStr   HKCU "${REGKEY}" "DisplayIcon"     "$INSTDIR\OpenMCL.exe"
  WriteRegStr   HKCU "${REGKEY}" "Publisher"       "${COMPANY}"
  WriteRegStr   HKCU "${REGKEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr   HKCU "${REGKEY}" "QuietUninstallString" "$\"$INSTDIR\Uninstall.exe$\" /S"
  WriteRegStr   HKCU "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "${REGKEY}" "HelpLink"        "${HELPURL}"
  WriteRegStr   HKCU "${REGKEY}" "URLUpdateInfo"   "${UPDATEURL}"
  WriteRegStr   HKCU "${REGKEY}" "URLInfoAbout"    "${ABOUTURL}"
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${REGKEY}" "EstimatedSize" "$0"
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\OpenMCL.exe" "" "$INSTDIR\OpenMCL.exe" 0
SectionEnd

LangString DESC_SecCore    ${LANG_ENGLISH} "The launcher itself, a Start menu entry and an uninstaller."
LangString DESC_SecDesktop ${LANG_ENGLISH} "Put a shortcut on your desktop."

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore}    $(DESC_SecCore)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_SecDesktop)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; --------------------------------------------------------------- uninstaller

Section "Uninstall"
  Delete "$INSTDIR\OpenMCL.exe"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"

  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk"
  RMDir  "$SMPROGRAMS\${APPNAME}"
  Delete "$DESKTOP\${APPNAME}.lnk"

  DeleteRegKey HKCU "${REGKEY}"
  DeleteRegKey HKCU "Software\${APPNAME}"

  ; Worlds, mods and saved accounts live in %APPDATA%\OpenMCL. Never remove
  ; them silently - someone reinstalling would lose every world they have.
  IfFileExists "$APPDATA\${APPNAME}\*.*" 0 done
    MessageBox MB_YESNO|MB_ICONEXCLAMATION|MB_DEFBUTTON2 \
      "Also delete your instances, worlds, mods and saved accounts?$\r$\n$\r$\nThey are in:$\r$\n$APPDATA\${APPNAME}$\r$\n$\r$\nChoose No to keep them for a future reinstall." \
      IDNO done
    RMDir /r "$APPDATA\${APPNAME}"
  done:
SectionEnd
