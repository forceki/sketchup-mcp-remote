# SketchUp connector installer for Windows.
# Served by the website with the server address and pairing code filled in. Run:
#   irm "https://your-site/install.ps1?code=ABCD-EFGH" | iex
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Server  = "__SERVER__"
$Code    = "__CODE__"
$RepoZip = "__REPO_ZIP__"
$Dir     = Join-Path $env:USERPROFILE "sketchup-connector"

Write-Host ""
Write-Host "=== SketchUp connector installer ===" -ForegroundColor Cyan

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return ,@($cmd.Source) }
    foreach ($p in @("$env:USERPROFILE\.local\bin\uv.exe", "$env:USERPROFILE\.cargo\bin\uv.exe")) {
        if (Test-Path $p) { return ,@($p) }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -m uv --version *> $null
        if ($LASTEXITCODE -eq 0) { return ,@($py.Source, "-m", "uv") }
    }
    return $null
}

# 1. uv (runs Python and installs what the connector needs)
$uv = Find-Uv
if (-not $uv) {
    Write-Host "[1/5] Installing uv..."
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $uv = Find-Uv
    if (-not $uv) { throw "Could not install uv. Install it from https://docs.astral.sh/uv/ and run this again." }
} else {
    Write-Host "[1/5] uv found"
}
$uvExe = $uv[0]
$uvPre = @($uv | Select-Object -Skip 1)
function Invoke-Uv { & $uvExe @uvPre @args }

# 2. connector (stop an older copy first: Windows locks files that are running)
Write-Host "[2/5] Downloading the connector to $Dir"
Get-CimInstance Win32_Process | Where-Object {
    ($_.ExecutablePath -and $_.ExecutablePath -like "$Dir\*") -or ($_.CommandLine -and $_.CommandLine -like "*$Dir\*")
} | Where-Object { $_.ProcessId -ne $PID } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
New-Item -ItemType Directory -Force $Dir | Out-Null
Invoke-WebRequest -UseBasicParsing "$Server/connector/connector.py" -OutFile (Join-Path $Dir "connector.py")

# 3. SketchUp MCP server + SketchUp extension (.rbz)
Write-Host "[3/5] Downloading the SketchUp MCP server"
$tmp = Join-Path $env:TEMP ("skc-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null
Invoke-WebRequest -UseBasicParsing $RepoZip -OutFile (Join-Path $tmp "mcp.zip")
Expand-Archive (Join-Path $tmp "mcp.zip") -DestinationPath $tmp -Force
$src = Get-ChildItem $tmp -Directory | Select-Object -First 1
$mcpDir = Join-Path $Dir "sketchup-mcp-remote"
if (Test-Path $mcpDir) {
    # update in place and keep the already-built .venv
    Get-ChildItem $mcpDir -Force | Where-Object { $_.Name -ne ".venv" } | Remove-Item -Recurse -Force
    Get-ChildItem $src.FullName -Force | Copy-Item -Destination $mcpDir -Recurse -Force
} else {
    Move-Item $src.FullName $mcpDir
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

# 4. pair with the website
if ($Code) {
    Write-Host "[4/5] Pairing this computer"
    Invoke-Uv run (Join-Path $Dir "connector.py") pair --server $Server --code $Code
    if ($LASTEXITCODE -ne 0) { throw "Pairing failed. Click 'Add computer' on the website for a new code and run this again." }
} else {
    Write-Host "[4/5] No code given; skipping pairing"
}

# 5. start shortcut on the Desktop
Write-Host "[5/5] Creating the 'Start SketchUp Connector' shortcut"
$uvLine = (@($uvExe) + $uvPre | ForEach-Object { '"' + $_ + '"' }) -join " "
$cmdText = "@echo off`r`ntitle SketchUp Connector`r`ncd /d `"$Dir`"`r`n$uvLine run connector.py run`r`npause`r`n"
$startCmd = Join-Path $Dir "Start SketchUp Connector.cmd"
[IO.File]::WriteAllText($startCmd, $cmdText)
$desktop = [Environment]::GetFolderPath("Desktop")
if ($desktop) { Copy-Item $startCmd (Join-Path $desktop "Start SketchUp Connector.cmd") -Force }

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "If SketchUp doesn't have the extension yet: Window > Extension Manager > Install Extension, pick:"
Write-Host "    $mcpDir\su_mcp.rbz"
Write-Host "Then in SketchUp: Extensions > MCP Server > Start Server."
Write-Host "Next time, start the connector with the 'Start SketchUp Connector' icon on your Desktop."
Write-Host ""
Write-Host "Starting the connector now (close this window to stop it)..." -ForegroundColor Cyan
Set-Location $Dir
Invoke-Uv run connector.py run
