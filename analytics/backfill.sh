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

start_date=${1:-"2023-01-01"}
time_zone=${3:-"Europe/London"}

if [ -z "$2" ]; then
    # If $2 is not provided, use the current date in the specified time zone
    end_date=$(TZ=$time_zone date -v-1d +"%Y-%m-%d")
else
    # If $2 is provided, use it as the end_date
    end_date=$2
fi

current_date=$start_date

while [[ $(date -j -f "%Y-%m-%d" "$current_date" +%s) -lt $(date -j -f "%Y-%m-%d" "$end_date" +%s) ]]; do
    # Calculate the date two months ahead
    next_date=$(date -j -v+60d -f "%Y-%m-%d" "$current_date" +%Y-%m-%d)

    # Ensure we do not pass the end date
    if [[ $(date -j -f "%Y-%m-%d" "$next_date" +%s) -gt $(date -j -f "%Y-%m-%d" "$end_date" +%s) ]]; then
        next_date=$end_date
    fi

    echo $current_date - $next_date
    # Run the gcloud command
    gcloud pubsub topics publish analytics_data --attribute=start_date=$current_date,end_date=$next_date

    # Update current_date to the next_date + 1
    current_date=$(date -j -v+1d -f "%Y-%m-%d" "$next_date" +%Y-%m-%d)

    # Delay for 5 seconds before the next iteration
    sleep 3
done
