[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9_.-]{1,128}$")]
    [string]$LambdaTag,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9_.-]{1,128}$")]
    [string]$WorkerTag,

    [ValidateSet(0, 1)]
    [int]$WorkerDesiredCount = 1,

    [ValidateSet(0, 1, 2)]
    [int]$GpuCapacityDesiredCount = 1,

    [ValidateSet("transformers", "llama_cpp")]
    [string]$ModelBackend = "transformers",

    [ValidatePattern("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    [string]$ModelId = "Qwen/Qwen3.8-27B",

    [ValidatePattern("^[0-9a-f]{40}$")]
    [string]$ModelRevision = "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0",

    [ValidateSet("bfloat16", "float16", "4bit")]
    [string]$ModelDtype = "4bit",

    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    [string]$LlamaModelFile = "gemma-4-E2B-it-UD-Q4_K_XL.gguf",

    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    [string]$LlamaMmprojFile = "mmproj-F16.gguf",

    [switch]$EvaluationCpuFallback,

    [switch]$SkipFrontend,

    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$infraRoot = Join-Path $repoRoot "infra"
$frontendRoot = Join-Path $repoRoot "frontend"
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
$lambdaRepository = & $tofuCommand $chdirArgument output -raw lambda_repository_url
$workerRepository = & $tofuCommand $chdirArgument output -raw worker_repository_url
$lambdaRepositoryName = $lambdaRepository.Substring($lambdaRepository.IndexOf("/") + 1)
$workerRepositoryName = $workerRepository.Substring($workerRepository.IndexOf("/") + 1)
$lambdaDigest = aws ecr describe-images --repository-name $lambdaRepositoryName --image-ids "imageTag=$LambdaTag" --query "imageDetails[0].imageDigest" --output text
$workerDigest = aws ecr describe-images --repository-name $workerRepositoryName --image-ids "imageTag=$WorkerTag" --query "imageDetails[0].imageDigest" --output text
if ($lambdaDigest -notmatch "^sha256:[0-9a-f]{64}$" -or $workerDigest -notmatch "^sha256:[0-9a-f]{64}$") {
    throw "Both immutable image digests must exist in the dedicated ECR repositories."
}

function Assert-EcrCriticalScanClean {
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Tag
    )
    $scan = $null
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        $raw = aws ecr describe-image-scan-findings `
            --repository-name $Repository `
            --image-id "imageTag=$Tag" `
            --query "{Status:imageScanStatus.status,Counts:imageScanFindings.findingSeverityCounts}" `
            --output json 2>$null
        if ($LASTEXITCODE -eq 0) {
            $scan = $raw | ConvertFrom-Json
            if ($scan.Status -eq "COMPLETE") { break }
            if ($scan.Status -eq "FAILED") { throw "ECR scan failed for $Repository`:$Tag." }
        }
        Start-Sleep -Seconds 5
    }
    if ($null -eq $scan -or $scan.Status -ne "COMPLETE") {
        throw "ECR scan did not complete for $Repository`:$Tag. Deployment is fail-closed."
    }
    $critical = if ($null -eq $scan.Counts.CRITICAL) { 0 } else { [int]$scan.Counts.CRITICAL }
    if ($critical -gt 0) {
        throw "ECR found $critical critical findings in $Repository`:$Tag. Refusing deployment."
    }
}

Assert-EcrCriticalScanClean -Repository $lambdaRepositoryName -Tag $LambdaTag
Assert-EcrCriticalScanClean -Repository $workerRepositoryName -Tag $WorkerTag

$lambdaImage = "$lambdaRepository@$lambdaDigest"
$workerImage = "$workerRepository@$workerDigest"
$planPath = Join-Path $infraRoot "application.tfplan"
$planArguments = @(
    "plan",
    "-input=false",
    "-lock-timeout=30s",
    "-out=$planPath",
    "-var=deploy_application=true",
    "-var=lambda_image_uri=$lambdaImage",
    "-var=worker_image_uri=$workerImage",
    "-var=worker_service_desired_count=$WorkerDesiredCount",
    "-var=gpu_capacity_desired_count=$GpuCapacityDesiredCount",
    "-var=model_backend=$ModelBackend",
    "-var=model_id=$ModelId",
    "-var=model_revision=$ModelRevision",
    "-var=model_dtype=$ModelDtype",
    "-var=llama_model_file=$LlamaModelFile",
    "-var=llama_mmproj_file=$LlamaMmprojFile",
    "-var=evaluation_cpu_fallback=$($EvaluationCpuFallback.IsPresent.ToString().ToLowerInvariant())"
)
& $tofuCommand $chdirArgument @planArguments
if ($LASTEXITCODE -ne 0) { throw "Application plan failed." }
if (-not $Apply) {
    Write-Host "Plan saved. Re-run with -Apply after reviewing it."
    exit 0
}

$applyArguments = @("apply", "-input=false", "-lock-timeout=30s", "-auto-approve", $planPath)
& $tofuCommand $chdirArgument @applyArguments
if ($LASTEXITCODE -ne 0) { throw "Application apply failed." }

if (-not $SkipFrontend) {
    Push-Location $frontendRoot
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "Frontend dependency install failed." }
        npm run lint
        if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }
        npm test -- --run
        if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed." }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    } finally {
        Pop-Location
    }

    $webBucket = & $tofuCommand $chdirArgument output -raw web_bucket
    if ($webBucket -notmatch "^pii-redaction-[a-z0-9-]+-web-[0-9a-f]{8}$") {
        throw "Refusing to synchronize an unexpected web bucket."
    }
    aws s3 sync (Join-Path $frontendRoot "dist") "s3://$webBucket/" --delete --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "Frontend synchronization failed." }

    $distribution = & $tofuCommand $chdirArgument output -raw cloudfront_distribution_id
    aws cloudfront create-invalidation --distribution-id $distribution --paths "/*" --query "Invalidation.Id" --output text
    if ($LASTEXITCODE -ne 0) { throw "CloudFront invalidation failed." }
}
$url = & $tofuCommand $chdirArgument output -raw application_url
[pscustomobject]@{
    ApplicationUrl = $url
    LambdaDigest = $lambdaDigest
    WorkerDigest = $workerDigest
    WorkerDesiredCount = $WorkerDesiredCount
    GpuCapacityDesiredCount = $GpuCapacityDesiredCount
    EvaluationCpuFallback = $EvaluationCpuFallback.IsPresent
    ModelBackend = $ModelBackend
    ModelId = $ModelId
    ModelRevision = $ModelRevision
} | ConvertTo-Json
