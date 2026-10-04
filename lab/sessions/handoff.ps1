#requires -Version 7.2
[CmdletBinding()]
param([string]$Image = 'market-radar-lab/sessions:local')

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
. (Join-Path $PSScriptRoot 'docker-session-common.ps1')
$clock = [Diagnostics.Stopwatch]::StartNew()
$PSDefaultParameterValues = @{} + $PSDefaultParameterValues
$PSDefaultParameterValues['Invoke-SessionContainer:OverallClock'] = $clock
$PSDefaultParameterValues['Invoke-SessionContainer:OverallLimitSeconds'] = 180
$checks = [ordered]@{}
$stage = 'preflight'
$report = [ordered]@{schema_version=1;suite='sessions_handoff';fixture_only=$true;status='failed';checks=$checks;useful_records=0}
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'docker_missing' }
    $repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    $runDirectory = Join-Path $repository ('.local/session-handoff/' + [guid]::NewGuid().ToString('N'))
    Assert-SessionLocalPath $runDirectory
    $owner = Join-Path $runDirectory 'owner-vault'
    $worker = Join-Path $runDirectory 'worker-vault'
    $outsider = Join-Path $runDirectory 'outsider-vault'
    $shared = Join-Path $runDirectory 'shared'
    $inputDirectory = Join-Path $runDirectory 'input'
    foreach ($directory in @($owner,$worker,$outsider,$shared,$inputDirectory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'fixtures/storage-state-v1.synthetic.json') -Destination (Join-Path $inputDirectory 'v1.json')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'fixtures/storage-state-v2.synthetic.json') -Destination (Join-Path $inputDirectory 'v2.json')
    $alias = 'demo-session'

    $stage = 'keygen'
    foreach ($vault in @($owner,$worker,$outsider)) {
        $result = Invoke-SessionContainer -Image $Image -Vault $vault -CommandArguments @('keygen','--vault','/vault')
        if ($result.status -ne 'created') { throw 'keygen_failed' }
    }
    $checks.separate_private_vaults = $true
    # Only a public recipient is copied. No private identity leaves its own vault.
    Copy-Item -LiteralPath (Join-Path $worker 'keys/recipient.txt') -Destination (Join-Path $shared 'worker-recipient.txt')

    $stage = 'prepare_v1'
    $result = Invoke-SessionContainer -Image $Image -Vault $owner -Shared $shared -InputDirectory $inputDirectory -CommandArguments @('prepare','--vault','/vault','--alias',$alias,'--state-file','/input/v1.json','--recipient-file','/shared/worker-recipient.txt')
    $checks.prepare_v1 = $result.version -eq 1 -and $result.fixture_only -eq $true
    $stage = 'share_v1'
    $result = Invoke-SessionContainer -Image $Image -Vault $owner -VaultReadOnly -Shared $shared -SharedWritable -CommandArguments @('share','--vault','/vault','--alias',$alias,'--recipient-file','/shared/worker-recipient.txt','--out','/shared/demo-v1.age')
    $checks.share_v1 = $result.version -eq 1
    $stage = 'import_v1'
    $result = Invoke-SessionContainer -Image $Image -Vault $worker -Shared $shared -CommandArguments @('import','--vault','/vault','--alias',$alias,'--bundle','/shared/demo-v1.age')
    $checks.import_v1 = $result.version -eq 1
    $stage = 'status_v1'
    $result = Invoke-SessionContainer -Image $Image -Vault $worker -VaultReadOnly -CommandArguments @('status','--vault','/vault','--alias',$alias)
    $checks.worker_v1_unverified = $result.version -eq 1 -and $result.status -eq 'unverified'

    $stage = 'wrong_key'
    $null = Invoke-SessionContainer -Image $Image -Vault $outsider -Shared $shared -ExpectedError 'decryption_rejected' -CommandArguments @('import','--vault','/vault','--alias',$alias,'--bundle','/shared/demo-v1.age')
    $result = Invoke-SessionContainer -Image $Image -Vault $outsider -VaultReadOnly -CommandArguments @('status','--vault','/vault','--alias',$alias)
    $checks.wrong_key_no_activation = $result.version -eq 0 -and $result.status -eq 'absent'

    $stage = 'renew_v2'
    $result = Invoke-SessionContainer -Image $Image -Vault $owner -Shared $shared -InputDirectory $inputDirectory -CommandArguments @('renew','--vault','/vault','--alias',$alias,'--state-file','/input/v2.json','--recipient-file','/shared/worker-recipient.txt','--expected-version','1')
    $checks.renew_v2 = $result.version -eq 2
    $stage = 'stale_cas'
    $null = Invoke-SessionContainer -Image $Image -Vault $owner -Shared $shared -InputDirectory $inputDirectory -ExpectedError 'version_conflict' -CommandArguments @('renew','--vault','/vault','--alias',$alias,'--state-file','/input/v1.json','--recipient-file','/shared/worker-recipient.txt','--expected-version','1')
    $result = Invoke-SessionContainer -Image $Image -Vault $owner -VaultReadOnly -CommandArguments @('status','--vault','/vault','--alias',$alias)
    $checks.stale_cas_preserves_v2 = $result.version -eq 2

    $stage = 'share_v2'
    $result = Invoke-SessionContainer -Image $Image -Vault $owner -VaultReadOnly -Shared $shared -SharedWritable -CommandArguments @('share','--vault','/vault','--alias',$alias,'--recipient-file','/shared/worker-recipient.txt','--out','/shared/demo-v2.age')
    $checks.share_v2 = $result.version -eq 2
    $stage = 'import_v2'
    $result = Invoke-SessionContainer -Image $Image -Vault $worker -Shared $shared -CommandArguments @('import','--vault','/vault','--alias',$alias,'--bundle','/shared/demo-v2.age','--expected-version','1')
    $checks.import_v2 = $result.version -eq 2
    $stage = 'status_v2'
    $result = Invoke-SessionContainer -Image $Image -Vault $worker -VaultReadOnly -CommandArguments @('status','--vault','/vault','--alias',$alias)
    $checks.worker_v2_unverified = $result.version -eq 2 -and $result.status -eq 'unverified'
    $stage = 'replay_v1'
    $null = Invoke-SessionContainer -Image $Image -Vault $worker -Shared $shared -ExpectedError 'version_conflict' -CommandArguments @('import','--vault','/vault','--alias',$alias,'--bundle','/shared/demo-v1.age','--expected-version','2')
    $checks.old_bundle_cannot_replace_v2 = $true
    $stage = 'shared_directory'
    $names = @(Get-ChildItem -LiteralPath $shared -Force | Select-Object -ExpandProperty Name)
    $checks.shared_only_recipient_and_ciphertext = $names.Count -eq 3 -and
        'worker-recipient.txt' -in $names -and 'demo-v1.age' -in $names -and 'demo-v2.age' -in $names
    $checks.versions_immutable = (Test-Path -LiteralPath (Join-Path $owner 'sessions/demo-session/1.age')) -and
        (Test-Path -LiteralPath (Join-Path $owner 'sessions/demo-session/2.age')) -and
        (Test-Path -LiteralPath (Join-Path $worker 'sessions/demo-session/1.age')) -and
        (Test-Path -LiteralPath (Join-Path $worker 'sessions/demo-session/2.age'))
    $checks.within_overall_deadline = $clock.Elapsed.TotalSeconds -lt 180
    if ($checks.Values -contains $false) { throw 'check_failed' }
    $report.status = 'passed'
    $report.useful_records = 2
    $report.versions_transferred = @(1,2)
    $report.artifact_directory = $runDirectory
    $report.network = 'none'
    $report.container_limits = @{memory_mib=128;cpus=0.5;pids=32;operation_timeout_seconds=30;overall_timeout_seconds=180}
} catch {
    # No exception text: it can contain Docker output, user paths or native diagnostics.
    $report.error_code = 'handoff_failed'
    $report.failed_stage = $stage
} finally {
    $clock.Stop()
    $report.timings = @{execution_ms=$clock.Elapsed.TotalMilliseconds}
}
$report | ConvertTo-Json -Depth 10 -Compress
if ($report.status -ne 'passed') { exit 1 }
