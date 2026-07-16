[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string] $ProjectId,

    [Parameter(Mandatory)]
    [ValidatePattern('^https://[a-z0-9-]+\.supabase\.co/?$')]
    [string] $SupabaseUrl,

    [string] $Region = 'europe-west3',
    [string] $ServiceName = 'limon-api',
    [string] $DatabaseSecretName = 'limon-database-url',
    [string] $CorsOrigins = 'https://limon-opal.vercel.app',
    [switch] $BootstrapOnly,
    [switch] $AllowUnauthenticated,
    [switch] $SkipStorageSmoke
)

$ErrorActionPreference = 'Stop'
$runtimeServiceAccountName = 'limon-api-runtime'
$runtimeServiceAccount = "$runtimeServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$bucketName = "$ProjectId-limon-blobs"

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments)][string[]] $CommandArgs)

    & gcloud @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud command failed: gcloud $($CommandArgs -join ' ')"
    }
}

function Test-GcloudResource {
    param([Parameter(ValueFromRemainingArguments)][string[]] $CommandArgs)

    # Resource probes commonly return a non-zero exit code when the resource
    # does not exist yet. Keep that expected result from becoming a terminating
    # PowerShell error while preserving the exit code for the caller.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & gcloud @CommandArgs *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw 'gcloud is not installed. Install the Google Cloud CLI before running this script.'
}

$billingEnabled = (& gcloud billing projects describe $ProjectId `
    --format='value(billingEnabled)' 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $billingEnabled -ne 'True') {
    throw "Project '$ProjectId' does not have team-managed billing enabled."
}

Invoke-Gcloud config set project $ProjectId
Invoke-Gcloud services enable `
    run.googleapis.com `
    cloudbuild.googleapis.com `
    artifactregistry.googleapis.com `
    storage.googleapis.com `
    secretmanager.googleapis.com

if (-not (Test-GcloudResource iam service-accounts describe $runtimeServiceAccount `
        --project=$ProjectId)) {
    Invoke-Gcloud iam service-accounts create $runtimeServiceAccountName `
        --project=$ProjectId `
        --display-name='LimON Cloud Run runtime'
}

if (-not (Test-GcloudResource storage buckets describe "gs://$bucketName" `
        --project=$ProjectId)) {
    Invoke-Gcloud storage buckets create "gs://$bucketName" `
        --project=$ProjectId `
        --location=$Region `
        --uniform-bucket-level-access `
        --public-access-prevention
}

Invoke-Gcloud storage buckets add-iam-policy-binding "gs://$bucketName" `
    --member="serviceAccount:$runtimeServiceAccount" `
    --role='roles/storage.objectUser'

if (-not (Test-GcloudResource secrets describe $DatabaseSecretName --project=$ProjectId)) {
    Invoke-Gcloud secrets create $DatabaseSecretName `
        --project=$ProjectId `
        --replication-policy='automatic'
}

Invoke-Gcloud secrets add-iam-policy-binding $DatabaseSecretName `
    --project=$ProjectId `
    --member="serviceAccount:$runtimeServiceAccount" `
    --role='roles/secretmanager.secretAccessor'

if ($BootstrapOnly) {
    Write-Host 'GCP bootstrap complete. No backend was deployed.'
    Write-Host "The team must add a version to Secret Manager secret '$DatabaseSecretName'."
    exit 0
}

$secretVersions = (& gcloud secrets versions list $DatabaseSecretName `
    --project=$ProjectId `
    --filter='state=ENABLED' `
    --format='value(name)' 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $secretVersions) {
    throw "Secret '$DatabaseSecretName' has no enabled version. Ask the team to add the Supabase session-pooler URL, then run this script again."
}

# Validate the secret locally without logging its value or passing it as a
# command-line argument. This prevents an avoidable Cloud Build when the
# Secret Manager value contains a label, whitespace, or a truncated URL.
$databaseUrl = (& gcloud secrets versions access latest `
    --secret=$DatabaseSecretName `
    --project=$ProjectId 2>$null).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not access the latest version of secret '$DatabaseSecretName'."
}

$supabaseProjectRef = ([Uri] $SupabaseUrl).Host.Split('.')[0]
$databaseUrlPattern = '^postgresql\+asyncpg://postgres\.' +
    [Regex]::Escape($supabaseProjectRef) +
    ':[^@\s]+@[^:/\s]+:5432/postgres\?ssl=require$'
if ($databaseUrl -notmatch $databaseUrlPattern) {
    throw "Secret '$DatabaseSecretName' is not the expected complete Supabase SQLAlchemy session-pooler URL. Store only the URL, without labels, quotes, whitespace, or redacted characters."
}
$databaseUrl = $null
Write-Host 'Database secret preflight passed.'

$envVars = "^@^LIMON_SUPABASE_URL=$SupabaseUrl@LIMON_GCS_BUCKET=$bucketName@LIMON_CORS_ORIGINS=$CorsOrigins"
$invocationFlag = if ($AllowUnauthenticated) {
    '--allow-unauthenticated'
}
else {
    '--no-allow-unauthenticated'
}
Invoke-Gcloud run deploy $ServiceName `
    --project=$ProjectId `
    --region=$Region `
    --source=. `
    --service-account=$runtimeServiceAccount `
    $invocationFlag `
    --set-env-vars=$envVars `
    --set-secrets="LIMON_DATABASE_URL=${DatabaseSecretName}:latest" `
    --min=0 `
    --max=1 `
    --concurrency=80 `
    --cpu=1 `
    --memory=512Mi `
    --timeout=60 `
    --port=8080 `
    --quiet

$serviceUrl = (& gcloud run services describe $ServiceName `
    --project=$ProjectId `
    --region=$Region `
    --format='value(status.url)').Trim()
if ($LASTEXITCODE -ne 0 -or -not $serviceUrl) {
    throw 'Cloud Run deployed but its service URL could not be resolved.'
}

$healthHeaders = @{}
if (-not $AllowUnauthenticated) {
    $identityToken = (& gcloud auth print-identity-token).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $identityToken) {
        throw 'Could not create an identity token for the private Cloud Run health check.'
    }
    $healthHeaders.Authorization = "Bearer $identityToken"
}

$health = Invoke-RestMethod -Uri "$serviceUrl/health" -Method Get -Headers $healthHeaders
if ($health.status -ne 'ok') {
    throw "Cloud Run health check failed at $serviceUrl/health."
}

if (-not $SkipStorageSmoke) {
    $image = (& gcloud run services describe $ServiceName `
        --project=$ProjectId `
        --region=$Region `
        --format='value(spec.template.spec.containers[0].image)').Trim()
    $smokeJob = "$ServiceName-gcs-smoke"

    Invoke-Gcloud run jobs deploy $smokeJob `
        --project=$ProjectId `
        --region=$Region `
        --image=$image `
        --service-account=$runtimeServiceAccount `
        --set-env-vars="LIMON_GCS_BUCKET=$bucketName" `
        --command='python' `
        --args='scripts/smoke_gcs.py' `
        --max-retries=0 `
        --task-timeout=5m `
        --quiet
    Invoke-Gcloud run jobs execute $smokeJob `
        --project=$ProjectId `
        --region=$Region `
        --wait
}

Write-Host "Deployment verified: $serviceUrl"
Write-Host "Private blob bucket: gs://$bucketName"
