# Builds Leos Lyssnare for Windows. Run in PowerShell from the desktop\ folder:
#   powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
#
# Needs: Python 3.10+ (64-bit) from python.org. For the installer, also
# Inno Setup 6 (https://jrsoftware.org/isinfo.php, or: winget install JRSoftware.InnoSetup).
# Without Inno Setup you still get a portable .zip.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path
$Work = Join-Path $Root "build\windows"
New-Item -ItemType Directory -Force $Work | Out-Null

Write-Host "> Python environment..."
$Venv = Join-Path $Work "venv"
if (-not (Test-Path $Venv)) { python -m venv $Venv }
$Python = Join-Path $Venv "Scripts\python.exe"
& $Python -m pip install --upgrade pip wheel
& $Python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "> Icons..."
$env:QT_QPA_PLATFORM = "offscreen"
& $Python packaging\make_icons.py
Remove-Item Env:QT_QPA_PLATFORM

Write-Host "> PyInstaller..."
& $Python -m PyInstaller --noconfirm --clean --distpath "$Work\dist" --workpath "$Work\pyinstaller" packaging\leoslyssnare.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Version = (& $Python -c "import leoslyssnare; print(leoslyssnare.__version__)").Trim()

Write-Host "> Portable zip..."
$Zip = Join-Path $Root "build\Leos_Lyssnare-$Version-windows-x64.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path "$Work\dist\LeosLyssnare" -DestinationPath $Zip

$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
    foreach ($Candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $Candidate) { $Iscc = $Candidate; break }
    }
}
if ($Iscc) {
    Write-Host "> Installer..."
    & $Iscc "/DAppVersion=$Version" "/DSourceDir=$Work\dist\LeosLyssnare" "/O$Root\build" packaging\windows-installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Done: build\Leos_Lyssnare-$Version-windows-x64-setup.exe"
} else {
    Write-Host "Inno Setup not found, skipping the installer."
}
Write-Host "Done: $Zip"
