#!/usr/bin/env pwsh
# Import from the Canon SD card into the E:/ and D:/ archives.
# Thin wrapper over `smart-gallery import` (replaces custom/import_canon.py).
#
# The catalog must already exist on each target drive — run
#   smart-gallery init <drive>
# once per drive before importing.

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# ── device config ────────────────────────────────────────────────────────────
$Source          = 'F:\'
$PhotoTargets    = @('E:\Photos\Canon', 'D:\Photos\Canon')
$VideoTargets    = @('E:\Videos',       'D:\Videos')
$PhotoExtensions = @('.cr3', '.jpg')
$VideoExtensions = @('.mp4')

# ── import one media type into one target subfolder ──────────────────────────
function Invoke-Import {
    param(
        [string]   $Target,
        [string[]] $Extensions,
        [ValidateSet('image', 'video')] [string] $FileType
    )
    # The catalog lives at the drive root that owns $Target (e.g. E:\ for E:\Photos\Canon).
    $root = [System.IO.Path]::GetPathRoot($Target)
    $sgArgs = @(
        'import', $Source,
        '--to', $root,
        '--output', $Target,
        '--file-types', $FileType,
        '--extensions'
    ) + $Extensions + @(
        '--no-by-media-type',
        '--structure', 'Year', 'Month',
        '--on-exist', 'skip'
    )
    & uv run --project $ProjectRoot smart-gallery @sgArgs
    if ($LASTEXITCODE -ne 0) { throw "smart-gallery import failed for $Target (exit $LASTEXITCODE)" }
}

foreach ($t in $PhotoTargets) { Invoke-Import -Target $t -Extensions $PhotoExtensions -FileType image }
foreach ($t in $VideoTargets) { Invoke-Import -Target $t -Extensions $VideoExtensions -FileType video }
