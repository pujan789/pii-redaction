[CmdletBinding()]
param(
    [ValidateSet("all", "lambda", "worker")]
    [string]$Target = "all",

    [ValidatePattern("^$|^[A-Za-z0-9_.-]{1,128}$")]
    [string]$BaseWorkerTag = ""
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
    if (-not $tofuPath) {
        throw "OpenTofu is required. Install it or add tofu to PATH."
    }
    $tofuCommand = $tofuPath
} else {
    $tofuCommand = $tofu.Source
}

$tempRoot = [IO.Path]::GetTempPath()
$archive = Join-Path $tempRoot ("pii-redaction-build-{0}.zip" -f [guid]::NewGuid().ToString("N"))
$tag = "build-{0}" -f [DateTime]::UtcNow.ToString("yyyyMMddHHmmss")

try {
    Push-Location $repoRoot
    & tar.exe -a -cf $archive `
        --exclude=.git `
        --exclude=.env `
        --exclude=.coverage `
        --exclude=.venv `
        --exclude=.terraform `
        --exclude=.playwright-cli `
        --exclude=frontend/node_modules `
        --exclude=frontend/dist `
        --exclude=private-evaluation `
        --exclude=private-results `
        --exclude=output `
        --exclude=model-cache `
        --exclude=data `
        --exclude=infra/backend.hcl `
        --exclude='*.tfplan' `
        --exclude='*.tfstate*' `
        --exclude='*.db' `
        .
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the sanitized build archive." }
    Pop-Location

    $chdirArgument = "-chdir=$infraRoot"
    $bucket = & $tofuCommand $chdirArgument output -raw documents_bucket
    $kmsKey = & $tofuCommand $chdirArgument output -raw kms_key_arn
    $project = & $tofuCommand $chdirArgument output -raw codebuild_project_name
    $baseWorkerImage = ""
    if ($BaseWorkerTag) {
        $workerRepository = & $tofuCommand $chdirArgument output -raw worker_repository_url
        $workerRepositoryName = $workerRepository.Substring($workerRepository.IndexOf("/") + 1)
        $baseDigest = aws ecr describe-images `
            --repository-name $workerRepositoryName `
            --image-ids "imageTag=$BaseWorkerTag" `
            --query "imageDetails[0].imageDigest" `
            --output text
        if ($baseDigest -notmatch "^sha256:[0-9a-f]{64}$") {
            throw "The requested worker overlay base does not exist."
        }
        $baseWorkerImage = "$workerRepository@$baseDigest"
    }

    aws s3 cp $archive "s3://$bucket/build-source/source.zip" `
        --sse aws:kms `
        --sse-kms-key-id $kmsKey `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "Failed to upload the sanitized build source." }

    aws codebuild start-build `
        --project-name $project `
        --environment-variables-override `
            "name=IMAGE_TAG,value=$tag,type=PLAINTEXT" `
            "name=BUILD_TARGET,value=$Target,type=PLAINTEXT" `
            "name=BASE_WORKER_IMAGE,value=$baseWorkerImage,type=PLAINTEXT" `
        --query "build.{BuildId:id,ImageTag:environment.environmentVariables[?name=='IMAGE_TAG']|[0].value}" `
        --output json
    if ($LASTEXITCODE -ne 0) { throw "Failed to start the CodeBuild image build." }
} finally {
    if ((Get-Location).Path -eq $repoRoot) {
        Pop-Location -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
}
