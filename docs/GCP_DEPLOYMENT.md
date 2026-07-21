# Google Cloud deployment

LimON runs as one FastAPI service on Cloud Run and stores private blobs in
Google Cloud Storage. Cloud Run uses `europe-west3` (Frankfurt), matching the
Supabase database region. The private Standard Storage bucket uses `us-east1`
so MVP usage can qualify for the Google Cloud Free Tier storage allowance.

## What the team must provide

- A team-owned Google Cloud project with billing already enabled.
- Permission to deploy Cloud Run services and create IAM, Storage, Secret
  Manager, Cloud Build, and Artifact Registry resources.
- The Supabase project URL.
- The Supabase IPv4 session-pooler connection URL. Store this value only in
  Secret Manager under `limon-database-url`; never commit or paste it into a
  command-line argument.

Do not attach a personal payment card or reuse an unrelated personal project.

## Architecture and security defaults

- Cloud Run region: `europe-west3` (Frankfurt).
- Cloud Storage region: `us-east1` (Free Tier eligible). The regions are
  intentionally independent; changing `-Region` does not move storage.
- Free Tier is a usage allowance, not a hard spending cap. Storage, operation,
  and data-transfer usage must remain within Google's current monthly limits;
  billing budget alerts notify the team but do not stop resources automatically.
- Cloud Run scales from zero to one instance for the MVP to minimize trial-credit usage.
- Cloud Run allows unauthenticated invocation by default because the Expo/web
  client cannot produce Google IAM identity tokens. Application data routes
  still require and verify Supabase JWTs; `/health` is intentionally public.
- Tags are scoped by authenticated user. Events require authentication but do
  not yet have a `user_id`; ownership enforcement remains a security-review
  backlog item that must be completed before using the MVP with multiple users.
- GCS uses uniform bucket-level access and enforced public-access prevention.
- The runtime service account receives `roles/storage.objectUser` only on the
  LimON bucket and `roles/secretmanager.secretAccessor` only on the database
  secret. It also receives `roles/iam.serviceAccountTokenCreator` on its own
  identity so the backend can create short-lived signed upload URLs through
  IAM `signBlob` without storing a private key.
- Cloud Run receives credentials from its service account through Application
  Default Credentials. No service-account key file is created.

## One-time prerequisites

Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), then
authenticate with the Google account the team authorized:

```powershell
gcloud auth login
```

Run the bootstrap phase from the repository root. It is idempotent and stops
before deployment:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "TEAM_PROJECT_ID" `
  -SupabaseUrl "https://PROJECT_REF.supabase.co" `
  -BootstrapOnly
```

The script enables the required APIs, creates the private `us-east1` bucket,
runtime service account, least-privilege IAM bindings, and an empty Secret
Manager secret named `limon-database-url`.

Bucket locations cannot be changed after creation. An existing European bucket
must be replaced with a new name and kept until upload and signed-URL checks
pass. Override the defaults explicitly when needed:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "TEAM_PROJECT_ID" `
  -SupabaseUrl "https://PROJECT_REF.supabase.co" `
  -StorageRegion "us-east1" `
  -StorageBucketName "TEAM_PROJECT_ID-limon-blobs-us-east1"
```

An authorized teammate must add the Supabase session-pooler URL as the first
secret version in the Google Cloud console. The expected SQLAlchemy form is:

```text
postgresql+asyncpg://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?ssl=require
```

## Deploy and verify

After the database secret has an enabled version, run the same command without
`-BootstrapOnly`:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "TEAM_PROJECT_ID" `
  -SupabaseUrl "https://PROJECT_REF.supabase.co"
```

This creates a publicly invokable Cloud Run service so the Expo/web client can
reach it. Supabase JWT authorization remains responsible for protecting
application routes. To require Google IAM authentication for an administrative
or temporary deployment, use the explicit private-mode switch:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "TEAM_PROJECT_ID" `
  -SupabaseUrl "https://PROJECT_REF.supabase.co" `
  -RequireIamAuthentication
```

Source deployment builds the existing Dockerfile remotely with Cloud Build;
local Docker is not required. The script then verifies `/health` and runs a
one-task Cloud Run Job with the production runtime service account. That job
uploads, reads, and deletes a uniquely named GCS object, proving storage works
without exposing a public test endpoint.

## Manual verification checklist

1. `GET SERVICE_URL/health` returns `{"status":"ok"}`.
2. A protected endpoint without a Supabase token returns `401`.
3. `GET /api/v1/users/me` with a valid Supabase token returns the current user.
4. The GCS smoke job execution succeeds and leaves no object behind.
5. Cloud Run logs contain no database URL, access token, or object contents.
6. The frontend uses the Cloud Run URL as its API base URL.

## Team inputs still required

Record these outside Git once provided:

- Google Cloud project ID
- Supabase project URL
- Name of the person who owns team billing
- Confirmation that `limon-database-url` has an enabled version
- Accounts that should receive deployment access
- Confirmation that event ownership is implemented before multi-user testing
