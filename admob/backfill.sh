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


# Usage:

# sh backfill.sh project_id start_date end_date time_zone pub_id

project_id=$1
start_date=${2:-"2022-01-01"}
time_zone=${4:-"America/Los_Angeles"}

# Run the gcloud commands
gcloud config set project ${project_id}

if [ -z "$3" ]; then
    # If $2 is not provided, use the current date minus one in the specified time zone
    end_date=$(TZ=$time_zone date -v-1d +"%Y-%m-%d")
else
    # If $2 is provided, use it as the end_date
    end_date=$3
fi

pub_id=${5:-""}

current_date=$start_date

while [[ $(date -j -f "%Y-%m-%d" "$current_date" +%s) -le $(date -j -f "%Y-%m-%d" "$end_date" +%s) ]]; do
    # Calculate the date two months ahead
    next_date=$(date -j -v+90d -f "%Y-%m-%d" "$current_date" +%Y-%m-%d)

    # Ensure we do not pass the end date
    if [[ $(date -j -f "%Y-%m-%d" "$next_date" +%s) -gt $(date -j -f "%Y-%m-%d" "$end_date" +%s) ]]; then
        next_date=$end_date
    fi

    echo $current_date - $next_date
    gcloud pubsub topics publish get_admob_reports --attribute=start_date=$current_date,end_date=$next_date,populate_apps_list=true,backfill=true,pub_id=$pub_id

    # Update current_date to the next_date + 1
    current_date=$(date -j -v+1d -f "%Y-%m-%d" "$next_date" +%Y-%m-%d)

    sleep 120
done
