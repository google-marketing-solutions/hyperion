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
  gcloud services enable iam.googleapis.com
  gcloud services enable 'pubsub.googleapis.com'
  gcloud services enable 'cloudfunctions.googleapis.com'
  gcloud services enable 'appengine.googleapis.com'
  gcloud services enable 'cloudscheduler.googleapis.com'
}

function deploy_cloud_function() {
  echo "Creating scheduler job admob_run_every_day and PubSub topic get_admob_reports"
  gcloud scheduler jobs create pubsub \
    "admob_run_every_day" \
    --schedule "0 2 * * *" \
    --topic get_admob_reports \
    --location us-central1 \
    --time-zone "America/Los_Angeles" \
    --message-body "send" \
    --project ${PROJECT}

  echo "Creating PubSub backfill topic get_admob_reports"
  gcloud pubsub topics create get_admob_reports

  echo "Deploying Get AdMob Reports Cloud Function"
  gcloud functions deploy get_admob_reports \
    --region us-central1 \
    --runtime python311 \
    --set-env-vars GCP_PROJECT=${PROJECT} \
    --entry-point admob_report_main \
    --memory 8GB \
    --timeout=540s \
    --trigger-topic get_admob_reports \

  # Assign BigQuery Data Owner role
  gcloud projects add-iam-policy-binding ${PROJECT} \
    --member="serviceAccount:${PROJECT}@appspot.gserviceaccount.com" \
    --role="roles/bigquery.dataOwner"

  # Assign BigQuery User role
  gcloud projects add-iam-policy-binding ${PROJECT} \
    --member="serviceAccount:${PROJECT}@appspot.gserviceaccount.com" \
    --role="roles/bigquery.user"
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
