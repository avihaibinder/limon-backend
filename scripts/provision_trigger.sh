#!/usr/bin/env bash
#
# Provision the transcription trigger chain for an already-deployed LimON backend:
#
#   GCS OBJECT_FINALIZE -> Pub/Sub topic -> push subscription -> POST /internal/uploaded
#                       -> Cloud Task -> POST /internal/transcribe (the worker)
#
# The BE handlers for this chain already exist (domain 04); this script stands up
# the GCP plumbing they expect and wires the matching env onto the Cloud Run
# service. deploy_gcp.sh does NOT do any of this, so run it FIRST (the service,
# bucket, and runtime SA must already exist), then run this.
#
# POC-open: the /internal/* endpoints are left UNAUTHENTICATED (decided; OIDC
# verification is deferred). So the push subscription and the Cloud Task carry no
# OIDC token, and the Cloud Run service stays --allow-unauthenticated. When OIDC
# lands, add --push-auth-service-account/--push-auth-token-audience here, set
# LIMON_TASKS_OIDC_SERVICE_ACCOUNT, and grant the callers roles/run.invoker.
#
# Idempotent: creates each resource only if missing, so re-running is safe.
#
# Usage:
#   scripts/provision_trigger.sh --project <id>
#
# Common flags:
#   --region <r>              Cloud Run region (default: europe-west3)
#   --service-name <n>        (default: limon-api)
#   --queue-location <r>      Cloud Tasks location (default: --region). NOTE:
#                             Cloud Tasks is not in every region; verify against
#                             `gcloud tasks locations list` if create fails.
#   --transcriber-url <u>     LIMON_TRANSCRIBER_ENDPOINT_URL (from scripts/endpoint/up)
#   --transcriber-token <t>   LIMON_TRANSCRIBER_ENDPOINT_TOKEN (from scripts/endpoint/up)
#   --supabase-service-role-key <k>  LIMON_SUPABASE_SERVICE_ROLE_KEY (delete-account)
#
# The Nebius URL/token change on every endpoint (re)create; pass them again to
# repoint the service. They are set as plain env for the POC (visible in the Cloud
# Run console) - move them to Secret Manager before this is anything but a demo.
#
# Prerequisites: gcloud installed and authenticated; deploy_gcp.sh already run.

set -euo pipefail

# ---- defaults -------------------------------------------------------------
REGION="europe-west3"
SERVICE_NAME="limon-api"
QUEUE_LOCATION=""
PROJECT_ID=""
TRANSCRIBER_URL=""
TRANSCRIBER_TOKEN=""
SUPABASE_SERVICE_ROLE_KEY=""

# Fixed resource names (mirror the deploy script's ${PROJECT}-limon-blobs bucket).
TOPIC_NAME="limon-uploads"
SUBSCRIPTION_NAME="limon-uploads-push"
QUEUE_NAME="limon-transcribe"

die() { echo "error: $*" >&2; exit 1; }

# ---- args -----------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)                     PROJECT_ID="$2"; shift 2 ;;
    --region)                      REGION="$2"; shift 2 ;;
    --service-name)                SERVICE_NAME="$2"; shift 2 ;;
    --queue-location)              QUEUE_LOCATION="$2"; shift 2 ;;
    --transcriber-url)             TRANSCRIBER_URL="$2"; shift 2 ;;
    --transcriber-token)           TRANSCRIBER_TOKEN="$2"; shift 2 ;;
    --supabase-service-role-key)   SUPABASE_SERVICE_ROLE_KEY="$2"; shift 2 ;;
    -h|--help)                     grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$PROJECT_ID" ]] || die "--project is required"
command -v gcloud >/dev/null 2>&1 \
  || die "gcloud is not installed. Install the Google Cloud CLI first."
[[ -n "$QUEUE_LOCATION" ]] || QUEUE_LOCATION="$REGION"

BUCKET_NAME="${PROJECT_ID}-limon-blobs"

# gcloud resource probes return non-zero when the resource is absent; swallow
# that so `set -e` doesn't abort the "create if missing" checks.
exists() { gcloud "$@" >/dev/null 2>&1; }

gcloud config set project "$PROJECT_ID" >/dev/null

# ---- APIs -----------------------------------------------------------------
gcloud services enable \
  pubsub.googleapis.com \
  cloudtasks.googleapis.com

# ---- preconditions: service + bucket must already exist -------------------
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project="$PROJECT_ID" --region="$REGION" \
  --format='value(status.url)' 2>/dev/null || true)
[[ -n "$SERVICE_URL" ]] \
  || die "Cloud Run service '$SERVICE_NAME' not found in $REGION. Run deploy_gcp.sh first."

exists storage buckets describe "gs://$BUCKET_NAME" --project="$PROJECT_ID" \
  || die "bucket gs://$BUCKET_NAME not found. Run deploy_gcp.sh first."

# ---- Cloud Tasks queue ----------------------------------------------------
if ! exists tasks queues describe "$QUEUE_NAME" \
       --project="$PROJECT_ID" --location="$QUEUE_LOCATION"; then
  gcloud tasks queues create "$QUEUE_NAME" \
    --project="$PROJECT_ID" --location="$QUEUE_LOCATION"
fi

# ---- Pub/Sub topic --------------------------------------------------------
if ! exists pubsub topics describe "$TOPIC_NAME" --project="$PROJECT_ID"; then
  gcloud pubsub topics create "$TOPIC_NAME" --project="$PROJECT_ID"
fi

# ---- GCS -> Pub/Sub notification ------------------------------------------
# The GCS service agent must be able to publish to the topic.
GCS_SERVICE_AGENT=$(gcloud storage service-agent --project="$PROJECT_ID")
gcloud pubsub topics add-iam-policy-binding "$TOPIC_NAME" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:$GCS_SERVICE_AGENT" \
  --role="roles/pubsub.publisher"

# Notifications have no natural unique key, so re-creating makes duplicates.
# Skip if one already targets this topic. The OBJECT_FINALIZE filter + JSON
# payload match what /internal/uploaded reads (eventType + objectId attributes,
# and `name` in the payload); see app/routers/internal.py.
topic_path="//pubsub.googleapis.com/projects/${PROJECT_ID}/topics/${TOPIC_NAME}"
if ! gcloud storage buckets notifications list "gs://$BUCKET_NAME" \
       --project="$PROJECT_ID" 2>/dev/null | grep -q "$TOPIC_NAME"; then
  gcloud storage buckets notifications create "gs://$BUCKET_NAME" \
    --project="$PROJECT_ID" \
    --topic="$TOPIC_NAME" \
    --event-types=OBJECT_FINALIZE \
    --payload-format=json
fi

# ---- Pub/Sub push subscription -> /internal/uploaded ----------------------
# POC-open: no --push-auth-service-account (endpoint is unauthenticated).
if ! exists pubsub subscriptions describe "$SUBSCRIPTION_NAME" --project="$PROJECT_ID"; then
  gcloud pubsub subscriptions create "$SUBSCRIPTION_NAME" \
    --project="$PROJECT_ID" \
    --topic="$TOPIC_NAME" \
    --push-endpoint="${SERVICE_URL}/internal/uploaded" \
    --ack-deadline=60
fi

# ---- wire env onto the service --------------------------------------------
# --update-env-vars merges, preserving what deploy_gcp.sh set.
env_pairs="LIMON_TASKS_PROJECT=${PROJECT_ID}"
env_pairs+=",LIMON_TASKS_LOCATION=${QUEUE_LOCATION}"
env_pairs+=",LIMON_TASKS_QUEUE=${QUEUE_NAME}"
env_pairs+=",LIMON_TASKS_WORKER_URL=${SERVICE_URL}"
[[ -n "$TRANSCRIBER_URL" ]]   && env_pairs+=",LIMON_TRANSCRIBER_ENDPOINT_URL=${TRANSCRIBER_URL}"
[[ -n "$TRANSCRIBER_TOKEN" ]] && env_pairs+=",LIMON_TRANSCRIBER_ENDPOINT_TOKEN=${TRANSCRIBER_TOKEN}"
[[ -n "$SUPABASE_SERVICE_ROLE_KEY" ]] \
  && env_pairs+=",LIMON_SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}"

gcloud run services update "$SERVICE_NAME" \
  --project="$PROJECT_ID" --region="$REGION" \
  --update-env-vars="$env_pairs"

# ---- summary --------------------------------------------------------------
echo
echo "Trigger chain provisioned:"
echo "  bucket gs://$BUCKET_NAME  --OBJECT_FINALIZE-->  topic $TOPIC_NAME"
echo "  topic $TOPIC_NAME  --push-->  ${SERVICE_URL}/internal/uploaded"
echo "  /internal/uploaded  --Cloud Task-->  ${SERVICE_URL}/internal/transcribe"
echo "  Cloud Tasks queue: $QUEUE_NAME ($QUEUE_LOCATION)"
echo
echo "POC-open: /internal/uploaded and /internal/transcribe are UNAUTHENTICATED."
echo "Lock them down (OIDC) before this is more than a demo."
if [[ -z "$TRANSCRIBER_URL" || -z "$TRANSCRIBER_TOKEN" ]]; then
  echo
  echo "NOTE: no Nebius endpoint wired. Raise it (scripts/endpoint/up) and re-run"
  echo "      with --transcriber-url/--transcriber-token, or the worker will treat"
  echo "      transcription as unavailable."
fi
