#requires -Version 7.2
# Static verification only. Never starts Docker or reads any session/vault.
$ErrorActionPreference = 'Stop'
$checks = [ordered]@{ast_parse=$true;no_direct_docker_shell=$true;no_host_deletion=$true}
$helperAst = $null
foreach ($name in @('docker-session-common.ps1','handoff.ps1','upload.ps1')) {
    $tokens = $null
    $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot $name),[ref]$tokens,[ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { $checks.ast_parse = $false }
    if ($name -eq 'docker-session-common.ps1') { $helperAst = $ast }
    foreach ($command in $ast.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst]},$true)) {
        $commandName = $command.GetCommandName()
        if ($commandName -eq 'docker') { $checks.no_direct_docker_shell = $false }
        if ($commandName -in @('Remove-Item','Clear-Content','rm','rmdir')) { $checks.no_host_deletion = $false }
    }
}
$shellAssignments = @($helperAst.FindAll({param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left -is [Management.Automation.Language.MemberExpressionAst] -and
    $node.Left.Member.Value -eq 'UseShellExecute'
},$true))
$checks.shell_execution_disabled = $shellAssignments.Count -eq 2 -and
    @($shellAssignments | Where-Object { $_.Right.Extent.Text -ne '$false' }).Count -eq 0
$argumentAdds = @($helperAst.FindAll({param($node)
    $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
    $node.Member.Value -eq 'Add' -and
    $node.Expression -is [Management.Automation.Language.MemberExpressionAst] -and
    $node.Expression.Member.Value -eq 'ArgumentList'
},$true))
$checks.literal_argument_lists = $argumentAdds.Count -eq 2
$boundedWaits = @($helperAst.FindAll({param($node)
    $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
    $node.Member.Value -eq 'WaitForExit' -and $node.Arguments.Count -eq 1
},$true))
$checks.bounded_process_waits = $boundedWaits.Count -eq 2
. (Join-Path $PSScriptRoot 'docker-session-common.ps1')
# Real existing files stand in for two PATH matches. Nothing is executed.
$firstFakeMatch = [pscustomobject]@{Source=(Join-Path $PSScriptRoot 'docker-session-common.ps1')}
$secondFakeMatch = [pscustomobject]@{Source=(Join-Path $PSScriptRoot 'handoff.ps1')}
try {
    $resolvedFake = @(Resolve-SessionDockerExecutable -Candidates @($firstFakeMatch,$secondFakeMatch))
    $checks.multiple_matches_resolve_one_file = $resolvedFake.Count -eq 1 -and
        $resolvedFake[0] -eq [IO.Path]::GetFullPath($firstFakeMatch.Source)
} catch { $checks.multiple_matches_resolve_one_file = $false }
try {
    $null = Resolve-SessionDockerExecutable -Candidates @([pscustomobject]@{
        Source=($firstFakeMatch.Source + ' ' + $secondFakeMatch.Source)
    })
    $checks.joined_executable_paths_rejected = $false
} catch { $checks.joined_executable_paths_rejected = $_.Exception.Message -eq 'docker_executable_rejected' }
$status = if ($checks.Values -contains $false) { 'failed' } else { 'passed' }
[ordered]@{schema_version=1;suite='sessions_scripts';status=$status;checks=$checks} | ConvertTo-Json -Compress
if ($status -ne 'passed') { exit 1 }
