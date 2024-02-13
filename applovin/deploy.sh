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
  sh deploy.sh --project cloud-project-id

Options:
  --project           Cloud Project ID

  -h | --help         This text
EOF
}

function enable_apis() {
  # Enable IAM API, roles/iam.serviceAccountCreator required
  gcloud services enable \
    artifactregistry.googleapis.com \
    cloudfunctions.googleapis.com \
    cloudbuild.googleapis.com \
    eventarc.googleapis.com \
    run.googleapis.com \
    logging.googleapis.com \
    pubsub.googleapis.com \
    iam.googleapis.com \
    appengine.googleapis.com \
    cloudscheduler.googleapis.com \
    bigquery.googleapis.com
}

function deploy_cloud_function() {
  echo "Creating scheduler job applovin_data_scheduler and PubSub topic applovin_data"
  gcloud scheduler jobs create pubsub \
    "applovin_data_scheduler" \
    --schedule "30 1 * * *" \
    --topic applovin_data \
    --location us-central1 \
    --time-zone="Etc/UTC" \
    --message-body "send" \
    --project ${PROJECT}

  echo "Creating PubSub topic applovin_data"
  gcloud pubsub topics create applovin_data

  echo "Deploying fetch applovin report Cloud Function"
  gcloud functions deploy applovin_data \
    --gen2 \
    --region us-central1 \
    --runtime python311 \
    --set-env-vars GCP_PROJECT=${PROJECT} \
    --entry-point main \
    --source . \
    --memory 2048MB \
    --timeout 540s \
    --trigger-topic applovin_data \
    --project ${PROJECT}
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

PROJECT=

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
    -h | --help | --*)
    usage
    return
    shift
    ;;
  esac
done

echo "[CONFIG]"
echo "  PROJECT: ${PROJECT}"

deploy_all