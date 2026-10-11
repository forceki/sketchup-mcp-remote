# Builds su_mcp.rbz (the SketchUp extension installer) from the ./su_mcp folder.
# Usage:  powershell -ExecutionPolicy Bypass -File .\build-rbz.ps1
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem

$src = Join-Path $PSScriptRoot "su_mcp"
$out = Join-Path $PSScriptRoot "su_mcp.rbz"
if (Test-Path $out) { Remove-Item $out -Force }

$zip = [System.IO.Compression.ZipFile]::Open($out, "Create")
try {
    Get-ChildItem $src -Recurse -File | Where-Object { $_.Name -ne "package.rb" } | ForEach-Object {
        $entry = $_.FullName.Substring($src.Length + 1).Replace("\", "/")   # forward slashes inside the zip
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $entry)
    }
} finally { $zip.Dispose() }

Write-Host "Built $out"
$z = [System.IO.Compression.ZipFile]::OpenRead($out); $z.Entries | ForEach-Object { "  " + $_.FullName }; $z.Dispose()
