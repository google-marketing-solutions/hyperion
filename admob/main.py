# Copyright 2024 Google LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import functions_framework
import json
import logging
import os
import pytz
import sys
import time
import traceback
from flatten_json import flatten
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from datetime import date, datetime, timedelta
import admob_utils

TOKEN_NUMBER = 0
TOTAL_TOKENS = 0

def custom_logging(message):
    """
    Custom logging function that includes token information if provided.

    Args:
        message: The log message.

    Returns:
        None
    """
    print(f'Token # {TOKEN_NUMBER}/{TOTAL_TOKENS} - {message}')

def list_apps(service, publisher_id, dry_run=False):
    """
    Lists all apps under an AdMob account, specifically filtering for Android apps.

    Args:
        service: An AdMob Service Object.
        publisher_id: The publisher ID.
        dry_run: Boolean flag for dry run mode.

    Returns:
        None
    """
    custom_logging(f'Generating apps list for {publisher_id}')

    data = []
    next_page_token = ''

    while True:
        apps, next_page_token = fetch_apps_page(service, publisher_id, next_page_token)
        if not apps:
            break
        for app in apps:
            if is_android_and_google_play_app(app):
                data.append(format_app_data(app))

        if not next_page_token:
            break

    if dry_run:
        handle_dry_run(data, publisher_id)
    else:
        client, table_id = setup_bigquery(publisher_id)

        did_update = compare_and_update_if_necessary(client, table_id, data)

        if did_update:
            log_bigquery_table_info(client, table_id)

def fetch_apps_page(service, publisher_id, page_token):
    """
    Fetches a page of apps from the AdMob service.

    Args:
        service: An AdMob service object.
        publisher_id: The publisher ID.
        page_token: Token for pagination.

    Returns:
        Tuple[List[dict]| NoneType, str| NoneType]: A tuple containing a list of apps and the next page token.
    """
    response = service.accounts().apps().list(
        pageSize=100,
        pageToken=page_token,
        parent=f'accounts/{publisher_id}'
    ).execute()

    # Check if the response is empty.
    if not response:
        return None, None

    apps = response.get('apps', [])
    next_page_token = response.get('nextPageToken')
    return apps, next_page_token

def is_android_and_google_play_app(app):
    """
    Checks if the given app is an Android app and published on the Play Store.

    Args:
        app: A dictionary representing an app.

    Returns:
        bool: True if the app is an Android app published on the Play Store, False otherwise.
    """
    return (
        app['platform'] == 'ANDROID'
            and 'linkedAppInfo' in app
            and 'androidAppStores' in app['linkedAppInfo']
            and 'GOOGLE_PLAY_APP_STORE' in app['linkedAppInfo']['androidAppStores']
    )

def format_app_data(app):
    """
    Formats the app data into a structured dictionary.

    Args:
        app: A dictionary representing an app.

    Returns:
        dict: Formatted app data.
    """
    return {
        'app_id': app['appId'],
        'app_store_id': app['linkedAppInfo'].get('appStoreId'),
        'app_store_display_name': app['linkedAppInfo'].get('displayName')
    }

def compare_and_update_if_necessary(client, table_id, incoming_rows):
    """
    Compares the incoming rows with the existing rows on BigQuery and updates the table if necessary.

    Args:
        client: A BigQuery client object.
        table_id: The BigQuery table ID.
        incoming_rows: A list of incoming rows.

    Returns:
        bool: True if the table was updated, False otherwise.
    """
    # Get existing rows from BigQuery
    existing_rows = get_existing_apps_list(client, table_id)
    existing_set = dict_list_to_tuple_set(existing_rows)

    incoming_set = dict_list_to_tuple_set(incoming_rows)

    # Find new rows (present in incoming rows but not in existing rows on BQ)
    difference = set(incoming_set) - set(existing_set)

    # Insert new rows into BigQuery
    if difference:
        rows_to_insert = [dict(t) for t in difference]
        custom_logging(f"Found {len(rows_to_insert)} new rows to insert.")
        load_data_to_bigquery(client, rows_to_insert, table_id)
        return True
    else:
        custom_logging("No new rows to insert.")
        return False

def get_existing_apps_list(client, table_id):
    """
    Gets the existing apps list from BigQuery.

    Args:
        client: A BigQuery client object.
        table_id: The BigQuery table ID.

    Returns:
        A list of existing apps.
    """
    query = f'''
        SELECT *
        FROM `{table_id}`
    '''

    job = client.query(query)
    results = job.result()
    return results

def dict_list_to_tuple_set(dict_list):
    """
    Converts a list of dictionaries to a set of tuples.

    Args:
        dict_list: A list of dictionaries.

    Returns:
        set: A set of tuples.
    """
    return set(tuple(sorted(d.items())) for d in dict_list)

def handle_dry_run(data, publisher_id, reporting_table=False):
    """
    Handles the dry run by saving table data to a local JSON file.

    Args:
        data: The list of apps data.
        publisher_id: The publisher ID used in the filename.

    Returns:
        None
    """
    try:
        json_string = json.dumps(data)
        file_name = ''

        if reporting_table:
            file_name = f'mediation_reports {publisher_id}.json'
        else:
            file_name = f'apps_list {publisher_id}.json'

        with open(file_name, 'w') as outfile:
            outfile.write(json_string)

        custom_logging("Dry run complete (local machine).")
    except Exception as e:
        exception_logging(e)

def exception_logging(e):
    """
    Logs an exception to the console, extracting and printing the stack trace along with the error message

    Args:
        e: The exception object.

    Returns:
        None
    """
    tb = traceback.format_exc()
    custom_logging(f"An error occurred: {e}\nTraceback: {tb}")

def setup_bigquery(publisher_id, reporting_table=False):
    """
    Sets up the BigQuery dataset and table.

    Args:
        publisher_id: The publisher ID used for naming the dataset and table.
        reporting_table: Boolean (flag) to determine whether the setup is for apps list or reporting table

    Returns:
        bigquery.Client: The BigQuery client.
        str: The table ID.
    """
    gcp_project = os.environ.get('GCP_PROJECT')
    dataset_id = "admob_reporting_data"

    if reporting_table:
        table_id = f"{gcp_project}.{dataset_id}.admob_mediation_report_{publisher_id}"
    else:
        table_id = f"{gcp_project}.{dataset_id}.list_apps_{publisher_id}"

    client = bigquery.Client(project=gcp_project)
    create_dataset(client, dataset_id)
    create_table(client, table_id, reporting_table)

    return client, table_id

def create_dataset(client, dataset_id):
    """
    Creates a dataset in BigQuery if it doesn't already exist.

    Args:
        client: A BigQuery client object.
        dataset_id: The ID of the dataset.

    Returns:
        None
    """
    try:
        client.get_dataset(dataset_id)
        custom_logging(f"Dataset {dataset_id} already exists.")
    except NotFound:
        dataset = client.create_dataset(dataset_id)
        custom_logging(f"Dataset {dataset.dataset_id} created.")

def create_table(client, table_id, reporting_table=False):
    """
    Creates a table in BigQuery if it doesn't already exist.

    Args:
        client: A BigQuery client object.
        table_id: The ID of the table.
        reporting_table: Boolean flag to indicate if it's a reporting table.

    Returns:
        None
    """
    try:
        client.get_table(table_id)
        custom_logging(f"Table {table_id} already exists.")
    except NotFound:
        schema = get_table_schema(reporting_table)
        table = bigquery.Table(table_id, schema=schema)
        table = client.create_table(table)
        custom_logging(f"Table {table_id} created.")
        if reporting_table:
            configure_reporting_table(table, table_id)

def get_table_schema(reporting_table):
    """
    Returns the schema for a table.

    Args:
        reporting_table: Boolean flag to indicate if it's a reporting table.

    Returns:
        List[bigquery.SchemaField]: The schema for the table.
    """
    schema = None
    if reporting_table:
        # Define the schema for the reporting table
        schema = [
            bigquery.SchemaField("dimensionValues_DATE_value", 'DATE', mode="NULLABLE"),
            bigquery.SchemaField("dimensionValues_COUNTRY_value", 'STRING', mode="NULLABLE"),
            bigquery.SchemaField("dimensionValues_APP_value", 'STRING', mode="NULLABLE"),
            bigquery.SchemaField("dimensionValues_APP_displayLabel", 'STRING', mode="NULLABLE"),
            bigquery.SchemaField("metricValues_ESTIMATED_EARNINGS_microsValue", 'INTEGER', mode="NULLABLE"),
            bigquery.SchemaField("metricValues_IMPRESSIONS_integerValue", 'INTEGER', mode="NULLABLE"),
        ]
    else:
        schema = [
            bigquery.SchemaField("app_id", 'STRING', mode="NULLABLE"),
            bigquery.SchemaField("app_store_id", 'STRING', mode="NULLABLE"),
            bigquery.SchemaField("app_store_display_name", 'STRING', mode="NULLABLE"),
        ]
    return schema

def configure_reporting_table(table, table_id):
    """
    Configures the time partitioning and clustering fields for a reporting table.

    Args:
        table: The BigQuery table object.
        table_id: The ID of the table.

    Returns:
        None
    """
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="dimensionValues_DATE_value",  # name of column to use for partitioning
    )
    # Enable "require where clause to query data"
    table.require_partition_filter = True
    table.clustering_fields = ["dimensionValues_APP_value", "dimensionValues_COUNTRY_value"]
    custom_logging(f"Reporting table {table_id} configured with time partitioning and clustering.")

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
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )

    for i in range(5):  # max retries for load job
        try:
            job = client.load_table_from_json(data, table_id, job_config=job_config)
            custom_logging(f'Job ID: {job.job_id}')
            job.result()  # Waits for the job to complete.
            break
        except Exception as e:
            custom_logging(f'Error on attempt {i+1}:')
            exception_logging(e)
            time.sleep(10 * (2 ** i)) # retry delay = 10

def log_bigquery_table_info(client, table_id):
    """
    Logs information about a BigQuery table such as the number of rows and columns.

    Args:
        client: The BigQuery client.
        table_id: The ID of the table.

    Returns:
        None
    """
    table = client.get_table(table_id)  # Make an API request.
    custom_logging(f"{table.num_rows} rows and {len(table.schema)} columns in {table_id}")

def generate_mediation_report(service, publisher_id, backfill=False, dry_run=True, start_date=None, end_date=None):
    """
    Generates and prints a mediation report.

    Args:
        service: An AdMob Service Object.
        backfill: A Boolean flag to indicate whether to backfill data or not.
        dry_run: A Boolean flag to indicate whether to run the function in dry-run mode.
        start_date: The start date.
        end_date: The end date.

    Returns:
        None

    """
    custom_logging(f'Generating mediation report for {publisher_id}')

    date_range = setup_date_range_for_report(backfill, start_date, end_date)

    custom_logging(date_range)

    report_spec = setup_mediation_report_spec(date_range)

    if not dry_run:
        client, table_id = setup_bigquery(publisher_id, True)

        if is_existing_data_in_date_range(client, table_id, end_date, start_date):
            custom_logging(f"There are already rows in the table with the specified date value: {end_date} - {start_date}")
            return

    data = []

    if backfill:
        batch_size = determine_optimal_batch_size(service, publisher_id, report_spec, start_date, end_date)
        custom_logging(f'Batch size (# of days): {batch_size}')

        data = process_batches(service, publisher_id, report_spec, batch_size, start_date, end_date)
    else:
        _, data = execute_mediation_report_request(service, publisher_id, report_spec)

    data = flatten_and_format_data(data)

    if dry_run:
        handle_dry_run(data, publisher_id, reporting_table=True)
    else:
        load_data_to_bigquery(client, data, table_id)
        log_bigquery_table_info(client, table_id)

def setup_date_range_for_report(backfill, start_date, end_date):
    """
    Sets up the date range for the report, especially for backfilling.

    Args:
        backfill: Boolean indicating if backfilling is needed.
        start_date (datetime.date): The start date of the report.
        end_date (datetime.date): The end date of the report.

    Returns:
        dict: The date range for the report.
    """
    if backfill and start_date:
        return create_date_range(start_date, end_date)
    else:
        return create_date_range(end_date, end_date)

def create_latest_date_to_run():
    """
    Provide a default latest date to run for when no dates were provided.

    Returns:
        latest_date_to_run: The default latest date to run.
    """
    tz = pytz.timezone('America/Los_Angeles')
    datetime_now = datetime.now(tz)
    cloud_scheduler_time = datetime(year=datetime_now.year, month=datetime_now.month, day=datetime_now.day, hour=2, minute=0, tzinfo=tz)

    if datetime_now < cloud_scheduler_time:
        latest_date_to_run = datetime_now.date() - timedelta(days=2)
    else:
        latest_date_to_run = datetime_now.date() - timedelta(days=1)

    return latest_date_to_run

def create_date_range(start_date, end_date):
    """Creates a date range dictionary with start and end dates.

    Args:
        start_date (datetime.date): The start date of the batch.
        end_date (datetime.date): The end date of the batch.

    Returns:
        dict: A dictionary representing the date range.
    """
    return {
        'start_date': date_object_to_dict_object(start_date),
        'end_date': date_object_to_dict_object(end_date)
    }

def date_object_to_dict_object(date_object):
    """Converts a date object to a dict object.

    Args:
        date_object (datetime.date): The date object to convert.

    Returns:
        dict: A dictionary representing the date object.
    """
    return {
            'year': date_object.year,
            'month': date_object.month,
            'day': date_object.day
    }

def setup_mediation_report_spec(date_range):
    """
    Sets up the report parameters like dimensions, metrics, and sort conditions.

    Args:
        date_range: The date range for the report.

    Returns:
        dict: The mediation report specifications.
    """
    dimensions = ['DATE', 'APP', 'COUNTRY']
    metrics = ['ESTIMATED_EARNINGS', 'IMPRESSIONS']
    sort_conditions = {'dimension': 'DATE', 'order': 'ASCENDING'}
    dimension_filters = {'dimension': 'PLATFORM', 'matchesAny': {'values': ['Android']}}

    return {
        'date_range': date_range,
        'dimensions': dimensions,
        'metrics': metrics,
        'sort_conditions': [sort_conditions],
        'dimension_filters': [dimension_filters],
        'localization_settings': {'currency_code': 'USD'},
    }

def is_existing_data_in_date_range(client, table_id, end_date, start_date=None):
    """
    Checks if there are existing rows in the table within the specified start (optional) and end date range.

    Args:
        client: The BigQuery client.
        table_id: The table ID to query.
        end_date: The end date of the range.
        start_date: The start date of the range.

    Returns:
        int: 1 if data exists for the given date range, 0 otherwise.
    """
    query = ''

    if start_date:
        query = f'''
            SELECT *
            FROM `{table_id}`
            WHERE dimensionValues_DATE_value BETWEEN '{start_date.strftime("%Y-%m-%d")}' and '{end_date.strftime("%Y-%m-%d")}'
            LIMIT 10
        '''
    else:
        query = f'''
            SELECT *
            FROM `{table_id}`
            WHERE dimensionValues_DATE_value = '{end_date.strftime("%Y-%m-%d")}'
            LIMIT 10
        '''

    job = client.query(query)
    results = job.result()
    return 1 if results.total_rows > 0 else 0

def determine_optimal_batch_size(service, publisher_id, report_spec, start_date, end_date):
    """
    Performs a binary search to find the optimal end date for backfilling.

    Args:
        service: The AdMob service object.
        publisher_id: The publisher ID.
        report_spec: The specifications for the mediation report.
        start_date: The start date of the report.
        end_date: The end date of the report.

    Returns:
        integer: The optimal batch size for each api call found via binary search.
    """
    left, right = 1, (end_date - start_date).days + 1
    while left <= right:
        mid = (left + right) // 2
        end_date_mid = start_date + timedelta(days=mid - 1)

        report_spec["date_range"]["end_date"] = date_object_to_dict_object(end_date_mid)

        # Execute the mediation report for the date range
        num_rows, _ = execute_mediation_report_request(service, publisher_id, report_spec)

        # Adjust the search range based on the number of rows
        if num_rows > 80000:  # Reduce the range if too many rows
            right = mid - 1
        else:  # Increase the range if the number of rows is manageable
            left = mid + 1

    return right

def process_batches(service, publisher_id, report_spec, initial_batch_size, start_date, end_date):
    """
    Processes batches of data, adjusting the date range for each batch.

    Args:
        service: The AdMob service object.
        publisher_id: The publisher ID.
        report_spec: The specifications for the mediation report.
        initial_batch_size: The size of each batch in days.

    Returns:
        list: The data from the mediation report.
    """
    batch_size = initial_batch_size
    offset = 0
    batch_count = 0
    data = []
    while True:
        batch_start_date = start_date + timedelta(days=max(offset, 0))
        batch_end_date = min(batch_start_date + timedelta(days=batch_size - 1), end_date)
        batch_count += 1

        report_spec["date_range"] = {
            'start_date': date_object_to_dict_object(batch_start_date),
            'end_date': date_object_to_dict_object(batch_end_date)
        }

        custom_logging(f'Batch #{batch_count}: {report_spec["date_range"]}')

        num_rows, response = execute_mediation_report_request(service, publisher_id, report_spec)

        if num_rows > 100000:
            logging.warning('Report not fully retrieved from the AdMob API due to number of rows in response exceeding 100k')
            batch_size -= 2  # Aggressively decrease batch size to avoid hitting the record retrieval limit
            continue

        data.extend(response[1:-1])  # Append the data from the current batch to the overall data list

        # Increment the offset by the number of days in the current batch
        offset += batch_size

        # If the end date of the current batch is equal to the overall end date, we have retrieved all the data and can break out of the loop
        if batch_end_date == end_date:
            break

    custom_logging(f'Processed {batch_count} batches')

    return data

def execute_mediation_report_request(service, publisher_id, report_spec):
    """
    Executes the mediation report request and processes the response.

    Args:
        service: The AdMob service object.
        publisher_id: The publisher ID for the request.
        report_spec: The specifications for the mediation report.

    Returns:
        tuple: The number of rows in the report and the report data.
    """
    request = {'report_spec': report_spec}
    response = service.accounts().mediationReport().generate(
        parent=f'accounts/{publisher_id}', body=request).execute()

    if 'matchingRowCount' not in response[-1]['footer']:
        custom_logging('Warning: This account does not contain any records for this dates.')
        return 0, []

    num_rows = int(response[-1]['footer']['matchingRowCount'])
    data = response[1:-1]
    return num_rows, data

def flatten_and_format_data(data):
    """
    Flattens the JSON data and formats specific fields.

    Args:
        data: The list of data to be processed.

    Returns:
        List[dict]: The processed data.
    """
    processed_data = []
    for row in data:
        flattened_row = flatten(row['row'])
        if should_remove_row(flattened_row):
            continue
        processed_row = post_process_flattened_row(flattened_row)
        processed_data.append(processed_row)
    return processed_data

def should_remove_row(row):
    """
    Determines whether a data row should be removed based on certain conditions.

    Args:
        row: The data row.

    Returns:
        bool: True if the row should be removed, False otherwise.
    """
    # Remove entries with zero impressions and earnings
    if (row.get('metricValues_IMPRESSIONS_integerValue') == 0 and row.get('metricValues_ESTIMATED_EARNINGS_microsValue') == 0):
        return True
    else:
        return False

def post_process_flattened_row(row):
    """
    Performs post-processing on the flattened row.

    Args:
        row: The row of flattened data.

    Returns:
        dict: The post-processed data row.
    """
    # Handling missing 'dimensionValues_COUNTRY_value'
    if 'dimensionValues_COUNTRY' in row:
        row.pop('dimensionValues_COUNTRY')
        row['dimensionValues_COUNTRY_value'] = 'Unknown Region'

    # Formatting 'dimensionValues_DATE_value'
    if 'dimensionValues_DATE_value' in row:
        date_string = row['dimensionValues_DATE_value']
        year, month, day = int(date_string[:4]), int(date_string[4:6]), int(date_string[6:])
        row['dimensionValues_DATE_value'] = datetime(year, month, day).strftime("%Y-%m-%d")

    return row

@functions_framework.cloud_event
def admob_report_main(cloud_event):
    """
    Main entry point for the Cloud Function triggered from a message on a Cloud Pub/Sub topic.

    Args:
        cloud_event: The Cloud Event that triggered the function.

    Returns:
        None
    """
    # Extract parameters from the cloud event
    params = extract_parameters_from_event(cloud_event)

    validate_params(params)

    iterate_over_tokens(params)

def extract_parameters_from_event(cloud_event):
    """
    Extracts parameters from the Cloud Event data.

    Args:
        cloud_event: The Cloud Event object.

    Returns:
        dict: Extracted parameters.
    """
    specified_pub_id, start_date, end_date = None, None, None
    dry_run, populate_apps_list, backfill = False, True, False

    if cloud_event.data["message"].get("attributes") is not None:
        attr_dic = cloud_event.data["message"]["attributes"]

        specified_pub_id = attr_dic.get("pub_id")

        start_date = attr_dic.get("start_date")
        end_date = attr_dic.get("end_date")

        # Convert dry_run, populate_apps_list and backfill to boolean if necessary
        dry_run = attr_dic.get("dry_run", dry_run)
        dry_run = dry_run in ['True', 'true', 'TRUE']

        populate_apps_list = attr_dic.get("populate_apps_list", populate_apps_list)
        populate_apps_list = populate_apps_list in ['True', 'true', 'TRUE']

        backfill = attr_dic.get("backfill", backfill)
        backfill = backfill in ['True', 'true', 'TRUE']

    return {
        "specified_pub_id": specified_pub_id,
        "start_date": start_date,
        "end_date": end_date,
        "dry_run": dry_run,
        "populate_apps_list": populate_apps_list,
        "backfill": backfill,
    }

def validate_params(params):
    """
    Validates the parameters

    Args:
        params: Dictionary of report parameters.

    Returns:
        dict: Validated parameters.
    """
    backfill = params.get("backfill")
    start_date = params.get("start_date")
    end_date = params.get("end_date")

    if backfill and not start_date:
        error_msg = "Backfill is enabled but no start date is specified."
        raise ValueError(error_msg)

    params["start_date"], params["end_date"] = handle_dates_for_backfill_scenario(start_date, end_date)

    return params

def handle_dates_for_backfill_scenario(start_date, end_date):
    """
    Handles backfill start and end dates based on environment variables and command-line arguments.

    Args:
        params: The parameters dictionary containing command-line arguments and environment variables to process report generation

    Returns:
        tuple: The start and end dates for the report.
    """
    if start_date:
        start_date = create_date_object(start_date)

    if end_date:
        end_date = create_date_object(end_date)
    else:
        end_date = create_latest_date_to_run()

    if end_date and start_date and start_date > end_date:
        error_msg = f'Start date {start_date} must be before or equal to end date {end_date}.'
        raise ValueError(error_msg)

    return start_date, end_date

def create_date_object(date_string):
    """
    Creates a date object from a date string.

    Args:
        date_string: The date string received from the user.

    Returns:
        datetime.date: The date object.
    """

    year, month, day = date_string.split('-')

    return datetime(year=int(year), month=int(month), day=int(day)).date()

def iterate_over_tokens(params):
    """
    Iterates over token files, authenticates and processes each one.

    Args:
        params: Dictionary of report parameters.

    Returns:
        None
    """
    global TOTAL_TOKENS, TOKEN_NUMBER

    token_files = admob_utils.list_files_with_prefix('token')

    TOTAL_TOKENS = len(token_files)

    specified_pub_id = params.get("specified_pub_id")

    for token_num, token_f in enumerate(token_files):
        try:
            TOKEN_NUMBER = token_num + 1

            pub_id_of_token = admob_utils.extract_publisher_id(token_f)

            # Check if the specified publisher ID matches the publisher ID of the current token to update total tokens and token count
            # If it doesn't matches and a specified pub id does exists, then skip that token
            if specified_pub_id:
                if specified_pub_id == pub_id_of_token:
                    TOTAL_TOKENS = 1
                    TOKEN_NUMBER =  1
                else:
                    continue

            service = admob_utils.authenticate(token_f)

            # Process report for the current token
            process_report_generation(service, pub_id_of_token, params)

        except Exception as e:
            exception_logging(e)

def process_report_generation(service, pub_id_of_token, params):
    """
    Processes the report generation based on the provided parameters.

    Args:
        service: The AdMob service object.
        pub_id_of_token: The publisher ID associated with the currently authenticated token
        params: The parameters dictionary to process the report generation. Relevant params are:
            start_date: The start date.
            end_date: The end date.
            dry_run: Boolean indicating if dry run is enabled.
            populate_apps_list: Boolean indicating if the list of apps should be populated.
            backfill: Boolean indicating if backfill is enabled.

    Returns:
        None
    """
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    dry_run = params.get("dry_run")
    populate_apps_list = params.get("populate_apps_list")
    backfill = params.get("backfill")

    # Call list_apps if required
    if populate_apps_list:
        list_apps(service, pub_id_of_token, dry_run)

    # Generate the mediation report
    generate_mediation_report(service, pub_id_of_token, backfill, dry_run, start_date, end_date)

def main():
    """
    Main function for the script when run as a command-line interface.
    """
    params = process_cli_args()

    params = validate_params(params)

    if params.get("generate_token_only"):
        admob_utils.authenticate()
        return

    iterate_over_tokens(params)

def process_cli_args():
    """
    Processes command-line arguments.

    Returns:
        dict: Extracted parameters from the command-line arguments.
    """
    # Check for command-line flags
    parser = argparse.ArgumentParser(description="Process command line flags.")
    parser.add_argument('-p', '--specified-pub-id', type=str, help="Specify a Publisher ID to only run reports for that ID.")
    parser.add_argument('-sd', '--start-date', type=str, help="Start date in YYYY-MM-DD format.")
    parser.add_argument('-ed', '--end-date', type=str, help="End date in YYYY-MM-DD format. Defaults to today if not specified")
    parser.add_argument('-r', '--disable-dry-run', action='store_false', help="Execute a dry run without making any actual changes. Defaults to True if not specified.")
    parser.add_argument('-a', '--populate-apps-list', action='store_true', help="Populate the applications list. Use this flag to enable this feature.")
    parser.add_argument('-b', '--backfill', action='store_true', help="Enable backfilling of data. Use this flag to activate backfill.")
    parser.add_argument('-t', '--generate-token-only', action='store_true', help="Generate an access token only")
    args = parser.parse_args()

    # Store the value of the --disable-dry-run flag in the variable dry_run
    dry_run = args.disable_dry_run

    return {
        "specified_pub_id": args.specified_pub_id,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "dry_run": dry_run,
        "populate_apps_list": args.populate_apps_list,
        "backfill": args.backfill,
        "generate_token_only": args.generate_token_only
    }

# Entry point for the script
if __name__ == "__main__":
    main()