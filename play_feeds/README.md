
# Deployment

## Prepare

1. Update `project_id` and `dataset_id` values in config.yaml.

2. Update mappings to following format:

```
mappings:
  game account title:
    bucket: gcs_bucket_name  _
    table_suffix: destination_bigquery_table_suffix
```

- `game account title`: Can be any custom value.
- `bucket`: Google Cloud Storage bucket storing reports starting with pubsite_prod.
- `table_suffix` is suffix of table, e.g if the value is ABC, Installs country report table be in \`project_id.play_console_reports.p_Installs_country_ABC\`

## Authorize
Make sure you are in the correct cloud project that the solution should deploy
to. Otherwise, run:
```
gcloud config set project ${PROJECT}
```

Run the following command to generate Application Default Credentials:
```
gcloud auth application-default login
```

Copy the application_default_credentials.json to this project folder. Run:
```
cp <source_application_default_credentials_json> .
```
<source_application_default_credentials_json> file can be found in:
- Linux, macOS: `$HOME/.config/gcloud/application_default_credentials.json`
- Windows: `%APPDATA%\gcloud\application_default_credentials.json`

## Deploy development
Run

`. deploy.sh --project <cloud_project_id> --topic play_feeds --dry-run`

## Deploy production
Run

`. deploy.sh --project <cloud_project_id> --topic play_feeds`


# Backfill

Run:

`. backfill.sh <start_date> <end_date> <dry_run>`

start_date and end_date must be in YYYYMMDD format, and if it is a dry run, specify dry_run for the third parameter, otherwise, leave it out.
E.g.

`. backfill.sh 20230520 20230525`

`. backfill.sh 20230520 20230525 dry_run`
