[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("tuning", "holdout")]
    [string]$Split,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^private-evaluation/[0-9a-f]{32}$")]
    [string]$Prefix,

    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$infraRoot = Join-Path $repoRoot "infra"
$privateRoot = Join-Path $repoRoot "private-results"
$outputPath = if ([IO.Path]::IsPathRooted($Output)) {
    [IO.Path]::GetFullPath($Output)
} else {
    [IO.Path]::GetFullPath((Join-Path $repoRoot $Output))
}
$privateRootPath = [IO.Path]::GetFullPath($privateRoot).TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
)
$privateRootPrefix = "$privateRootPath$([IO.Path]::DirectorySeparatorChar)"
if (-not $outputPath.StartsWith($privateRootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Progress output must stay under the ignored private-results directory."
}
if (Test-Path -LiteralPath $outputPath -PathType Leaf) {
    throw "Progress output cannot be a file."
}
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null

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

$bucket = & $tofuCommand "-chdir=$infraRoot" output -raw documents_bucket
$checkpointKey = "$Prefix/output/$Split/checkpoint.json"
aws s3api head-object --bucket $bucket --key $checkpointKey --output json | Out-Null
if ($LASTEXITCODE -ne 0) { throw "No completed evaluation checkpoint is available yet." }
$remote = "s3://$bucket/$Prefix/output/$Split/"
aws s3 sync $remote $outputPath --only-show-errors
if ($LASTEXITCODE -ne 0) { throw "Private evaluation progress sync failed." }

$checkpointPath = Join-Path $outputPath "checkpoint.json"
if (-not (Test-Path -LiteralPath $checkpointPath -PathType Leaf)) { throw "Checkpoint download failed." }
$checkpoint = Get-Content -LiteralPath $checkpointPath -Raw | ConvertFrom-Json
if ($checkpoint.split -ne $Split -or $null -eq $checkpoint.results) {
    throw "The downloaded checkpoint is invalid."
}
[pscustomobject]@{
    Split = $Split
    CheckpointDocuments = @($checkpoint.results).Count
    DownloadedFiles = @(Get-ChildItem -LiteralPath $outputPath -File -Recurse).Count
} | ConvertTo-Json
