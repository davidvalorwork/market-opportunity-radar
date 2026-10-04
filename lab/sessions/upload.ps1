#requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('upload','renew')][string]$Action,
    [Parameter(Mandatory)][string]$StorageState,
    [Parameter(Mandatory)][string]$Vault,
    [Parameter(Mandatory)][string]$Alias,
    [Parameter(Mandatory)][string]$AllowedOrigin,
    [string[]]$RecipientFile = @(),
    [ValidateRange(0,2147483646)][int]$ExpectedVersion = 0,
    [string]$Image = 'market-radar-lab/sessions:local'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
. (Join-Path $PSScriptRoot 'docker-session-common.ps1')
try {
    if ($Alias -cnotmatch '^[a-z][a-z0-9-]{0,47}$' -or
        $Alias -match '^(con|prn|aux|nul|com[1-9]|lpt[1-9])$') { throw 'alias_rejected' }
    if ($Action -eq 'upload' -and $ExpectedVersion -ne 0) { throw 'initial_version_required' }
    if ($Action -eq 'renew' -and $ExpectedVersion -lt 1) { throw 'current_version_required' }
    $statePath = [IO.Path]::GetFullPath($StorageState)
    Assert-SessionLocalPath $statePath
    $stateItem = Get-Item -LiteralPath $statePath
    if ($stateItem.PSIsContainer -or $stateItem.Length -gt 1048576) { throw 'state_file_rejected' }
    $vaultPath = [IO.Path]::GetFullPath($Vault)
    Assert-SessionLocalPath $vaultPath
    if (-not (Test-Path -LiteralPath (Join-Path $vaultPath 'keys/recipient.txt'))) { throw 'keygen_required' }
    $command = if ($Action -eq 'upload') { 'prepare' } else { 'renew' }
    $arguments = @($command,'--vault','/vault','--alias',$Alias,'--allowed-origin',$AllowedOrigin,
        '--user-export','--state-file','/input-state.json','--expected-version',[string]$ExpectedVersion)
    # Mount only the explicitly supplied public files, never their entire directories.
    $publicPaths = @()
    foreach ($recipient in $RecipientFile) {
        $full = [IO.Path]::GetFullPath($recipient)
        Assert-SessionLocalPath $full
        $item = Get-Item -LiteralPath $full
        if ($item.PSIsContainer -or $item.Length -gt 4096 -or $item.Name.Contains(',')) { throw 'recipient_file_rejected' }
        $arguments += @('--recipient-file',('/recipient-' + $publicPaths.Count + '.txt'))
        $publicPaths += $full
    }
    $result = Invoke-SessionContainer -Image $Image -Vault $vaultPath -InputFile $statePath -PublicRecipientFiles $publicPaths -CommandArguments $arguments
    [ordered]@{schema_version=1;suite='sessions_upload';status=$result.status;version=$result.version;fixture_only=$false;remote_verified=$false} |
        ConvertTo-Json -Compress
} catch {
    $code = 'explicit_upload_failed'
    $message = $_.Exception.Message
    if ($message -cmatch '^container_rejected:([a-z_]{1,64})$') { $code = $Matches[1] }
    elseif ($message -in @('alias_rejected','initial_version_required','current_version_required',
            'state_file_rejected','keygen_required','recipient_file_rejected','mount_path_rejected',
            'reparse_path_rejected','container_json_missing','container_schema_rejected','container_output_rejected',
            'container_timeout','container_cleanup_failed','handoff_budget_exhausted','docker_executable_rejected')) { $code = $message }
    [ordered]@{schema_version=1;suite='sessions_upload';status='failed';error_code=$code} | ConvertTo-Json -Compress
    exit 1
}
