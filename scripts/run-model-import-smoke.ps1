[CmdletBinding()]
param()

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
$cluster = & $tofuCommand $chdirArgument output -raw ecs_cluster_name
$taskDefinition = & $tofuCommand $chdirArgument output -raw evaluation_task_definition
$capacityProvider = & $tofuCommand $chdirArgument output -raw worker_capacity_provider
$overrides = @{
    containerOverrides = @(
        @{
            name = "evaluation"
            command = @(
                "python",
                "-c",
                "import os; from transformers import AutoModelForMultimodalLM, AutoProcessor; AutoProcessor.from_pretrained(os.environ['PII_MODEL_ID'], revision=os.environ['PII_MODEL_REVISION'], trust_remote_code=False); print(AutoModelForMultimodalLM.__name__); print('PROCESSOR_OK')"
            )
        }
    )
} | ConvertTo-Json -Compress -Depth 8
$overrideFile = Join-Path ([IO.Path]::GetTempPath()) ("pii-import-smoke-{0}.json" -f [guid]::NewGuid().ToString("N"))
try {
    [IO.File]::WriteAllText($overrideFile, $overrides, [Text.UTF8Encoding]::new($false))
    $response = aws ecs run-task `
        --cluster $cluster `
        --task-definition $taskDefinition `
        --capacity-provider-strategy "capacityProvider=$capacityProvider,weight=1" `
        --count 1 `
        --started-by "model-import-smoke" `
        --overrides "file://$overrideFile" `
        --output json | ConvertFrom-Json
} finally {
    Remove-Item -LiteralPath $overrideFile -Force -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -ne 0 -or $response.failures.Count -gt 0 -or $response.tasks.Count -ne 1) {
    throw "The model import smoke task did not start."
}
[pscustomobject]@{ TaskArn = $response.tasks[0].taskArn } | ConvertTo-Json
