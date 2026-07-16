# Google Cloud deployment

LimON runs as one FastAPI service on Cloud Run and stores private blobs in
Google Cloud Storage. All resources use `europe-west3` (Frankfurt), matching
the Supabase database region.

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

- Region: `europe-west3` (Frankfurt).
- Cloud Run scales from zero to one instance for the MVP to minimize trial-credit usage.
- Cloud Run is private by default. Public invocation requires the explicit
  `-AllowUnauthenticated` switch because the Expo/web client cannot produce
  Google IAM identity tokens. Application routes still verify Supabase JWTs.
- Do not enable public invocation until every patient-owned resource is scoped
  by the authenticated user. At present, tags are scoped but events still need
  a `user_id` migration and ownership enforcement.
- GCS uses uniform bucket-level access and enforced public-access prevention.
- The runtime service account receives `roles/storage.objectUser` only on the
  LimON bucket and `roles/secretmanager.secretAccessor` only on the database
  secret.
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

The script enables the required APIs, creates the private Frankfurt bucket,
runtime service account, least-privilege IAM bindings, and an empty Secret
Manager secret named `limon-database-url`.

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

This creates a private Cloud Run service. Once event ownership is implemented
and reviewed, add `-AllowUnauthenticated` so the Expo/web client can invoke the
service and rely on Supabase JWT authorization:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "TEAM_PROJECT_ID" `
  -SupabaseUrl "https://PROJECT_REF.supabase.co" `
  -AllowUnauthenticated
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
- Confirmation that event ownership is implemented before public invocation
