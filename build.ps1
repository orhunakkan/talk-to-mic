# Builds the Windows installer: dist\talk-to-mic-setup-<version>.exe
# Needs the .venv from the README (with requirements.txt installed) and Inno Setup 6
# (winget install JRSoftware.InnoSetup).

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
Set-Location $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"
$version = & $python -c "import talk_to_mic; print(talk_to_mic.__version__)"

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 not found; install it with: winget install JRSoftware.InnoSetup" }

Write-Host "Building talk-to-mic $version"
& $python packaging\make_icon.py build\icon.ico
& $python -m PyInstaller packaging\talk-to-mic.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller
& $iscc /Qp "/DAppVersion=$version" packaging\installer.iss

Write-Host "Installer: dist\talk-to-mic-setup-$version.exe"
