$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$requirements = Join-Path $repoRoot "requirements"
New-Item -ItemType Directory -Path $requirements -Force | Out-Null

Push-Location $repoRoot
try {
    uv export --frozen --no-dev --no-emit-project --output-file requirements/api.txt
    if ($LASTEXITCODE -ne 0) { throw "Failed to export the API lockfile." }
    uv export `
        --frozen `
        --no-dev `
        --extra worker `
        --extra model `
        --no-emit-project `
        --output-file requirements/worker.txt
    if ($LASTEXITCODE -ne 0) { throw "Failed to export the worker lockfile." }
} finally {
    Pop-Location
}
