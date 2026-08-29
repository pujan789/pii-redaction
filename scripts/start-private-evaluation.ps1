[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("tuning", "holdout")]
    [string]$Split,

    [Parameter(Mandatory = $true)]
    [string]$Manifest,

    [ValidatePattern("^$|^private-evaluation/[0-9a-f]{32}$")]
    [string]$ExistingPrefix = "",

    [ValidateSet("", "transformers", "llama_cpp")]
    [string]$ModelBackend = "",

    [ValidatePattern("^$|^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    [string]$ModelId = "",

    [ValidatePattern("^$|^[0-9a-f]{40}$")]
    [string]$ModelRevision = "",

    [ValidatePattern("^$|^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    [string]$LlamaModelFile = "",

    [ValidatePattern("^$|^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    [string]$LlamaMmprojFile = "",

    [ValidateRange(0, 16384)]
    [int]$ModelMaxNewTokens = 0,

    [ValidateRange(-1, 2)]
    [int]$ModelAuditPasses = -1,

    [ValidateRange(0, 16384)]
    [int]$LlamaImageMinTokens = 0
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$infraRoot = Join-Path $repoRoot "infra"
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
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
$serviceName = & $tofuCommand $chdirArgument output -raw worker_service_name
$taskDefinition = & $tofuCommand $chdirArgument output -raw evaluation_task_definition
$capacityProvider = & $tofuCommand $chdirArgument output -raw worker_capacity_provider
$service = aws ecs describe-services --cluster $cluster --services $serviceName --query "services[0].{Desired:desiredCount,Running:runningCount}" --output json | ConvertFrom-Json
if ($service.Desired -ne 0 -or $service.Running -ne 0) {
    throw "The production worker must be at desired/running count zero before private evaluation."
}
$containerInstances = [int](aws ecs list-container-instances --cluster $cluster --status ACTIVE --query "length(containerInstanceArns)" --output text)
if ($containerInstances -lt 1) { throw "The isolated evaluation container instance is not ready." }

$prefix = $ExistingPrefix
if (-not $prefix) {
    $runId = [guid]::NewGuid().ToString("N")
    $prefix = "private-evaluation/$runId"
    Push-Location $repoRoot
    try {
        uv run pii-evaluate stage-aws --manifest $manifestPath --bucket $bucket --prefix $prefix --split $Split
        if ($LASTEXITCODE -ne 0) { throw "Private evaluation staging failed." }
    } finally {
        Pop-Location
    }
} else {
    aws s3api head-object --bucket $bucket --key "$prefix/manifest.json" --output json | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The existing encrypted evaluation prefix is unavailable." }
}

$environmentOverrides = @()
foreach ($override in @(
    @{ name = "PII_MODEL_BACKEND"; value = $ModelBackend },
    @{ name = "PII_MODEL_ID"; value = $ModelId },
    @{ name = "PII_MODEL_REVISION"; value = $ModelRevision },
    @{ name = "PII_LLAMA_MODEL_FILE"; value = $LlamaModelFile },
    @{ name = "PII_LLAMA_MMPROJ_FILE"; value = $LlamaMmprojFile },
    @{ name = "PII_MODEL_MAX_NEW_TOKENS"; value = if ($ModelMaxNewTokens) { [string]$ModelMaxNewTokens } else { "" } },
    @{ name = "PII_MODEL_AUDIT_PASSES"; value = if ($ModelAuditPasses -ge 0) { [string]$ModelAuditPasses } else { "" } },
    @{ name = "PII_LLAMA_IMAGE_MIN_TOKENS"; value = if ($LlamaImageMinTokens) { [string]$LlamaImageMinTokens } else { "" } }
)) {
    if ($override.value) { $environmentOverrides += $override }
}
if (($ModelBackend -or $ModelId -or $ModelRevision) -and
    -not ($ModelBackend -and $ModelId -and $ModelRevision)) {
    throw "ModelBackend, ModelId, and ModelRevision must be supplied together."
}

$overrides = @{
    containerOverrides = @(
        @{
            name = "evaluation"
            environment = $environmentOverrides
            command = @(
                "python",
                "-m",
                "taxhance_pii.evaluation.cli",
                "aws-run",
                "--bucket", $bucket,
                "--prefix", $prefix,
                "--split", $Split
            )
        }
    )
} | ConvertTo-Json -Compress -Depth 8
$strategy = "capacityProvider=$capacityProvider,weight=1"
$overrideFile = Join-Path ([IO.Path]::GetTempPath()) ("pii-evaluation-overrides-{0}.json" -f [guid]::NewGuid().ToString("N"))
try {
    [IO.File]::WriteAllText($overrideFile, $overrides, [Text.UTF8Encoding]::new($false))
    $response = aws ecs run-task `
        --cluster $cluster `
        --task-definition $taskDefinition `
        --capacity-provider-strategy $strategy `
        --count 1 `
        --started-by "private-evaluation-$Split" `
        --overrides "file://$overrideFile" `
        --output json | ConvertFrom-Json
} finally {
    Remove-Item -LiteralPath $overrideFile -Force -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -ne 0 -or $response.failures.Count -gt 0 -or $response.tasks.Count -ne 1) {
    throw "The private evaluation task did not start. The encrypted prefix remains for retry or explicit cleanup."
}
[pscustomobject]@{
    Split = $Split
    Prefix = $prefix
    TaskArn = $response.tasks[0].taskArn
    ModelBackend = if ($ModelBackend) { $ModelBackend } else { "task-default" }
    ModelId = if ($ModelId) { $ModelId } else { "task-default" }
} | ConvertTo-Json
