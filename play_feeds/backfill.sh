start_date=$1
end_date=$2
dry_run=$3

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
    --attribute="dry_run=$dry_run,start_date=$start_date,end_date=$end_date"
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
  echo "Sample command: . backfill.sh 20230401 20230415"
  echo "            or: . backfill.sh 20230401 20230415 dry_run"
fi




