<#
.SYNOPSIS
Builds the Smart Gallery desktop app and its Windows installer.

.DESCRIPTION
1. uv sync --extra faces --extra gui
2. PyInstaller: dist\SmartGallery\ (SmartGallery.exe + smart-gallery.exe)
3. Inno Setup 6: dist\SmartGallery-Setup-<version>.exe

Run from the repository root: ./packaging/build.ps1
Needs uv and Inno Setup 6 (ISCC.exe on PATH or in its default install folder).
#>

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    # Echo the command's output and return it with the exit code. stderr is
    # merged in and must not count as a PowerShell error (Windows PowerShell 5.1).
    Write-Host "> $Exe $($Arguments -join ' ')" -ForegroundColor Cyan
    $lines = @()
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        $lines += $line
    }
    [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $lines }
}

function Find-Iscc {
    $onPath = Get-Command "iscc" -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    foreach ($candidate in @(
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            "C:\Program Files\Inno Setup 6\ISCC.exe"
        )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    # Check the tools up front so a missing one fails before the slow build.
    if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
        Fail "uv was not found on PATH. Install it from https://docs.astral.sh/uv/."
    }
    $iscc = Find-Iscc
    if (-not $iscc) {
        Fail ("Inno Setup 6 was not found (ISCC.exe is not on PATH or in " +
            "'C:\Program Files (x86)\Inno Setup 6' / 'C:\Program Files\Inno Setup 6'). " +
            "Install it from https://jrsoftware.org/isdl.php.")
    }

    $versionLine = Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"' |
        Select-Object -First 1
    if (-not $versionLine) { Fail "Could not read the version from pyproject.toml." }
    $version = $versionLine.Matches[0].Groups[1].Value

    $sync = Invoke-Native "uv" @("sync", "--extra", "faces", "--extra", "gui")
    if ($sync.ExitCode -ne 0) {
        if ($sync.Output -match "os error 32") {
            Write-Host ("Hint: a file in .venv is in use. Close running smart-gallery " +
                "windows (ui, gui, dashboard) and run the build again.") -ForegroundColor Yellow
        }
        Fail "uv sync failed (exit code $($sync.ExitCode))."
    }

    $freeze = Invoke-Native "uv" @(
        "run", "--no-sync", "pyinstaller", "packaging/smart_gallery.spec", "--noconfirm"
    )
    if ($freeze.ExitCode -ne 0) { Fail "PyInstaller failed (exit code $($freeze.ExitCode))." }

    $setup = Invoke-Native $iscc @("/DVersion=$version", "packaging\installer.iss")
    if ($setup.ExitCode -ne 0) { Fail "Inno Setup failed (exit code $($setup.ExitCode))." }

    $installer = Join-Path $root "dist\SmartGallery-Setup-$version.exe"
    if (-not (Test-Path $installer)) { Fail "Expected installer not found: $installer" }
    Write-Host "Installer: $installer" -ForegroundColor Green
}
finally {
    Pop-Location
}
