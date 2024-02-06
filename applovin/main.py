#!/usr/bin/env python

# Copyright 2024 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import time

import google.api_core.exceptions as google_exceptions
from google.cloud import bigquery
import functions_framework
import requests
from dotenv import load_dotenv
import yaml

# max retries for BQ load job
MAX_RETRIES = 5

CONFIG_FILE = "config.yaml"

try:
    with open(CONFIG_FILE, "r") as mapping_file:
        config = yaml.safe_load(mapping_file)
except FileNotFoundError:
    pass


@functions_framework.cloud_event
def main(cloud_event=None):
    if cloud_event:
        load_dotenv(".env")
    else:
        load_dotenv(".env.local")

    # Calculate yesterday's date in UTC
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    yesterday = utc_now - datetime.timedelta(days=1)
    yesterday_date_str = yesterday.strftime("%Y-%m-%d")

    load_dotenv()
    API_KEY = os.getenv("API_KEY")
    url = "https://r.applovin.com/report"
    params = {
        "api_key": API_KEY,
        "start": yesterday_date_str,  # Set start to yesterday's date
        "end": yesterday_date_str,  # Set end to yesterday's date as well
        "format": "json",
        "columns": "day,application,package_name,country,impressions,revenue",
        "having": "impressions > 0 AND revenue > 0",
        "filter_platform": "android",
        "sort_day": "ASC",
    }

    # Make the GET request
    response = requests.get(url, params=params)

    # Check if the request was successful
    if response.status_code == 200:
        data = response.json()
        data = data["results"]
        if cloud_event:
            project_id = os.getenv("GCP_PROJECT")
            client = bigquery.Client(project=project_id)
            dataset_id = f"{project_id}.{config['dataset_name']}"
            client.create_dataset(dataset_id, exists_ok=True)
            table_id = f"{dataset_id}.{config['table']['name']}"
            create_table(client, table_id)
            load_data_to_bigquery(client, data, table_id)
        else:
            os.makedirs(config["local_dir_name"], exist_ok=True)
            file_path = f"{config['local_dir_name']}/{config['local_file_name']}.json"
            # Write the JSON results to a file
            with open(file_path, "w") as file:
                json.dump(data, file, indent=4)
            print(f"Data successfully written to {file_path}")
    else:
        print(f"Failed to retrieve data: {response.status_code}")


def create_table(client, table_id):
    """
    Creates a table in BigQuery if it doesn't already exist.

    Args:
        client: A BigQuery client object.
        table_id: The ID of the table.

    Returns:
        None
    """
    try:
        client.get_table(table_id)
    except google_exceptions.NotFound:
        schema = get_table_schema()
        table = bigquery.Table(table_id, schema=schema)
        table = client.create_table(table, exists_ok=True)
        print(f"Table {table_id} created.")
        optimize_table(table)


def get_table_schema():
    """
    Returns the schema for a table.

    Returns:
        List[bigquery.SchemaField]: The schema for the table.
    """
    schema = [
        bigquery.SchemaField(d["name"], d["type"])
        for d in config["table"]["schema"]["dimensions"]
    ] + [
        bigquery.SchemaField(m["name"], m["type"])
        for m in config["table"]["schema"]["metrics"]
    ]
    return schema


def optimize_table(table):
    """
    Configures the time partitioning and clustering fields for a table for optimization.

    Args:
        table: The BigQuery table object.

    Returns:
        None
    """
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field=config["table"]["partition_field"],
    )
    table.require_partition_filter = True

    table.clustering_fields = config["table"]["clustering_fields"]


def load_data_to_bigquery(client, data, table_id):
    """
    Loads data to a BigQuery table with retries.

    Args:
        client: The BigQuery client.
        data: The data to load.
        table_id: The table ID where data will be loaded.

    Returns:
        None
    """
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    for i in range(MAX_RETRIES):
        try:
            job = client.load_table_from_json(data, table_id, job_config=job_config)
            print(f"Job ID: {job.job_id}")
            job.result()  # Waits for the job to complete.
            break
        except (
            google_exceptions.GoogleAPICallError,
            google_exceptions.RetryError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            print(f"Attempt {i+1} failed with error: {e}")
            if i < MAX_RETRIES - 1:  # if not the last attempt
                time.sleep(10 * (2**i))  # exponential backoff
            else:
                print("Final attempt failed, data not loaded to BigQuery")


if __name__ == "__main__":
    main()
