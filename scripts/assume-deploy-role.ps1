param(
    [string]$RoleName = "TaxHancePiiRedactionDeployment",
    [int]$DurationSeconds = 3600
)

$ErrorActionPreference = "Stop"
$accountId = aws sts get-caller-identity --query Account --output text
$session = aws sts assume-role --role-arn "arn:aws:iam::${accountId}:role/$RoleName" --role-session-name "pii-redaction-deploy" --duration-seconds $DurationSeconds --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $session.Credentials) {
    throw "Unable to assume the deployment role. AWS account root sessions cannot assume roles; use an IAM or Identity Center administrator identity."
}

$env:AWS_ACCESS_KEY_ID = $session.Credentials.AccessKeyId
$env:AWS_SECRET_ACCESS_KEY = $session.Credentials.SecretAccessKey
$env:AWS_SESSION_TOKEN = $session.Credentials.SessionToken
$env:AWS_REGION = "us-east-1"

Write-Output "Deployment role assumed for this PowerShell process."
