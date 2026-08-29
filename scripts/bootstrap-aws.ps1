param(
    [string]$Region = "us-east-1",
    [string]$RoleName = "TaxHancePiiRedactionDeployment"
)

$ErrorActionPreference = "Stop"
$identity = aws sts get-caller-identity --output json | ConvertFrom-Json
if (-not $identity.Account) {
    throw "AWS authentication is unavailable."
}

$accountId = [string]$identity.Account
$stateBucket = "pii-redaction-tofu-state-$accountId"
$lockTable = "pii-redaction-tofu-locks"

$savedPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
aws s3api head-bucket --bucket $stateBucket *> $null
$stateBucketExists = $LASTEXITCODE -eq 0
$ErrorActionPreference = $savedPreference
if (-not $stateBucketExists) {
    if ($Region -eq "us-east-1") {
        aws s3api create-bucket --bucket $stateBucket --region $Region | Out-Null
    } else {
        aws s3api create-bucket --bucket $stateBucket --region $Region --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
    }
}

aws s3api put-public-access-block --bucket $stateBucket --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
aws s3api put-bucket-versioning --bucket $stateBucket --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket $stateBucket --server-side-encryption-configuration "Rules=[{ApplyServerSideEncryptionByDefault={SSEAlgorithm=AES256}}]"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure state bucket encryption."
}

$ErrorActionPreference = "Continue"
aws dynamodb describe-table --table-name $lockTable --region $Region *> $null
$lockTableExists = $LASTEXITCODE -eq 0
$ErrorActionPreference = $savedPreference
if (-not $lockTableExists) {
    aws dynamodb create-table --table-name $lockTable --region $Region --billing-mode PAY_PER_REQUEST --attribute-definitions AttributeName=LockID,AttributeType=S --key-schema AttributeName=LockID,KeyType=HASH | Out-Null
    aws dynamodb wait table-exists --table-name $lockTable --region $Region
}

$ErrorActionPreference = "Continue"
aws iam get-role --role-name $RoleName *> $null
$roleExists = $LASTEXITCODE -eq 0
$ErrorActionPreference = $savedPreference
if (-not $roleExists) {
    $trustPath = Join-Path ([System.IO.Path]::GetTempPath()) "pii-redaction-deploy-trust.json"
    $trust = @{
        Version = "2012-10-17"
        Statement = @(@{
            Effect = "Allow"
            Principal = @{ AWS = "arn:aws:iam::${accountId}:root" }
            Action = "sts:AssumeRole"
        })
    } | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($trustPath, $trust)
    try {
        aws iam create-role --role-name $RoleName --assume-role-policy-document "file://$trustPath" --description "Deployment role for the isolated PII redaction stack" | Out-Null
        aws iam attach-role-policy --role-name $RoleName --policy-arn "arn:aws:iam::aws:policy/AdministratorAccess"
    } finally {
        Remove-Item -LiteralPath $trustPath -Force -ErrorAction SilentlyContinue
    }
}

$backend = @"
bucket         = "$stateBucket"
key            = "prod/pii-redaction.tfstate"
region         = "$Region"
dynamodb_table = "$lockTable"
encrypt        = true
"@
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "..\infra\backend.hcl"), $backend)

Write-Output "AWS bootstrap complete. State storage and deployment role are ready."
Write-Output "Next: assume role $RoleName, then run 'tofu -chdir=infra init -backend-config=backend.hcl'."
