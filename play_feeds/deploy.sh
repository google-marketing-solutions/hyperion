# Copyright 2023 Google LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
    --set-build-env-vars=GCLOUD_PROJECT=${PROJECT}

  echo "Deploying Play Feeds Cloud Function for Backfill"
  gcloud functions deploy play_feeds_backfill \
    --region us-central1 \
    --runtime python311 \
    --entry-point backfill \
    --memory 8GB \
    --timeout=540s \
    --trigger-topic=${TOPIC}_backfill \
    --set-build-env-vars=GCLOUD_PROJECT=${PROJECT}

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
echo "  PROJECT: ${PROJECT}"
echo "  TOPIC: ${TOPIC}"
echo "  DRY_RUN: ${TEST}"

deploy_all
