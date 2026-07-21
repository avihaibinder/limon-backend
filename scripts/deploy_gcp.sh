#!/usr/bin/env bash
#
# Deploy the LimON backend to Cloud Run + bootstrap its GCS/IAM/Secret Manager.
#
# macOS/Linux/Cloud Shell companion to scripts/deploy_gcp.ps1 (Windows). Both
# do the same thing; keep them in sync when either changes.
#
# The script is idempotent: it creates resources only if missing, so re-running
# it to ship a new revision is safe.
#
# Usage:
#   scripts/deploy_gcp.sh --project <id> --supabase-url https://<ref>.supabase.co
#
# Common flags:
#   --region <r>            (default: europe-west3)
#   --storage-region <r>    (default: us-east1; Free Tier eligible)
#   --storage-bucket <n>    (default: <project>-limon-blobs-<storage-region>)
#   --service-name <n>      (default: limon-api)
#   --cors-origins <csv>    (default: https://limon-opal.vercel.app)
#   --bootstrap-only        create infra, then stop (no deploy)
#   --require-iam-auth      deploy WITHOUT --allow-unauthenticated
#   --skip-storage-smoke    don't run the GCS round-trip Cloud Run job
#
# Prerequisites: gcloud installed and authenticated; the project must already
# have billing enabled (a bucket can't be created otherwise). The Supabase
# session-pooler DB URL must be added as a Secret Manager version (the script
# tells you when it's missing).

set -euo pipefail

# ---- defaults -------------------------------------------------------------
REGION="europe-west3"
STORAGE_REGION="us-east1"
STORAGE_BUCKET_NAME=""
SERVICE_NAME="limon-api"
DATABASE_SECRET_NAME="limon-database-url"
CORS_ORIGINS="https://limon-opal.vercel.app"
BOOTSTRAP_ONLY=false
REQUIRE_IAM_AUTH=false
SKIP_STORAGE_SMOKE=false
PROJECT_ID=""
SUPABASE_URL=""

die() { echo "error: $*" >&2; exit 1; }

# ---- args -----------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)             PROJECT_ID="$2"; shift 2 ;;
    --supabase-url)        SUPABASE_URL="$2"; shift 2 ;;
    --region)              REGION="$2"; shift 2 ;;
    --storage-region)      STORAGE_REGION="$2"; shift 2 ;;
    --storage-bucket)      STORAGE_BUCKET_NAME="$2"; shift 2 ;;
    --service-name)        SERVICE_NAME="$2"; shift 2 ;;
    --cors-origins)        CORS_ORIGINS="$2"; shift 2 ;;
    --database-secret)     DATABASE_SECRET_NAME="$2"; shift 2 ;;
    --bootstrap-only)      BOOTSTRAP_ONLY=true; shift ;;
    --require-iam-auth)    REQUIRE_IAM_AUTH=true; shift ;;
    --skip-storage-smoke)  SKIP_STORAGE_SMOKE=true; shift ;;
    -h|--help)             grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$PROJECT_ID" ]] || die "--project is required"
[[ -n "$SUPABASE_URL" ]] || die "--supabase-url is required"
[[ "$SUPABASE_URL" =~ ^https://[a-z0-9-]+\.supabase\.co/?$ ]] \
  || die "--supabase-url must look like https://<ref>.supabase.co"
command -v gcloud >/dev/null 2>&1 \
  || die "gcloud is not installed. Install the Google Cloud CLI first."
[[ "$STORAGE_REGION" =~ ^(us-west1|us-central1|us-east1)$ ]] \
  || die "--storage-region must be us-west1, us-central1, or us-east1"

RUNTIME_SA_NAME="limon-api-runtime"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET_NAME="${STORAGE_BUCKET_NAME:-${PROJECT_ID}-limon-blobs-${STORAGE_REGION}}"

# gcloud resource probes return non-zero when the resource is absent; swallow
# that so `set -e` doesn't abort the "create if missing" checks.
exists() { gcloud "$@" >/dev/null 2>&1; }

# ---- billing guard --------------------------------------------------------
billing_enabled=$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingEnabled)' 2>/dev/null || true)
[[ "$billing_enabled" == "True" ]] \
  || die "project '$PROJECT_ID' does not have billing enabled."

# ---- project + APIs -------------------------------------------------------
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com

# ---- runtime service account ---------------------------------------------
if ! exists iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT_ID"; then
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name="LimON Cloud Run runtime"
fi

# ---- private bucket -------------------------------------------------------
if ! exists storage buckets describe "gs://$BUCKET_NAME" --project="$PROJECT_ID"; then
  gcloud storage buckets create "gs://$BUCKET_NAME" \
    --project="$PROJECT_ID" \
    --location="$STORAGE_REGION" \
    --default-storage-class="STANDARD" \
    --uniform-bucket-level-access \
    --public-access-prevention
else
  actual_storage_region=$(gcloud storage buckets describe "gs://$BUCKET_NAME" \
    --project="$PROJECT_ID" --format='value(location)')
  [[ "${actual_storage_region,,}" == "${STORAGE_REGION,,}" ]] \
    || die "bucket '$BUCKET_NAME' is in '$actual_storage_region', expected '$STORAGE_REGION'. Bucket locations cannot be changed; use a different --storage-bucket."
fi

# Read/write objects (server-side BlobStorage upload/download/delete).
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
  --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/storage.objectUser"

# Sign presigned upload URLs AS ITSELF: signBlob is an IAM call, so the runtime
# SA needs token-creator on its OWN identity. Without this, presigning 403s.
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/iam.serviceAccountTokenCreator"

# ---- database secret ------------------------------------------------------
if ! exists secrets describe "$DATABASE_SECRET_NAME" --project="$PROJECT_ID"; then
  gcloud secrets create "$DATABASE_SECRET_NAME" \
    --project="$PROJECT_ID" \
    --replication-policy="automatic"
fi
gcloud secrets add-iam-policy-binding "$DATABASE_SECRET_NAME" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/secretmanager.secretAccessor"

if [[ "$BOOTSTRAP_ONLY" == true ]]; then
  echo "GCP bootstrap complete. No backend was deployed."
  echo "Add the Supabase session-pooler URL as a version of secret '$DATABASE_SECRET_NAME', then re-run without --bootstrap-only."
  exit 0
fi

# ---- require an enabled DB secret version before deploying ----------------
enabled_versions=$(gcloud secrets versions list "$DATABASE_SECRET_NAME" \
  --project="$PROJECT_ID" --filter='state=ENABLED' --format='value(name)' 2>/dev/null || true)
[[ -n "$enabled_versions" ]] \
  || die "secret '$DATABASE_SECRET_NAME' has no enabled version. Add the Supabase session-pooler URL, then re-run."

# ---- deploy ---------------------------------------------------------------
auth_flag="--allow-unauthenticated"
[[ "$REQUIRE_IAM_AUTH" == true ]] && auth_flag="--no-allow-unauthenticated"

# LIMON_GCS_SIGNER_SERVICE_ACCOUNT is intentionally omitted: on Cloud Run the
# attached SA signs URLs as itself. Only the bucket + non-secret settings go in
# env; the DB URL comes from Secret Manager.
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --service-account="$RUNTIME_SA" \
  --set-env-vars="LIMON_GCS_BUCKET=${BUCKET_NAME},LIMON_SUPABASE_URL=${SUPABASE_URL},LIMON_CORS_ORIGINS=${CORS_ORIGINS}" \
  --set-secrets="LIMON_DATABASE_URL=${DATABASE_SECRET_NAME}:latest" \
  $auth_flag

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')

# ---- health check ---------------------------------------------------------
health_header=()
if [[ "$REQUIRE_IAM_AUTH" == true ]]; then
  health_header=(-H "Authorization: Bearer $(gcloud auth print-identity-token)")
fi
if ! curl -sf "${health_header[@]}" "$SERVICE_URL/health" >/dev/null; then
  die "Cloud Run health check failed at $SERVICE_URL/health"
fi

# ---- GCS round-trip smoke test (as a Cloud Run job) -----------------------
if [[ "$SKIP_STORAGE_SMOKE" != true ]]; then
  image=$(gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" --region="$REGION" \
    --format='value(spec.template.spec.containers[0].image)')
  smoke_job="${SERVICE_NAME}-gcs-smoke"
  gcloud run jobs deploy "$smoke_job" \
    --project="$PROJECT_ID" --region="$REGION" \
    --image="$image" \
    --service-account="$RUNTIME_SA" \
    --set-env-vars="LIMON_GCS_BUCKET=$BUCKET_NAME" \
    --command="python" \
    --args="scripts/smoke_gcs.py" \
    --max-retries=0 --task-timeout=5m --quiet
  gcloud run jobs execute "$smoke_job" \
    --project="$PROJECT_ID" --region="$REGION" --wait
fi

echo "Deployment verified: $SERVICE_URL"
echo "Private blob bucket: gs://$BUCKET_NAME"
