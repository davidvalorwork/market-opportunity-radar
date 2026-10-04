# Functions only: dot sourcing this file never starts Docker.
function Resolve-SessionDockerExecutable {
    param([Parameter(Mandatory)][object[]]$Candidates)
    # Get-Command can return docker.exe AND an extensionless shim on Windows.
    # Resolve exactly one existing file, never a joined array or shell command.
    if ($Candidates.Count -lt 1) { throw 'docker_missing' }
    $source = [string]$Candidates[0].Source
    if ([string]::IsNullOrWhiteSpace($source) -or -not [IO.Path]::IsPathFullyQualified($source)) {
        throw 'docker_executable_rejected'
    }
    try { $item = Get-Item -LiteralPath $source -Force -ErrorAction Stop }
    catch { throw 'docker_executable_rejected' }
    if ($item.PSIsContainer) { throw 'docker_executable_rejected' }
    return [string]$item.FullName
}

function Assert-SessionLocalPath {
    param([Parameter(Mandatory)][string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    if ($current.Contains(',')) { throw 'mount_path_rejected' }
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'reparse_path_rejected'
            }
        }
        $parent = [IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
}

function Invoke-SessionContainer {
    param(
        [Parameter(Mandatory)][string]$Image,
        [Parameter(Mandatory)][string]$Vault,
        [Parameter(Mandatory)][string[]]$CommandArguments,
        [string]$Shared,
        [string]$InputDirectory,
        [string]$InputFile,
        [string[]]$PublicRecipientFiles = @(),
        [switch]$VaultReadOnly,
        [switch]$SharedWritable,
        [string]$ExpectedError,
        [Diagnostics.Stopwatch]$OverallClock,
        [ValidateRange(1,180)][int]$OverallLimitSeconds = 180
    )
    $operationClock = [Diagnostics.Stopwatch]::StartNew()
    # The 30-second operation budget includes up to 2 seconds for scoped cleanup.
    $operationLimitMs = 30000
    if ($OverallClock) {
        $operationLimitMs = [int][Math]::Min($operationLimitMs,
            [Math]::Floor($OverallLimitSeconds * 1000 - $OverallClock.Elapsed.TotalMilliseconds))
    }
    if ($operationLimitMs -le 2000) { throw 'handoff_budget_exhausted' }
    $containerName = 'radar-session-' + [guid]::NewGuid().ToString('N')
    Assert-SessionLocalPath $Vault
    $vaultMount = "type=bind,source=$Vault,target=/vault"
    if ($VaultReadOnly) { $vaultMount += ',readonly' }
    $dockerArguments = @('run','--name',$containerName,'--pull','never','--network','none','--read-only',
        '--memory','128m','--memory-swap','128m','--cpus','0.5','--pids-limit','32',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--mount',$vaultMount)
    if ($Shared) {
        Assert-SessionLocalPath $Shared
        $sharedMount = "type=bind,source=$Shared,target=/shared"
        if (-not $SharedWritable) { $sharedMount += ',readonly' }
        $dockerArguments += @('--mount',$sharedMount)
    }
    if ($InputDirectory) {
        Assert-SessionLocalPath $InputDirectory
        $dockerArguments += @('--mount',"type=bind,source=$InputDirectory,target=/input,readonly")
    }
    if ($InputFile) {
        Assert-SessionLocalPath $InputFile
        $dockerArguments += @('--mount',"type=bind,source=$InputFile,target=/input-state.json,readonly")
    }
    for ($index = 0; $index -lt $PublicRecipientFiles.Count; $index++) {
        $recipientPath = $PublicRecipientFiles[$index]
        Assert-SessionLocalPath $recipientPath
        $dockerArguments += @('--mount',"type=bind,source=$recipientPath,target=/recipient-$index.txt,readonly")
    }
    $dockerArguments += $Image
    $dockerArguments += $CommandArguments
    # ProcessStartInfo.ArgumentList preserves literal arguments and never invokes a shell.
    $dockerCandidates = @(Get-Command docker -CommandType Application -ErrorAction Stop)
    $dockerPath = Resolve-SessionDockerExecutable -Candidates $dockerCandidates
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $process.StartInfo.FileName = $dockerPath
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    foreach ($argument in $dockerArguments) { $process.StartInfo.ArgumentList.Add([string]$argument) }
    $started = $false
    $cleanupFailed = $false
    try {
        $started = $process.Start()
        if (-not $started) { throw 'container_start_failed' }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $waitMs = [int][Math]::Floor($operationLimitMs - 2000 - $operationClock.Elapsed.TotalMilliseconds)
        if ($waitMs -le 0 -or -not $process.WaitForExit($waitMs)) { throw 'container_timeout' }
        $containerExit = $process.ExitCode
        if (-not $stdoutTask.IsCompleted -or -not $stderrTask.IsCompleted) {
            $drainMs = [int][Math]::Max(0, [Math]::Floor($operationLimitMs - 2000 - $operationClock.Elapsed.TotalMilliseconds))
            if ($drainMs -le 0 -or -not [Threading.Tasks.Task]::WaitAll(
                [Threading.Tasks.Task[]]@($stdoutTask,$stderrTask),$drainMs)) { throw 'container_timeout' }
        }
        $text = $stdoutTask.GetAwaiter().GetResult()
        # stderr is captured and discarded: daemon diagnostics are never logged.
    } finally {
        if ($started) {
            try { if (-not $process.HasExited) { $process.Kill($true) } } catch { }
            # Only this invocation's random identifier is ever targeted; no volumes/prune.
            $cleanup = [Diagnostics.Process]::new()
            $cleanup.StartInfo = [Diagnostics.ProcessStartInfo]::new()
            $cleanup.StartInfo.FileName = $dockerPath
            $cleanup.StartInfo.UseShellExecute = $false
            $cleanup.StartInfo.CreateNoWindow = $true
            $cleanup.StartInfo.RedirectStandardOutput = $true
            $cleanup.StartInfo.RedirectStandardError = $true
            foreach ($argument in @('rm','--force',$containerName)) { $cleanup.StartInfo.ArgumentList.Add($argument) }
            try {
                $cleanupWait = [int][Math]::Max(0,[Math]::Min(2000,
                    [Math]::Floor($operationLimitMs - $operationClock.Elapsed.TotalMilliseconds)))
                if ($cleanupWait -le 0 -or -not $cleanup.Start()) { $cleanupFailed = $true }
                else {
                    $cleanupOut = $cleanup.StandardOutput.ReadToEndAsync()
                    $cleanupErr = $cleanup.StandardError.ReadToEndAsync()
                    if (-not $cleanup.WaitForExit($cleanupWait)) {
                        $cleanupFailed = $true
                        try { $cleanup.Kill($true) } catch { }
                    } elseif ($cleanup.ExitCode -ne 0) { $cleanupFailed = $true }
                }
            } catch { $cleanupFailed = $true } finally { $cleanup.Dispose() }
        }
        $process.Dispose()
    }
    if ($cleanupFailed) { throw 'container_cleanup_failed' }
    if ($operationClock.Elapsed.TotalMilliseconds -gt 30000 -or
        ($OverallClock -and $OverallClock.Elapsed.TotalSeconds -ge $OverallLimitSeconds)) {
        throw 'handoff_budget_exhausted'
    }
    if ($text.Length -gt 65536 -or $text.Contains('SYNTHETIC-HANDOFF') -or
        $text.Contains('SYNTHETIC-LOCAL') -or $text.Contains('SYNTHETIC-IDB') -or
        $text.Contains('AGE-SECRET-KEY')) { throw 'container_output_rejected' }
    try { $result = $text | ConvertFrom-Json -ErrorAction Stop } catch { throw 'container_json_missing' }
    if ($result.schema_version -ne 1 -or $result.suite -ne 'sessions') { throw 'container_schema_rejected' }
    if ($ExpectedError) {
        if ($containerExit -ne 1 -or $result.status -ne 'failed' -or $result.error_code -ne $ExpectedError) {
            throw 'expected_rejection_missing'
        }
    } elseif ($containerExit -ne 0 -or $result.status -eq 'failed') {
        if ($result.status -eq 'failed' -and $result.error_code -cmatch '^[a-z_]{1,64}$') {
            throw ('container_rejected:' + $result.error_code)
        }
        throw 'container_operation_failed'
    }
    return $result
}
