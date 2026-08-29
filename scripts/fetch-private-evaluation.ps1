[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("tuning", "holdout")]
    [string]$Split,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^private-evaluation/[0-9a-f]{32}$")]
    [string]$Prefix,

    [Parameter(Mandatory = $true)]
    [string]$TaskArn,

    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$infraRoot = Join-Path $repoRoot "infra"
$tofu = Get-Command tofu -ErrorAction SilentlyContinue
if (-not $tofu) {
    $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $tofuPath = Get-ChildItem -LiteralPath $wingetRoot -Filter "tofu.exe" -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "OpenTofu" } |
        Select-Object -ExpandProperty FullName -First 1
    if (-not $tofuPath) { throw "OpenTofu is required." }
    $tofuCommand = $tofuPath
} else {
    $tofuCommand = $tofu.Source
}

$chdirArgument = "-chdir=$infraRoot"
$bucket = & $tofuCommand $chdirArgument output -raw documents_bucket
$cluster = & $tofuCommand $chdirArgument output -raw ecs_cluster_name
$task = aws ecs describe-tasks --cluster $cluster --tasks $TaskArn --query "tasks[0].{Status:lastStatus,StopCode:stopCode,Reason:stoppedReason,Container:containers[0].{ExitCode:exitCode,Reason:reason}}" --output json | ConvertFrom-Json
if ($task.Status -ne "STOPPED") {
    throw "The evaluation task is not stopped yet."
}
if ($task.Container.ExitCode -ne 0) {
    throw "The evaluation task failed ($($task.StopCode)): $($task.Container.Reason)"
}

Push-Location $repoRoot
try {
    uv run pii-evaluate fetch-aws `
        --bucket $bucket `
        --prefix $Prefix `
        --split $Split `
        --output $Output `
        --delete-remote
    if ($LASTEXITCODE -ne 0) { throw "Fetching or deleting private evaluation artifacts failed." }
} finally {
    Pop-Location
}
