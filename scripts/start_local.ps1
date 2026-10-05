# Local launcher: WhatsApp bridges + Telegram pilot. Started at logon from the user's Startup folder.
# Secrets stay in .local/pilot-state (token file); nothing here prints them.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$state = Join-Path $repo '.local\pilot-state'
$log = Join-Path $state 'launcher.log'
function Note($text) { Add-Content -Path $log -Value ((Get-Date -Format s) + ' ' + $text) -Encoding utf8 }

$mutex = New-Object System.Threading.Mutex($false, 'Local\market-radar-launcher')
if (-not $mutex.WaitOne(0)) { exit 0 }  # already running

function Start-Bridges {
    $config = Get-Content (Join-Path $state 'config.json') -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($bridge in $config.pilot.bridges.PSObject.Properties) {
        $port = [int]$bridge.Value.port
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { continue }
        $dir = $bridge.Value.dir
        $env:WHATSAPP_BRIDGE_PORT = "$port"
        Start-Process -FilePath (Join-Path $dir 'whatsapp-bridge.exe') -WorkingDirectory $dir -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $dir 'bridge.log') -RedirectStandardError (Join-Path $dir 'bridge.log.err') | Out-Null
        Note "bridge $($bridge.Name) started on $port"
    }
}

# Analysis archive (Postgres in Docker Desktop); the pilot works without it if Docker is down.
cmd /c "docker compose -f `"$repo\docker\local-db.compose.yml`" up -d >nul 2>&1"
Note "database compose exit $LASTEXITCODE"

while ($true) {
    try {
        Start-Bridges
        Start-Sleep 10
        $env:RADAR_TELEGRAM_TOKEN = (Get-Content (Join-Path $state 'telegram-token.txt') -Raw).Trim()
        $env:PYTHONIOENCODING = 'utf-8'
        $env:OPENCLI_WINDOW = 'background'  # automation windows stay out of the way
        $pilot = Start-Process -FilePath (Join-Path $repo '.venv\Scripts\python.exe') -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
            -ArgumentList '-m', 'radar.entrypoints.telegram_pilot', 'run', '--state-dir', '.local/pilot-state', '--max-polls', '10000' `
            -RedirectStandardOutput (Join-Path $state 'pilot.log') -RedirectStandardError (Join-Path $state 'pilot.err')
        Remove-Item Env:\RADAR_TELEGRAM_TOKEN
        $pilot.Id | Set-Content (Join-Path $state 'pilot.pid')
        Note "pilot started pid $($pilot.Id)"
        while (-not $pilot.HasExited) { Start-Sleep 30; Start-Bridges }
        Note "pilot exited $($pilot.ExitCode)"
    } catch {
        Note "launcher error: $($_.Exception.GetType().Name)"
    }
    Start-Sleep 60  # a killed pilot keeps its poll lease up to 5 min; retry until it expires
}
