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
from concurrent.futures import ThreadPoolExecutor
import json
import os
from math import ceil
import time
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    Filter,
    FilterExpression,
)
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.cloud import bigquery
from google.api_core.exceptions import NotFound, GoogleAPICallError, RetryError
from requests.exceptions import ConnectionError, Timeout

import functions_framework


@functions_framework.cloud_event
def main(cloud_event=None):
    """Main function that is also the entry point of the cloud function"""
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
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                print(f"Generated an exception: {exc}")

    print("All done")


def get_all_properties(admin_client):
    """
    Returns all properties under all accounts accessible by the caller.

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


def extract_property_id_from_resource_name(property_resource_name):
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


def assign_start_and_end_dates(cloud_event):
    """
    Extracts parameters from the Cloud Event data.

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
        start_date = "yesterday"
        end_date = "yesterday"

    return start_date, end_date


def fetch_and_save_report(
    data_client, admin_client, property_id, cloud_event, start_date, end_date
):
    data = run_report(data_client, admin_client, property_id, start_date, end_date)
    if data:
        if cloud_event:
            save_reports_response_to_BQ([(property_id, data)])
        else:
            save_reports_response_locally([(property_id, data)])


def run_report(data_client, admin_client, property_id, start_date, end_date):
    """Runs a simple report on a Google Analytics 4 property."""
    print(f"Running report for {property_id}")
    rows_requested = 250000
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="date"),
            Dimension(name="streamId"),
            Dimension(name="country"),
        ],
        metrics=[
            Metric(name="active1DayUsers"),
            Metric(name="active7DayUsers"),
            Metric(name="active28DayUsers"),
            Metric(name="dauPerMau"),
            Metric(name="dauPerWau"),
            Metric(name="wauPerMau"),
            Metric(name="purchaseRevenue"),
            Metric(name="averagePurchaseRevenuePerPayingUser"),
            Metric(name="averageSessionDuration"),
        ],
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
        response = data_client.run_report(request)
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
                response = data_client.run_report(request)
                response.rows += total_response_rows
            except (GoogleAPICallError, RetryError, ConnectionError, Timeout) as e:
                print(
                    f"Error occurred while fetching report for property {property_id}: {e}"
                )
                break

        # Processing data from the response
        data = format_data_for_saving(response, admin_client, property_id)

        return data
    else:
        return None


def format_data_for_saving(response, admin_client, property_id):
    dimension_headers = [header.name for header in response.dimension_headers]
    metric_headers = [header.name for header in response.metric_headers]

    # Convert each row of data into a dictionary
    formatted_data = []
    app_ids = set()
    for row in response.rows:
        row_dict = {}
        # Add dimension values to the dictionary
        for header, value in zip(dimension_headers, row.dimension_values):
            # Format date if the dimension is a date
            if header == "date":
                row_dict[header] = format_date(value.value)
            elif header == "streamId":
                row_dict["appId"] = get_app_name_from_data_stream(
                    admin_client, property_id, value.value, app_ids
                )
            else:
                row_dict[header] = value.value
        # Add metric values to the dictionary
        for header, value in zip(metric_headers, row.metric_values):
            if header in ["active1DayUsers", "active7DayUsers", "active28DayUsers"]:
                row_dict[header] = int(value.value)
            elif header in [
                "dauPerMau",
                "dauPerWau",
                "wauPerMau",
                "purchaseRevenue",
                "totalAdRevenue",
                "averagePurchaseRevenuePerUser",
                "averageSessionDuration",
            ]:
                row_dict[header] = float(value.value)

        formatted_data.append(row_dict)

    return formatted_data


def format_date(date_str):
    # Assuming date_str is in 'YYYYMMDD' format and converting it to 'YYYY-MM-DD'
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


def get_app_name_from_data_stream(client, property_id, stream_id, app_ids):
    """
    Retrieves the details for the data stream.

    Args:
        property_id(str): The Google Analytics Property ID.
        stream_id(str): The data stream ID.

    Returns:
        app_id
    """
    app_ids_dict = convert_set_of_tuples_to_dict(app_ids)
    if stream_id in app_ids_dict:
        app_id = app_ids_dict[stream_id]
    else:
        data_stream = client.get_data_stream(
            name=f"properties/{property_id}/dataStreams/{stream_id}"
        )
        app_id = (
            data_stream.android_app_stream_data.package_name
            or data_stream.ios_app_stream_data.bundle_id
        )
        app_ids.add((stream_id, app_id))

    return app_id


def convert_set_of_tuples_to_dict(my_set):
    my_dict = {}
    # Iterate through the set of tuples and add key-value pairs to the dictionary
    for tup in my_set:
        key, value = tup
        my_dict[key] = value
    return my_dict


def save_reports_response_to_BQ(reports):
    project_id = os.environ.get("GCP_PROJECT")
    client = bigquery.Client(project=project_id)

    dataset_id = f"{project_id}.analytics_reporting_data"
    client.create_dataset(dataset_id, exists_ok=True)

    for report in reports:
        property_id, data = report

        table_id = f"{dataset_id}.analytics_report_{property_id}"
        create_table(client, table_id)

        load_data_to_bigquery(client, data, table_id)


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
    except NotFound:
        schema = get_table_schema()
        table = bigquery.Table(table_id, schema=schema)
        table = client.create_table(table)
        print(f"Table {table_id} created.")
        optimize_table(table)


def get_table_schema():
    """
    Returns the schema for a table.

    Returns:
        List[bigquery.SchemaField]: The schema for the table.
    """
    schema = [
        bigquery.SchemaField(
            "date", "DATE"
        ),  # Assuming 'date' is in 'YYYY-MM-DD' format
        bigquery.SchemaField("appId", "STRING"),  # Assuming 'appId' is a string
        bigquery.SchemaField("country", "STRING"),  # Assuming 'country' is a string
        # Metrics
        bigquery.SchemaField("active1DayUsers", "INTEGER"),
        bigquery.SchemaField("active7DayUsers", "INTEGER"),
        bigquery.SchemaField("active28DayUsers", "INTEGER"),
        bigquery.SchemaField("dauPerMau", "FLOAT"),
        bigquery.SchemaField("dauPerWau", "FLOAT"),
        bigquery.SchemaField("wauPerMau", "FLOAT"),
        bigquery.SchemaField("purchaseRevenue", "FLOAT"),
        bigquery.SchemaField("totalAdRevenue", "FLOAT"),
        bigquery.SchemaField("averagePurchaseRevenuePerUser", "FLOAT"),
        bigquery.SchemaField("averageSessionDuration", "FLOAT"),
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
        field="date",
    )
    table.require_partition_filter = True

    table.clustering_fields = ["appId", "country"]


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


def save_reports_response_locally(reports):
    os.makedirs("responses", exist_ok=True)

    for report in reports:
        property_id, data = report

        json_string = json.dumps(data)

        file_path = f"responses/analytics_data_from_response_{property_id}.json"

        with open(file_path, "w") as file:
            file.write(json_string)

        print(f"Report result for property id {property_id} saved to a local file")


if __name__ == "__main__":
    main()
