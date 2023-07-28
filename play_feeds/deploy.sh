# Functions
function usage() {
  cat <<EOF
deploy.sh
=========

Usage:
  . deploy.sh --project cloud-project-id --topic play_feeds --dry-run

Options:
  --project           Cloud Project ID
  --topic             Pub Sub topic name
  --dry-run           Test running with own cloud project only.

  -h | --help         This text
EOF
}

function enable_apis() {
  # Enable IAM API, roles/iam.serviceAccountCreator required
  gcloud services enable iam.googleapis.com
  gcloud services enable 'pubsub.googleapis.com'
  gcloud services enable 'cloudfunctions.googleapis.com'
  gcloud services enable 'appengine.googleapis.com'
  gcloud services enable 'cloudscheduler.googleapis.com'
}

function deploy_cloud_function() {
  echo "Creating scheduler job play_feeds_scheduler and PubSub topic ${TOPIC}"
  gcloud scheduler jobs create pubsub \
    "play_feeds_scheduler" \
    --schedule "0 6 * * *" \
    --topic "${TOPIC}" \
    --attributes dry_run="${TEST}" \
    --location us-central1 \
    --project ${PROJECT}

  echo "Creating PubSub backfill topic ${TOPIC}_backfill"
  gcloud pubsub topics create ${TOPIC}_backfill

  echo "Deploying Play Feeds Cloud Function"
  gcloud functions deploy play_feeds \
    --region us-central1 \
    --runtime python311 \
    --entry-point main \
    --memory 2048MB \
    --timeout=540s \
    --trigger-topic=${TOPIC} \
    --set-build-env-vars=GOOGLE_APPLICATION_CREDENTIALS=application_default_credentials.json,GCLOUD_PROJECT=${PROJECT}

  echo "Deploying Play Feeds Cloud Function for Backfill"
  gcloud functions deploy play_feeds_backfill \
    --region us-central1 \
    --runtime python311 \
    --entry-point backfill \
    --memory 8GB \
    --timeout=540s \
    --trigger-topic=${TOPIC}_backfill \
    --set-build-env-vars=GOOGLE_APPLICATION_CREDENTIALS=application_default_credentials.json,GCLOUD_PROJECT=${PROJECT}

}

function deploy_all() {
  echo "Setting project..."
  gcloud config set project ${PROJECT}

  echo "(Step 1) Enable APIs."

  enable_apis
  echo "(Step 2) Deploy cloud function."
  deploy_cloud_function
}

# Main

echo "(Step 0) Parsing arguments and setting variables."

TOPIC=play_feeds
PROJECT=
TEST=" "

if [ "$#" == "0" ]; then
	usage
	return
fi

while [[ $# -gt 0 ]]; do
  case $1 in
    --project)
    shift
    PROJECT="$1"
    shift
    ;;
    --topic)
    shift
    TOPIC="$1"
    shift
    ;;
    --dry-run)
    shift
    TEST="dry_run"
    ;;
    -h | --help | --*)
    usage
    return
    shift
    ;;
  esac
done

echo "[CONFIG]"
echo "  PROPJECT: ${PROJECT}"
echo "  TOPIC: ${TOPIC}"
echo "  DRY_RUN: ${TEST}"

deploy_all
