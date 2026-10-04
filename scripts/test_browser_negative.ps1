param(
    [string]$Image = 'market-radar-lab/browser:local',
    [ValidateRange(64,2048)][int]$MemoryMiB = 1024
)

# Synthetic-only smoke cases; never reads account state or publishes a port.
$ErrorActionPreference = 'Stop'
$container = 'market-radar-negative-' + [guid]::NewGuid().ToString('N')
$created = $false
$checks = @()
try {
    $null = & docker run --detach --name $container --label market-radar.fixture-only=true --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory "${MemoryMiB}m" --memory-swap "${MemoryMiB}m" --cpus 1 --pids-limit 256 --tmpfs /tmp:rw,exec,nosuid,nodev,size=768m $Image
    if ($LASTEXITCODE -ne 0) { throw 'negative_container_start_failed' }
    $created = $true
    $ready = 'const http=require("http"); const end=Date.now()+15000; function p(){const r=http.get("http://127.0.0.1:8080/",s=>{s.resume();process.exit(0)});r.on("error",()=>{if(Date.now()>end)process.exit(1);setTimeout(p,100)});r.setTimeout(500,()=>r.destroy())}p();'
    $null = & docker exec $container node -e $ready
    if ($LASTEXITCODE -ne 0) { throw 'negative_runtime_not_ready' }
    foreach ($mode in @('cookies_only', 'none')) {
        $event = @{ schema_version = 1; suite = 'browser'; fixture_only = $true; mode = 'direct'; batch = 1; session_mode = $mode }
        $raw = $event | ConvertTo-Json -Compress | & docker exec -i $container node /var/task/lab/browser/invoke.mjs
        $invokeExit = $LASTEXITCODE
        $result = $raw | ConvertFrom-Json
        $pass = $invokeExit -ne 0 -and $result.status -eq 'failed' -and $result.error.code -eq 'portable_session_or_extraction_failed' -and $result.useful_records -eq 0 -and $result.checks.cleanup_complete -eq $true -and $result.checks.external_requests_blocked -eq 0
        $checks += @{ case = $mode; expected_failure = $pass; error_code = $result.error.code; useful_records = $result.useful_records; cleanup_complete = $result.checks.cleanup_complete }
    }
    $event = @{ schema_version = 1; suite = 'browser'; fixture_only = $false }
    $raw = $event | ConvertTo-Json -Compress | & docker exec -i $container node /var/task/lab/browser/invoke.mjs
    $invokeExit = $LASTEXITCODE
    $result = $raw | ConvertFrom-Json
    $checks += @{ case = 'reject_nonfixture'; expected_failure = ($invokeExit -ne 0 -and $result.error.code -eq 'requires_schema_1_browser_fixture_only'); error_code = $result.error.code }
} finally {
    if ($created) {
        # The target is exactly the unique container created above, never a glob.
        $null = & docker rm --force $container
        if ($LASTEXITCODE -ne 0) { throw 'negative_container_cleanup_failed' }
    }
}
$failed = @($checks | Where-Object { -not $_.expected_failure }).Count
@{ schema_version = 1; fixture_only = $true; suite = 'browser_negative'; passed = ($failed -eq 0); checks = $checks; container_removed = $true } | ConvertTo-Json -Depth 8
if ($failed -ne 0) { exit 1 }
