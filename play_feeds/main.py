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

"""Automate pulling play feeds report from Cloud Storage to BigQuery.

Example source report names:
  gs://<bucket>/stats/installs/installs_<app_id>_202209_country.csv
  gs://<bucket>/stats/crashes/crashes_<app_id>_202208_os_version.csv

Example destination table names:
  project_id.play_console_reports.p_Installs_country_ABC
"""

import re
import yaml
import os

from collections.abc import Mapping
from datetime import datetime, timedelta, date
from google.cloud import bigquery, logging
from google.cloud.storage.blob import Blob

import bq_utils
import gcs_utils

# Modify CONFIG_YAML_FILE to point to a different config file if required.
CONFIG_YAML_FILE = "config.yaml"

# Modify REPORT_TYPES to include report type and dimensions to include.
REPORT_TYPES = {"Installs": ["country"], "Earnings": [""]}

# REPORT_TYPES = {
#   'Crashes': ['app_version', 'device', 'os_version', 'overview'],
#   'Installs': ['app_version', 'carrier', 'country', 'device', 'language',
#                'os_version', 'overview'],
#   'Ratings': ['app_version', 'carrier', 'country', 'device', 'language', 'os_version', 'overview'],
#   'Reviews': [''],
#   'Store_Performance': ['country', 'traffic_source'],
# }

with open(CONFIG_YAML_FILE, "r") as mapping_file:
    config = yaml.safe_load(mapping_file)
    project_id = config.get("project_id")

os.environ["GCLOUD_PROJECT"] = project_id

logging_client = logging.Client()
logger = logging_client.logger(name="play-feeds")


def transfer_play_reports(
    bucket: str,
    table_suffix: str,
    project_id: str,
    dataset_id: str,
    start_date: datetime,
    end_date: datetime,
    encoding: str = "utf-16",
    dry_run: bool = False,
    report_requested: str = None,
):
    """Transfers play reports from Cloud Storage to Bigquery.

    Args:
      bucket: Source cloud storage bucket name without gs:// prefix.
      table_suffix: Table suffix for the BigQuery table names.
      project_id: BigQuery project id.
      dataset_id: BigQuery dataset id containing destination tables.
      start_date: Start date for filtering report data.
      end_date: End date for filtering report data.
      encoding: Encoding of play report files in Cloud Storage, default is utf-16.
      dry_run: Boolean flag specifying the run is for testing.
    """

    def get_prefix_by_report_type(report_type: str) -> str:
        lower_report_type = report_type.lower()
        if report_type == "Reviews" or report_type == "Earnings":
            prefix = "{report_type}/{report_type}_".format(
                report_type=lower_report_type
            )
        else:
            prefix = "stats/{report_type}/{report_type}_".format(
                report_type=lower_report_type
            )
        return prefix

    log_message = (
        "Transferring reports from Cloud Storage to BigQuery table. "
        "GCS bucket: gs://{}, BigQuery table suffix: {} "
        "start_date: {}, end_date: {}"
    ).format(
        bucket, table_suffix, start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
    )
    logger.log(log_message)

    for report_type, dimensions in REPORT_TYPES.items():
        if report_requested and report_type.lower() == report_requested.lower():
            prefix = get_prefix_by_report_type(report_type)
            logger.log(
                "Finding report files for report type: {} and period: {} ~ {}".format(
                    report_type, start_date, end_date
                )
            )
            dimension_blobs = gcs_utils.find_files_from_cloud_storage_with_date_range(
                bucket, prefix, dimensions, start_date, end_date
            )

            write_cloud_storage_to_bigquery(
                report_type,
                dimension_blobs,
                table_suffix,
                project_id,
                dataset_id,
                start_date,
                end_date,
                encoding=encoding,
                dry_run=dry_run,
            )


def write_cloud_storage_to_bigquery(
    report_type: str,
    dimension_blobs: Mapping[str, list[Blob]],
    table_suffix: str,
    project_id: str,
    dataset_id: str,
    start_date: datetime,
    end_date: datetime,
    encoding: str = "utf-16",
    dry_run: bool = False,
):
    """Reads cloud storage reports and writes to BigQuery table.

    Args:
      report_type: Report type strings such as Crashes and Installs etc.
      dimension_blobs: A dict mapping dimension to list of report blobs.
      table_suffix: Table suffix for the BigQuery table names.
      project_id: BigQuery project id.
      dataset_id: BigQuery dataset id containing destination tables.
      start_date: Start date for filtering report data.
      end_date: End date for filtering report data.
      encoding: Encoding of play report files in Cloud Storage, default is utf-16.
      dry_run: Boolean flag specifying the run is for testing.
    """

    def get_table_name_by_dimension(dimension: str) -> str:
        if dimension:
            table_name = "p_{}_{}_{}".format(report_type, dimension, table_suffix)
        else:
            table_name = "p_{}_{}".format(report_type, table_suffix)
        return table_name

    def get_schema_by_report_type(report_type: str) -> list:
        if report_type == "Reviews":
            schema = [
                bigquery.SchemaField(
                    "Review_Submit_Date_and_Time", bigquery.enums.SqlTypeNames.TIMESTAMP
                ),
                bigquery.SchemaField(
                    "Review_Last_Update_Date_and_Time",
                    bigquery.enums.SqlTypeNames.TIMESTAMP,
                ),
                bigquery.SchemaField(
                    "Developer_Reply_Date_and_Time",
                    bigquery.enums.SqlTypeNames.TIMESTAMP,
                ),
                bigquery.SchemaField(
                    "App_Version_Code", bigquery.enums.SqlTypeNames.INTEGER
                ),
                bigquery.SchemaField(
                    "App_Version_Name", bigquery.enums.SqlTypeNames.STRING
                ),
                bigquery.SchemaField(
                    "Review_Title", bigquery.enums.SqlTypeNames.STRING
                ),
                bigquery.SchemaField(
                    "Developer_Reply_Millis_Since_Epoch",
                    bigquery.enums.SqlTypeNames.INTEGER,
                ),
                bigquery.SchemaField(
                    "Developer_Reply_Text", bigquery.enums.SqlTypeNames.STRING
                ),
            ]
        elif report_type == "Earnings":
            schema = [
                bigquery.SchemaField(
                    "Transaction_Date", bigquery.enums.SqlTypeNames.DATE
                )
            ]
        else:
            schema = [bigquery.SchemaField("Date", bigquery.enums.SqlTypeNames.DATE)]
        return schema

    def get_date_fields_by_report_type(report_type):
        if report_type == "Reviews":
            return [
                "Review Submit Date and Time",
                "Review Last Update Date and Time",
                "Developer Reply Date and Time",
            ]
        elif report_type == "Earnings":
            return ["Transaction Date"]
        else:
            return ["Date"]

    logger.log(
        "Writing reports from cloud storage to BigQuery for report type {}.".format(
            report_type
        )
    )

    client = bigquery.Client()
    date_fields = get_date_fields_by_report_type(report_type)
    date_filter_fields = re.sub(r"\s+", "_", date_fields[0])

    for dimension, blobs in dimension_blobs.items():
        df = gcs_utils.get_blobs_data_frame_with_date_range(
            report_type,
            blobs,
            start_date,
            end_date,
            dry_run=dry_run,
            date_fields=date_fields,
        )

        if not df.empty:
            table_name = get_table_name_by_dimension(dimension)
            table_id = "{}.{}.{}".format(project_id, dataset_id, table_name)
            start_date_string = start_date.strftime("%Y-%m-%d")
            end_date_string = end_date.strftime("%Y-%m-%d")
            num_rows = len(df)

            # Skip writing to the table if table already has this period's data.
            if bq_utils.table_exists(
                client, table_id
            ) and bq_utils.table_has_same_data_for_date_range(
                num_rows,
                client,
                table_id,
                start_date_string,
                end_date_string,
                date_filter_fields,
            ):
                logger.log(
                    "Table {} already has the records for period {} ~ {}.".format(
                        table_id, start_date, end_date
                    )
                )
                continue

            # Write to BigQuery table if table does not exist or report is new data.
            time_partitioning = bigquery.table.TimePartitioning()
            schema = get_schema_by_report_type(report_type)
            job_config = bigquery.LoadJobConfig(
                create_disposition="CREATE_IF_NEEDED",
                write_disposition="WRITE_APPEND",
                encoding="utf-8",
                time_partitioning=time_partitioning,
                schema=schema,
            )

            if report_type == "Ratings" and dimension == "carrier":
                df["Carrier"] = df["Carrier"].astype(str)
            elif report_type == "Installs" and dimension == "os_version":
                df["Android_OS_Version"] = df["Android_OS_Version"].astype(str)
            elif report_type == "Earnings":
                df = df.convert_dtypes()
                df["Hardware"] = df["Hardware"].astype(str)

            logger.log("Writing to bigquery table: {}".format(table_id))
            job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
            job.result()
        else:
            logger.log("DF is empty.")
        del df


def run(
    dry_run: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    report_requested: str | None = None,
):
    """Reads config file and run the function.

    Args:
      dry_run: Boolean flag specifying the run is for testing.
      start_date: Start date string in the format: YYYYMMDD.
      end_date: End date string in the format: YYYYMMDD.
      report_type: Report type strings such as Crashes and Installs etc.
    """
    # There are 4 scenarios, backfill or running for a day / the most recent day:
    # If (start_date, end_date) is:
    # (1) (None, None), then run for most recent day D-4 ~ D-4;
    # (2) (20220801, None), then run backfill for period: 20220801 ~ D-4;
    # (3) (None, 20220801), then run for one day: 20220801 ~ 20220801;
    # (4) (20220801, 20220930), then run backfill for period: 20220801 ~ 20220930.
    if end_date:
        end_date = datetime.strptime(end_date, "%Y%m%d")
    else:
        today = date.today()
        end_date = datetime(today.year, today.month, today.day) - timedelta(days=4)
    start_date = datetime.strptime(start_date, "%Y%m%d") if start_date else end_date

    if dry_run:
        config_yaml_file = "config_test.yaml"
    else:
        config_yaml_file = "config.yaml"

    with open(config_yaml_file, "r") as mapping_file:
        config = yaml.safe_load(mapping_file)

        project_id = config.get("project_id")
        dataset_id = config.get("dataset_id")
        mappings = config.get("mappings", {})

        # For each game, reads reports from its bucket and writes to BigQuery
        for game, bucket_suffix in mappings.items():
            logger.log("Running for game {}".format(game))
            bucket = bucket_suffix.get("bucket")
            table_suffix = bucket_suffix.get("table_suffix")
            if bucket and table_suffix:
                transfer_play_reports(
                    bucket,
                    table_suffix,
                    project_id,
                    dataset_id,
                    start_date,
                    end_date,
                    encoding="utf-16",
                    dry_run=dry_run,
                    report_requested=report_requested,
                )
            else:
                logger.log(
                    "Missing bucket and/or table_suffix value. Add to config yaml file."
                )


def main(event: Mapping[str, Mapping], context: Mapping):
    """Main function that is also the entry point of the cloud function.

    Args:
      data: The event payload.
      context: Metadata for the event.
    """
    pubsub_attributes = event["attributes"]
    dry_run = pubsub_attributes.get("dry_run")
    if dry_run == "dry_run":
        logger.log("Running test...")
        run(dry_run=True)
    else:
        logger.log("This is NOT a dry run...")
        run()


def backfill(event: Mapping[str, Mapping], context: Mapping):
    pubsub_attributes = event["attributes"]
    dry_run = pubsub_attributes.get("dry_run")
    start_date = pubsub_attributes.get("start_date")
    end_date = pubsub_attributes.get("end_date")
    report_requested = pubsub_attributes.get("report_requested")
    # TODO: Add report type and dimension filters.

    if dry_run == "dry_run":
        logger.log("Running backfill test...")
        run(
            dry_run=True,
            start_date=start_date,
            end_date=end_date,
            report_requested=report_requested,
        )
    else:
        logger.log("Running backfill...")
        run(start_date=start_date, end_date=end_date, report_requested=report_requested)