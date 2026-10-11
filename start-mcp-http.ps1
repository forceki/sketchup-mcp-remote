# Starts the SketchUp MCP server in HTTP mode, bound to this PC's Tailscale IP only.
# Usage:  powershell -ExecutionPolicy Bypass -File .\start-mcp-http.ps1 [-Port 8001]
param([int]$Port = 8001)

$ip = (& tailscale ip -4).Trim()
if (-not $ip) { Write-Error "Tailscale is not running or has no IPv4 address."; exit 1 }

$env:SKETCHUP_MCP_TRANSPORT     = "streamable-http"
$env:SKETCHUP_MCP_HOST          = $ip
$env:SKETCHUP_MCP_PORT          = "$Port"
$env:SKETCHUP_MCP_ALLOWED_HOSTS = "$($ip):*"

Set-Location $PSScriptRoot
Write-Host "Serving SketchUp MCP at http://$($ip):$Port/mcp (Tailscale only)"
& uv run sketchup-mcp
