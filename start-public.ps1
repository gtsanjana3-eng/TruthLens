# TruthLens – make the Vercel website work from this computer.
#
#   Right-click this file -> "Run with PowerShell"   (or:  powershell -ExecutionPolicy Bypass -File start-public.ps1)
#
# 1. starts the backend (API + AI models) if it is not already running,
# 2. opens a free Cloudflare tunnel to it (the tunnel address changes on every start),
# 3. redeploys the Vercel website pointing at the new tunnel address.
# The Vercel link itself never changes. Keep this computer on while people use the site.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$tools = Join-Path $root "tools"
$cloudflared = Join-Path $tools "cloudflared.exe"
$log = Join-Path $tools "tunnel.log"
$vercel = "vercel@65.12.0"

function Test-Backend { try { (Invoke-WebRequest "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { $false } }

if (-not (Test-Path $cloudflared)) { throw "tools\cloudflared.exe is missing." }

# 1. backend
if (Test-Backend) { Write-Host "Backend already running." }
else {
  Write-Host "Starting backend..."
  Start-Process -FilePath (Join-Path $backend ".venv\Scripts\python.exe") -WorkingDirectory $backend -WindowStyle Minimized `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
  $t = 0
  while (-not (Test-Backend)) { Start-Sleep 3; $t += 3; if ($t -gt 240) { throw "Backend did not start (check the Supabase connection)." } }
  Write-Host "Backend is up."
}

# 2. tunnel (replace any tunnel this script started before)
Get-Process cloudflared -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $cloudflared } | Stop-Process -Force
Remove-Item $log -ErrorAction SilentlyContinue
Start-Process -FilePath $cloudflared -WindowStyle Minimized -RedirectStandardError $log `
  -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8000"
$api = $null; $t = 0
while (-not $api) {
  Start-Sleep 2; $t += 2
  if (Test-Path $log) { $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1; if ($m) { $api = $m.Matches[0].Value } }
  if ($t -gt 90) { throw "Tunnel did not start; see tools\tunnel.log" }
}
Write-Host "Tunnel: $api"
$t = 0
# curl.exe (built into Windows) falls back from IPv6 to IPv4; Windows PowerShell's web client can hang on IPv6.
while ((& curl.exe -s -m 10 -o NUL -w "%{http_code}" "$api/api/health") -ne "200") { Start-Sleep 3; $t += 3; if ($t -gt 120) { throw "Tunnel not reachable yet; run again." } }

# 3. redeploy the website with the new backend address (the Vercel link stays the same)
Write-Host "Updating the Vercel website..."
Push-Location (Join-Path $root "frontend")
# Run through cmd so informational lines on stderr are not treated as PowerShell errors; judge by the exit code.
try { cmd /c "npx --yes $vercel deploy --prod --yes --build-env VITE_API_BASE=$api 2>&1" | Out-Host; $code = $LASTEXITCODE } finally { Pop-Location }
if ($code -ne 0) { throw "Vercel deployment failed (exit code $code)." }
Write-Host ""
Write-Host "Done. Your public site is live at your Vercel address. Keep this computer on." -ForegroundColor Green
