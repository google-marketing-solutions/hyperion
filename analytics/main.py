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

import concurrent.futures
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from dotenv import load_dotenv
import json
import os
from math import ceil
import time
import traceback
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    Cohort,
    CohortSpec,
    CohortsRange,
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    Filter,
    FilterExpression,
    RunReportResponse,
)
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.cloud import bigquery
from google.api_core.exceptions import NotFound, GoogleAPICallError, RetryError
from requests.exceptions import ConnectionError, Timeout
import functions_framework
import yaml

CONFIG_FILE = "config.yaml"

try:
    with open(CONFIG_FILE, "r") as mapping_file:
        config = yaml.safe_load(mapping_file)
except FileNotFoundError:
    pass


@functions_framework.cloud_event
def main(cloud_event: Optional[dict[str, Any]] = None):
    """Main function that is also the entry point of the cloud function.

    This function orchestrates the data fetching and saving process for multiple
    Google Analytics 4 properties. It handles both local file storage and BigQuery
    loading based on the presence of a Cloud Event.

    Args:
        cloud_event: A dictionary representing the Cloud Event data. If provided,
            the data will be saved to BigQuery.
    """
    if not cloud_event:
        load_dotenv(".env.local")

    data_client = BetaAnalyticsDataClient()
    admin_client = AnalyticsAdminServiceClient()

    properties = get_all_properties(admin_client)

    start_date, end_date = assign_start_and_end_dates(cloud_event)

    # Define the maximum number of threads
    max_threads = 10  # Adjust this based on your needs and environment

    # Use ThreadPoolExecutor to run tasks in parallel
    with ThreadPoolExecutor(max_threads) as executor:
        futures = [
            executor.submit(
                fetch_and_save_report,
                data_client,
                admin_client,
                property_id,
                cloud_event,
                start_date,
                end_date,
            )
            for property_id in properties
        ]

        # Optionally, handle the results of each future here
        for index, future in enumerate(concurrent.futures.as_completed(futures)):
            try:
                future.result()
            except (GoogleAPICallError, RetryError, ConnectionError, Timeout) as exc:
                print(f"Property #{properties[index]}: Generated an exception: {exc}")
                print("Affected date range: ", start_date, end_date)
                traceback.print_exc()

    print("All done")


def get_all_properties(admin_client: AnalyticsAdminServiceClient) -> list[str]:
    """Returns all properties under all accounts accessible by the caller.

    Args:
        admin_client: An `AnalyticsAdminServiceClient` object for interacting with
            the Google Analytics Admin API.

    Returns:
        A list of property IDs.
    """
    results = admin_client.list_account_summaries()

    properties = []

    for account_summary in results:
        for property_summary in account_summary.property_summaries:
            property_id = extract_property_id_from_resource_name(
                property_summary.property
            )
            properties.append(property_id)

    return properties


def extract_property_id_from_resource_name(property_resource_name: str) -> str:
    """Extracts the ID from a property_resource_name such as properties/421450059.

    Args:
    string: The string to extract the ID from.

    Returns:
    The ID extracted from the string.
    """
    # Check if the string is in the correct format.
    if not property_resource_name.startswith("properties/"):
        raise ValueError("The string must start with 'properties/'.")

    # Extract the ID from the string.
    id = property_resource_name[len("properties/") :]

    # Return the ID.
    return id


def assign_start_and_end_dates(
    cloud_event: Optional[dict[str, Any]]
) -> tuple[str, str]:
    """Extracts parameters from the Cloud Event data.

    Args:
        cloud_event: The Cloud Event object.

    Returns:
        dict: Extracted start and end dates.
    """
    if cloud_event and cloud_event.data["message"].get("attributes") is not None:
        attr_dic = cloud_event.data["message"]["attributes"]

        start_date = attr_dic.get("start_date")
        end_date = attr_dic.get("end_date")
    else:
        # Default start and end dates
        today = datetime.now(timezone.utc)
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = yesterday
        end_date = yesterday

    return start_date, end_date


def fetch_and_save_report(
    data_client: BetaAnalyticsDataClient,
    admin_client: AnalyticsAdminServiceClient,
    property_id: str,
    cloud_event: dict,
    start_date: str,
    end_date: str,
) -> None:
    """Fetches and saves a report for a given Google Analytics 4 property.

    This function retrieves data from Google Analytics 4 using the provided
    `data_client` and `admin_client`. It then formats the data and saves it
    either to a BigQuery table (if `cloud_event` is provided) or locally as a JSON
    file.

    Args:
        data_client: A `BetaAnalyticsDataClient` object for interacting with the
            Google Analytics Data API.
        admin_client: An `AnalyticsAdminServiceClient` object for interacting with
            the Google Analytics Admin API.
        property_id: The Google Analytics 4 property ID.
        cloud_event: A dictionary representing the Cloud Event data. If provided,
            the data will be saved to BigQuery.
        start_date: The start date of the report in YYYY-MM-DD format.
        end_date: The end date of the report in YYYY-MM-DD format.
    """
    if cloud_event:
        project_id = os.environ.get("GCP_PROJECT")
        client = bigquery.Client(project=project_id)

        dataset_id = f"{project_id}.{config['dataset_name']}"
        client.create_dataset(dataset_id, exists_ok=True)

        table_id = f"{dataset_id}.{config['table']['name_prefix']}{property_id}"
        create_table(client, table_id)

        if is_existing_data_in_date_range(client, table_id, start_date, end_date):
            print(
                f"There are already rows in the {table_id} with the specified date value: {start_date} - {end_date}"
            )
            return

    response = run_report(data_client, property_id, start_date, end_date)
    if not response:
        return  # Early return if no response, avoiding nested conditionals

    app_ids, data = get_app_ids_and_format_data(response, admin_client, property_id)
    if not app_ids or not data:
        return  # Early return if no app_ids or data, avoiding further processing

    today = datetime.now(timezone.utc)
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # Process cohort data only if the end date is yesterday
    if end_date == yesterday:
        cohort_response = run_report_with_cohort(data_client, property_id)
        if cohort_response:
            cohorts_data = format_cohort_data(
                cohort_response, admin_client, property_id, app_ids
            )
            data = join_json_objects(data, cohorts_data)

    # Decide on the storage based on the cloud_event flag
    if cloud_event:
        load_data_to_bigquery(client, data, table_id)
    else:
        save_report_response_locally(data, property_id)


def run_report(
    client: BetaAnalyticsDataClient, property_id: str, start_date: str, end_date: str
) -> Optional[RunReportResponse]:
    """Runs a simple report on a Google Analytics 4 property.

    Args:
        client: A `BetaAnalyticsDataClient` object for interacting with the
            Google Analytics Data API.
        property_id: The Google Analytics 4 property ID.
        start_date: The start date of the report in YYYY-MM-DD format.
        end_date: The end date of the report in YYYY-MM-DD format.

    Returns:
        A `RunReportResponse` object if the report is successful, otherwise None.
    """
    rows_requested = 250000
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name=d.get("api_name", d.get("name")))
            for d in config["table"]["schema"]["dimensions"]
        ],
        metrics=[Metric(name=m["name"]) for m in config["table"]["schema"]["metrics"]],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="platform",
                string_filter=Filter.StringFilter(value="Android"),
            )
        ),
        limit=rows_requested,
        offset=0,
        currency_code="USD",
    )

    try:
        response = client.run_report(request)
    except (GoogleAPICallError, RetryError, ConnectionError, Timeout) as e:
        print(f"Error occurred while fetching report for property {property_id}: {e}")
        return None  # or handle it differently based on your needs

    if response.row_count > 0:
        total_response_rows = response.rows
        additional_requests = ceil(
            (max(response.row_count - rows_requested, 0)) / rows_requested
        )
        for i in range(additional_requests):
            request.offset = rows_requested * (i + 1)
            try:
                response = client.run_report(request)
                response.rows += total_response_rows
            except (GoogleAPICallError, RetryError, ConnectionError, Timeout) as e:
                print(
                    f"Error occurred while fetching report for property {property_id}: {e}"
                )
                break

        return response
    else:
        return None


def get_app_ids_and_format_data(
    response: RunReportResponse, client: AnalyticsAdminServiceClient, property_id: str
) -> tuple[set[tuple[str, str]], list[dict[str, Any]]]:
    """Extracts app IDs and formats data from a Google Analytics 4 report response.

    This function processes the `RunReportResponse` object, extracting app IDs
    from data streams and formatting the data into a list of dictionaries.

    Args:
        response: The `RunReportResponse` object returned from the Google Analytics
            Data API.
        client: An `AnalyticsAdminServiceClient` object for interacting with the
            Google Analytics Admin API.
        property_id: The Google Analytics 4 property ID.

    Returns:
        A tuple containing:
            - A set of tuples representing app IDs (stream ID, app ID).
            - A list of dictionaries, each representing a row of data from the
                report.
    """
    dimension_headers = [header.name for header in response.dimension_headers]
    metric_headers = [header.name for header in response.metric_headers]
    retention_metric_header = [
        m["name"] for m in config["table"]["schema"]["metrics_cohort"]
    ]

    # Convert each row of data into a dictionary
    formatted_data = []
    app_ids = set()
    for row in response.rows:
        row_dict = {}
        skip_row = False
        # Add dimension values to the dictionary
        for header, value in zip(dimension_headers, row.dimension_values):
            # Format date if the dimension is a date
            if header == "date":
                row_dict[header] = format_date(value.value)
            elif header == "streamId":
                app_id = get_app_name_from_data_stream(
                    client, property_id, value.value, app_ids
                )
                row_dict["appId"] = app_id
                if app_id is None:
                    skip_row = True
            else:
                row_dict[header] = value.value
        # Add metric values to the dictionary
        for header, value in zip(metric_headers, row.metric_values):
            if header in [
                "active1DayUsers",
                "active7DayUsers",
                "active28DayUsers",
                "userEngagementDuration",
            ]:
                row_dict[header] = int(value.value)
            else:
                row_dict[header] = float(value.value)

        # Set default values of retention metrics
        for m in retention_metric_header:
            row_dict[m] = 0

        if not skip_row:
            formatted_data.append(row_dict)

    return app_ids, formatted_data


def format_date(date_str: str) -> str:
    """Formats a date string from 'YYYYMMDD' to 'YYYY-MM-DD'.

    Args:
        date_str: The date string in 'YYYYMMDD' format.

    Returns:
        The formatted date string in 'YYYY-MM-DD' format.
    """
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


def get_app_name_from_data_stream(
    client: AnalyticsAdminServiceClient,
    property_id: str,
    stream_id: str,
    app_ids: set[tuple[str, str]],
) -> Optional[str]:
    """Retrieves the details for the data stream.

    Args:
        client: An `AnalyticsAdminServiceClient` object for interacting with the
            Google Analytics Admin API.
        property_id: The Google Analytics Property ID.
        stream_id: The data stream ID.
        app_ids: A set of tuples representing app IDs (stream ID, app ID).

    Returns:
        app_id: The app ID associated with the data stream, or None if not found.
    """
    app_ids_dict = convert_set_of_tuples_to_dict(app_ids)
    if stream_id in app_ids_dict:
        app_id = app_ids_dict[stream_id]
    else:
        try:
            data_stream = client.get_data_stream(
                name=f"properties/{property_id}/dataStreams/{stream_id}"
            )
            app_id = (
                data_stream.android_app_stream_data.package_name
                or data_stream.ios_app_stream_data.bundle_id
            )
            app_ids.add((stream_id, app_id))
        except (GoogleAPICallError, RetryError, ConnectionError, Timeout) as e:
            return None

    return app_id


def convert_set_of_tuples_to_dict(my_set: set[tuple[str, str]]) -> dict[str, str]:
    """Converts a set of tuples to a dictionary.

    This function takes a set of tuples, where each tuple represents a key-value pair,
    and converts it into a dictionary.

    Args:
        my_set: The set of tuples to convert.

    Returns:
        A dictionary containing the key-value pairs from the set of tuples.
    """
    my_dict = {}
    # Iterate through the set of tuples and add key-value pairs to the dictionary
    for tup in my_set:
        key, value = tup
        my_dict[key] = value
    return my_dict


def run_report_with_cohort(
    client: BetaAnalyticsDataClient, property_id: str
) -> Optional[RunReportResponse]:
    """Runs report for multiple stream ids and country on a cohort of users whose first session happened on the
    same day. The number of active users and user retention rate is calculated
    for the cohort using DAILY granularity.

    Args:
        client: A `BetaAnalyticsDataClient` object for interacting with the
            Google Analytics Data API.
        property_id: The Google Analytics 4 property ID.

    Returns:
        A `RunReportResponse` object if the report is successful, otherwise None.
    """
    # Calculate date ranges for the two cohorts
    today = datetime.now(timezone.utc)
    thirty_one_days_ago = (today - timedelta(days=31)).strftime("%Y-%m-%d")

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="cohort"),
            Dimension(name="streamId"),
            Dimension(name="countryId"),
            Dimension(name="cohortNthDay"),
        ],
        metrics=[
            Metric(name="cohortActiveUsers"),
            Metric(name="cohortTotalUsers"),
        ],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="platform",
                string_filter=Filter.StringFilter(value="Android"),
            )
        ),
        cohort_spec=CohortSpec(
            cohorts=[
                Cohort(
                    dimension="firstSessionDate",
                    date_range=DateRange(
                        start_date=thirty_one_days_ago, end_date=thirty_one_days_ago
                    ),
                ),
            ],
            cohorts_range=CohortsRange(
                start_offset=0,
                end_offset=30,
                granularity=CohortsRange.Granularity.DAILY,
            ),
        ),
    )

    try:
        response = client.run_report(request)
    except (GoogleAPICallError, RetryError, ConnectionError, Timeout) as e:
        print(f"Error occurred while fetching report for property {property_id}: {e}")
        return None

    return response


def format_cohort_data(
    response: RunReportResponse,
    client: AnalyticsAdminServiceClient,
    property_id: str,
    app_ids: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Processes cohort data from a Google Analytics 4 report response and formats it for output.

    This function takes the `RunReportResponse` object containing cohort data, extracts relevant information,
    and formats it into a list of dictionaries suitable for saving or further processing.

    Args:
        response: The `RunReportResponse` object returned from the Google Analytics Data API.
        client: An `AnalyticsAdminServiceClient` object for interacting with the Google Analytics Admin API.
        property_id: The Google Analytics 4 property ID.
        app_ids: A set of tuples representing app IDs (stream ID, app ID).

    Returns:
        A list of dictionaries, each representing a row of cohort data.
    """
    # Initialize a structure for the processed data
    retention_data = defaultdict(dict)
    formatted_data = []

    # Calculate yesterday's date in UTC
    utc_now = datetime.now(timezone.utc)
    yesterday = utc_now - timedelta(days=1)
    yesterday_date_str = yesterday.strftime("%Y-%m-%d")

    # Process the API response
    for row in response.rows:
        # This needs to match your actual data structure
        stream_id = row.dimension_values[1].value
        country_id = row.dimension_values[2].value
        cohort_day = row.dimension_values[3].value
        date = yesterday_date_str

        # Directly use cohortRetentionRate from the response
        active_users = int(row.metric_values[0].value)
        total_users = int(row.metric_values[1].value)

        key = (date, stream_id, country_id)
        if cohort_day == "0001":
            retention_data[key]["day01ActiveUsers"] = active_users
            retention_data[key]["day01TotalUsers"] = total_users

        elif cohort_day == "0007":
            retention_data[key]["day07ActiveUsers"] = active_users
            retention_data[key]["day07TotalUsers"] = total_users

        elif cohort_day == "0030":
            retention_data[key]["day30ActiveUsers"] = active_users
            retention_data[key]["day30TotalUsers"] = total_users

    # Convert the aggregated data into the desired output format
    formatted_data = [
        {
            "date": date,
            "appId": get_app_name_from_data_stream(
                client, property_id, stream_id, app_ids
            ),
            "countryId": country_id,
            **sorted(metrics.items()),
        }
        for (date, stream_id, country_id), metrics in retention_data.items()
    ]

    return formatted_data


def join_json_objects(
    list1: list[dict[str, Any]], list2: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merges two lists of dictionaries based on a unique identifier.

    This function combines two lists of dictionaries, ensuring that all keys from both lists are present in the
    merged dictionaries. It uses a unique identifier (date, appId, and countryId) to match corresponding objects
    from both lists.

    Args:
        list1: The first list of dictionaries.
        list2: The second list of dictionaries.

    Returns:
        A new list of dictionaries containing the merged data.
    """
    # Combine all keys from both lists, set default value to 0 or 0.0 based on key type
    # Extract the ordered keys from the first list and extend with unique keys from the second list
    ordered_keys = list(list1[0].keys()) if list1 else []
    unique_keys_from_list2 = {
        key for obj in list2 for key in obj.keys() if key not in ordered_keys
    }
    ordered_keys.extend(unique_keys_from_list2)
    default_values = {
        key: (
            ""
            if any(isinstance(obj.get(key), str) for obj in list1 + list2)
            else (
                0.0
                if any(isinstance(obj.get(key), float) for obj in list1 + list2)
                else 0
            )
        )
        for key in ordered_keys
    }

    # Function to create a unique identifier for each object based on date, appId, and countryId
    def identifier(obj):
        return obj["date"], obj["appId"], obj["countryId"]

    # Create dictionaries for fast lookup
    dict1 = {identifier(obj): obj for obj in list1}
    dict2 = {identifier(obj): obj for obj in list2}

    # Initialize a list to hold the result
    joined_list = []

    # Merge dictionaries
    all_identifiers = set(dict1.keys()) | set(dict2.keys())
    for id in all_identifiers:
        # Start with default values to ensure all keys exist
        merged_obj = default_values.copy()

        # Update with values from both objects, if they exist
        merged_obj.update(dict1.get(id, {}))
        merged_obj.update(dict2.get(id, {}))

        joined_list.append(merged_obj)

    return joined_list


def create_table(client: bigquery.Client, table_id: str) -> None:
    """Creates a table in BigQuery if it doesn't already exist.

    Args:
        client: A BigQuery client object.
        table_id: The ID of the table.

    Returns:
        None
    """
    try:
        client.get_table(table_id)
    except NotFound:
        schema = get_table_schema()
        table = bigquery.Table(table_id, schema=schema)
        table = client.create_table(table, exists_ok=True)
        print(f"Table {table_id} created.")
        optimize_table(table)


def get_table_schema() -> list[bigquery.SchemaField]:
    """Returns the schema for a table.

    Returns:
        schema: The schema for the table.
    """
    s = config["table"]["schema"]
    config_schema = s["dimensions"] + s["metrics"] + s["metrics_cohort"]
    schema = [bigquery.SchemaField(c["name"], c["type"]) for c in config_schema]
    return schema


def optimize_table(table: bigquery.Table) -> None:
    """Configures the time partitioning and clustering fields for a table for optimization.

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


def is_existing_data_in_date_range(
    client: bigquery.Client, table_id: str, start_date: str, end_date: str
) -> int:
    """Checks if there are existing rows in the table within the specified start (optional) and end date range.

    Args:
        client: The BigQuery client.
        table_id: The table ID to query.
        start_date: The start date of the range.
        end_date: The end date of the range.

    Returns:
        int: 1 if data exists for the given date range, 0 otherwise.
    """
    query = f"""
        SELECT *
        FROM `{table_id}`
        WHERE date BETWEEN '{start_date}' and '{end_date}'
        LIMIT 10
    """

    job = client.query(query)
    results = job.result()
    return int(results.total_rows > 0)


def load_data_to_bigquery(
    client: bigquery.Client, data: list[dict[str, Any]], table_id: str
) -> None:
    """Loads data to a BigQuery table with retries.

    This function attempts to load the provided data into the specified BigQuery table.
    It handles potential errors like GoogleAPICallError, RetryError, ConnectionError, and Timeout
    by implementing exponential backoff with a maximum of 5 retries.

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

    for i in range(5):  # max retries for load job
        try:
            job = client.load_table_from_json(data, table_id, job_config=job_config)
            print(f"Job ID: {job.job_id}")
            job.result()  # Waits for the job to complete.
            break
        except (GoogleAPICallError, RetryError, ConnectionError, Timeout) as e:
            print(f"Attempt {i+1} failed with error: {e}")
            if i < 4:  # if not the last attempt
                time.sleep(10 * (2**i))  # exponential backoff
            else:
                print("Final attempt failed, data not loaded to BigQuery")


def save_report_response_locally(
    report: list[dict[str, Any]], property_id: str
) -> None:
    """Saves the report response locally as a JSON file.

    This function creates a directory if it doesn't exist and then saves the
    report data to a JSON file with a filename based on the property ID.

    Args:
        report: The report data to be saved.
        property_id: The Google Analytics 4 property ID.
    """
    os.makedirs(config["table"]["local_dir_name"], exist_ok=True)
    file_path = f"{config['table']['local_dir_name']}/{config['table']['local_file_name_prefix']}{property_id}.json"
    with open(file_path, "w") as file:
        json.dump(report, file, indent=4)


if __name__ == "__main__":
    main()
