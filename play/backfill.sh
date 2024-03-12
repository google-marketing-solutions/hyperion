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

start_date=$1
end_date=$2
dry_run=$3
report_requested=$4

date "+%Y%m%d" -d $start_date > /dev/null  2>&1
start_date_is_valid=$?;

date "+%Y%m%d" -d $end_date > /dev/null  2>&1
end_date_is_valid=$?;

if [[ $start_date_is_valid == 0 ]] && [[ $end_date_is_valid == 0 ]]
then
  echo $start_date;
  echo $end_date;
  echo "Backfilling for $start_date ~ $end_date"
  gcloud pubsub topics publish play_feeds_backfill \
    --attribute="dry_run=$dry_run,start_date=$start_date,end_date=$end_date,report_requested=$report_requested"
else
  if [[ $start_date_is_valid != 0 ]] && [[ $end_date_is_valid != 0 ]]
  then
    echo "start_date and end_date are not in the correct format."
  elif [[ $start_date_is_valid != 0 ]]
  then
    echo "start_date is not in the correct format."
  else
    echo "end_date is not in the correct format."
  fi
  echo "Sample command: . backfill.sh 20230401 20230415 installs"
  echo "            or: . backfill.sh 20230401 20230415 dry_run earnings"
fi
