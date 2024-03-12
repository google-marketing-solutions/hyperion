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
  # Define job configurations in an array
  jobs=(
      'play_feeds_scheduler::0 6 * * *'
      'play_feeds_earnings_scheduler::0 0 6 * *'
  )

  # Loop through the jobs array
  for index in "${jobs[@]}"; do
      job_name="${index%%::*}"
      schedule="${index##*::}"
      attributes="dry_run=${TEST}"

      # Depending on the job, set specific attributes
      if [[ "$job_name" == "play_feeds_scheduler" ]]; then
          attributes="${attributes},report_requested=installs"
      elif [[ "$job_name" == "play_feeds_earnings_scheduler" ]]; then
          attributes="${attributes},report_requested=earnings"
      fi

      # Check if the job exists
      if gcloud scheduler jobs describe ${job_name} --location us-central1 --project ${PROJECT} 2>/dev/null; then
          echo "Job ${job_name} exists, updating..."
          # Update the job
          gcloud scheduler jobs update pubsub \
              "${job_name}" \
              --schedule "${schedule}" \
              --topic "${TOPIC}" \
              --update-attributes "${attributes}" \
              --location us-central1 \
              --project ${PROJECT}
      else
          echo "Job ${job_name} does not exist, creating..."
          # Create the job
          gcloud scheduler jobs create pubsub \
              "${job_name}" \
              --schedule "${schedule}" \
              --topic "${TOPIC}" \
              --attributes "${attributes}" \
              --location us-central1 \
              --project ${PROJECT}
      fi
  done

  echo "Creating PubSub topic ${TOPIC}"
  gcloud pubsub topics create ${TOPIC}

  echo "Creating PubSub backfill topic ${TOPIC}_backfill"
  gcloud pubsub topics create ${TOPIC}_backfill

  echo "Deploying Play Feeds Cloud Function"
  gcloud functions deploy play_feeds_1 \
    --gen2 \
    --project ${PROJECT} \
    --set-env-vars GCP_PROJECT=${PROJECT} \
    --region us-central1 \
    --runtime python311 \
    --entry-point main \
    --memory 2048MB \
    --timeout=540s \
    --trigger-topic=${TOPIC} \
    --service-account ${PROJECT}@appspot.gserviceaccount.com

  echo "Deploying Play Feeds Cloud Function for Backfill"
  gcloud functions deploy play_feeds_backfill_1 \
    --gen2 \
    --project ${PROJECT} \
    --set-env-vars GCP_PROJECT=${PROJECT} \
    --region us-central1 \
    --runtime python311 \
    --entry-point backfill \
    --memory 8GB \
    --timeout=540s \
    --trigger-topic=${TOPIC}_backfill \
    --service-account ${PROJECT}@appspot.gserviceaccount.com

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