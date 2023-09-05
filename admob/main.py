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

import admob_utils
from google.cloud import bigquery
from google.api_core.exceptions import NotFound
import json
from flatten_json import flatten
import functions_framework
from datetime import date, datetime, timedelta
import pytz
import os
import sys
import time
import logging
import os


PUBLISHER_ID = os.environ.get('PUBLISHER_ID')
TOKEN_NUMBER = 0
TOTAL_TOKENS = 0


def custom_logging(message):
    print(f'Token # {TOKEN_NUMBER}/{TOTAL_TOKENS} - {message}')


def list_apps(service, dry_run=False):
    """Lists all apps under an AdMob account.

    Args:
        service: An AdMob Service Object.
    """
    custom_logging(f'Generating apps list for {PUBLISHER_ID}')

    data = []
    next_page_token = ''

    while True:
        # Execute the request.
        response = service.accounts().apps().list(
            pageSize=100,
            pageToken=next_page_token,
            parent='accounts/{}'.format(PUBLISHER_ID)).execute()

        # Check if the response is empty.
        if not response:
            break

        # Print the result.
        apps = response['apps']
        for app in apps:
            if 'linkedAppInfo' in app and app['platform'] == 'ANDROID':
                data.append({
                    'app_id': app['appId'],
                    'app_store_id': app['linkedAppInfo']['appStoreId'],
                    'app_store_display_name': app['linkedAppInfo']['displayName'] if 'displayName' in app['linkedAppInfo'] else None,
                })

        if 'nextPageToken' not in response:
            break

        # Update the next page token.
        next_page_token = response['nextPageToken']
    
    if dry_run:
        try:
            # Test out by saving the data to a local json file.
            json_string = json.dumps(data)
            with open(f'apps_list {PUBLISHER_ID}.json', 'w') as outfile:
                outfile.write(json_string)
            custom_logging("dry run complete (local machine) \n")
        except Exception:
            custom_logging("dry run complete (cloud function) \n")
    else:
        client = bigquery.Client(project=os.environ.get('GCP_PROJECT'))

        dataset_id = "admob_reporting_data"

        # Check if the dataset already exists
        dataset_exists = False
        try:
            client.get_dataset(dataset_id)
            dataset_exists = True
        except NotFound:
            pass

        if not dataset_exists:
            # The dataset does not exist, so create it
            dataset = client.create_dataset(dataset_id)
            custom_logging(f"Dataset {dataset.dataset_id} created.")
        else:
            custom_logging(f"Dataset {dataset_id} already exists.")

        table_id = f"{os.environ.get('GCP_PROJECT')}.{dataset_id}.list_apps_{PUBLISHER_ID}"

        # Check if table exists
        table_exists = False
        try:
            client.get_table(table_id)
            table_exists = True
        except NotFound:
            pass

        # Create table if it doesn't exist
        if not table_exists:
            table = bigquery.Table(table_id)
            table = client.create_table(table)
            custom_logging(f"Table {table.table_id} created.")
        else:
            custom_logging(f"Table {table_id} already exists.")

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON, autodetect=True, write_disposition=bigquery.WriteDisposition.WRITE_APPEND)

        job = client.load_table_from_json(
            data, table_id, job_config=job_config)

        job.result()  # Waits for the job to complete.

        table = client.get_table(table_id)  # Make an API request.
        custom_logging(
            "{} rows and {} columns in {}".format(
                table.num_rows, len(table.schema), table_id
            )
        )


def generate_network_report(service, backfill=False, dry_run=False, start_date_year='2022', start_date_month='1', start_date_day='1', end_date_year=None, end_date_month=None, end_date_day=None):
    """Generates and prints a network report.

    Args:
      service: An AdMob Service Object.
      backfill: A Boolean flag to indicate whether to backfill data or not.
      dry_run: A Boolean flag to indicate whether to run the function in dry-run mode.
      populate_apps_list: A Boolean flag to indicate whether to call the `list_apps()` function.
      start_date_year: The year of the start date.
      start_date_month: The month of the start date.
      start_date_day: The day of the start date.
      end_date_year: The year of the end date (optional).
      end_date_month: The month of the end date (optional).
      end_date_day: The day of the end date (optional).
    """
    # Set date range. AdMob API only supports the account default timezone and
    # "America/Los_Angeles", see
    # https://developers.google.com/admob/api/v1/reference/rest/v1/accounts.networkReport/generate
    # for more information.
    custom_logging(f'Generating network report for {PUBLISHER_ID}')

    tz = pytz.timezone('America/Los_Angeles')
    datetime_now = datetime.now(tz)

    # Set dimensions.
    dimensions = ['DATE', 'APP', 'COUNTRY']

    # Set metrics.
    metrics = ['ESTIMATED_EARNINGS', 'IMPRESSIONS']

    # Set sort conditions.
    sort_conditions = {'dimension': 'DATE', 'order': 'ASCENDING'}

    # Set dimension metrics.
    dimension_filters = {'dimension': 'PLATFORM', 'matchesAny': {'values': ['Android']}}

    data = []

    if not dry_run:
        dataset_id = "admob_reporting_data"
        table_id = f"{os.environ.get('GCP_PROJECT')}.{dataset_id}.admob_network_report_{PUBLISHER_ID}"
        
        # Construct a BigQuery client object.
        client = bigquery.Client(project=os.environ.get('GCP_PROJECT'))

        # Check if the dataset already exists
        dataset_exists = False
        try:
            client.get_dataset(dataset_id)
            dataset_exists = True
        except NotFound:
            pass

        if not dataset_exists:
            # The dataset does not exist, so create it
            dataset = client.create_dataset(dataset_id)
            custom_logging(f"Dataset {dataset.dataset_id} created.")
        else:
            custom_logging(f"Dataset {dataset_id} already exists.")

        # Check if table exists
        table_exists = False
        try:
            client.get_table(table_id)
            table_exists = True
        except NotFound:
            pass

        # Create table if it doesn't exist
        if not table_exists:
            # Create a schema
            schema = [
                bigquery.SchemaField("dimensionValues_DATE_value", 'DATE', mode="NULLABLE"),
                bigquery.SchemaField("dimensionValues_COUNTRY_value", 'STRING', mode="NULLABLE"),
                bigquery.SchemaField("dimensionValues_APP_value", 'STRING', mode="NULLABLE"),
                bigquery.SchemaField("dimensionValues_APP_displayLabel", 'STRING', mode="NULLABLE"),
                bigquery.SchemaField("metricValues_ESTIMATED_EARNINGS_microsValue", 'INTEGER', mode="NULLABLE"),
                bigquery.SchemaField("metricValues_IMPRESSIONS_integerValue", 'INTEGER', mode="NULLABLE"),
            ]
            table = bigquery.Table(table_id, schema=schema)
            table = client.create_table(table)
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="dimensionValues_DATE_value",  # name of column to use for partitioning
            )
            # Enable "require where clause to query data"
            table.require_partition_filter = True
            table.clustering_fields = ["dimensionValues_APP_value", "dimensionValues_COUNTRY_value"]
            custom_logging(f"Table {table.table_id} created.")
        else:
            custom_logging(f"Table {table_id} already exists.")

    if not backfill:
        end_date = datetime_now.date() - timedelta(days=1)
        date_range = {
            'start_date': {'year': end_date.year, 'month': end_date.month, 'day': end_date.day},
            'end_date': {'year': end_date.year, 'month': end_date.month, 'day': end_date.day}
        }

        # Run a query to check if there are any rows in the table with the specified date value
        # And, run it just for the first token loop instance to avoid checking for data just added during current function run 
        if not dry_run and TOKEN_NUMBER == 1:
            query = """
                SELECT *
                FROM `{}`
                WHERE dimensionValues_DATE_value = '{}'
                LIMIT 10
            """.format(table_id, end_date.strftime("%Y-%m-%d"))

            # Run the query and get the results.
            job = client.query(query)
            results = job.result()

            # Check if the number of rows in the results is greater than 0.
            if results.total_rows > 0:
                custom_logging(f"There are already rows in the table with the specified date value: {end_date}")
                return 1
                
        custom_logging(date_range)

        # Create network report specifications.
        report_spec = {
            'date_range': date_range,
            'dimensions': dimensions,
            'metrics': metrics,
            'sort_conditions': [sort_conditions],
            'dimension_filters': [dimension_filters],
            'localization_settings': {'currency_code': 'USD'},
        }

        # Create network report request.
        request = {'report_spec': report_spec}

        # Execute network report request.
        response = service.accounts().networkReport().generate(
            parent='accounts/{}'.format(PUBLISHER_ID), body=request).execute()
        
        if 'matchingRowCount' not in response[-1]['footer']:
            custom_logging('Warning: This account does not contain any records for this dates \n')
            return

        num_rows = int(response[-1]['footer']['matchingRowCount'])
        data = response[1:-1]
    else:
        if not end_date_year:
            end_date = datetime_now.date() - timedelta(days=1)
        else:
            end_date = datetime(year=int(end_date_year), month=int(end_date_month), day=int(end_date_day)).date()

        # if the cloud scheduler is still going to run for yesterday at 02:00, then start the backfill from the day before yesterday (two days ago)
        cloud_scheduler_time = datetime(year=datetime_now.year, month=datetime_now.month, day=datetime_now.day, hour=2, minute=0, tzinfo=tz)
        if datetime_now < cloud_scheduler_time and not end_date_year:
            end_date = datetime_now.date() - timedelta(days=2)

        if (start_date_year and start_date_month and start_date_day):
            start_date = datetime(year=int(start_date_year), month=int(start_date_month), day=int(start_date_day)).date()
        else:
            return

        date_range = {
            'start_date': {'year': start_date.year, 'month': start_date.month, 'day': start_date.day},
            'end_date': {'year': end_date.year, 'month': end_date.month, 'day': end_date.day}
        }

        # Run a query to check if there are any rows in the table with the specified date value
        # And, run it just for the first token loop instance to avoid checking for data just added during current function run
        if not dry_run and TOKEN_NUMBER == 1:
            query = """
                SELECT *
                FROM `{}`
                WHERE dimensionValues_DATE_value BETWEEN '{}' and '{}'
                LIMIT 10
            """.format(table_id, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))

            # Run the query and get the results.
            job = client.query(query)
            results = job.result()

            # Check if the number of rows in the results is greater than 0.
            if results.total_rows > 0:
                custom_logging(f"There are already rows in the table within the specified start and end date range: {start_date} - {end_date}")
                return 1

        custom_logging(date_range)
        
        left, right = 1, (end_date - start_date).days + 1
        while left <= right:
            mid = (left + right) // 2
            end_date_mid = start_date + timedelta(days=mid - 1)

            date_range = {
                'start_date': {'year': start_date.year, 'month': start_date.month, 'day': start_date.day},
                'end_date': {'year': end_date_mid.year, 'month': end_date_mid.month, 'day': end_date_mid.day}
            }

            # Create network report specifications.
            report_spec = {
                'date_range': date_range,
                'dimensions': dimensions,
                'metrics': metrics,
                'sort_conditions': [sort_conditions],
                'localization_settings': {'currency_code': 'USD'},
            }

            # Create network report request.
            request = {'report_spec': report_spec}

            response = service.accounts().networkReport().generate(
                parent='accounts/{}'.format(PUBLISHER_ID), body=request).execute()

            if 'matchingRowCount' not in response[-1]['footer']:
                custom_logging('Warning: This account does not contain any records for this dates \n')
                return
            
            # If the total number of records in the response is less than 80k (record retrieval limit is 100k), try a larger batch size; otherwise, try a smaller batch size
            num_rows = int(response[-1]['footer']['matchingRowCount'])
            if num_rows < 80000:
                left = mid + 1
            else:
                right = mid - 1

        # The optimal batch size is the largest batch size that returns less than 80k rows 
        batch_size = right
        custom_logging(f'batch_size in days: {batch_size}')

        # Loop through each batch of data, adjusting the date range as needed
        offset = 0
        batch_count = 0
        while True:
            # Calculate the start and end dates for the current batch
            batch_start_date = start_date + timedelta(days=max(offset, 0))
            batch_end_date = min(batch_start_date + timedelta(days=batch_size - 1), end_date)
            batch_count += 1

            # Make the API request for the current batch
            date_range = {
                'start_date': {'year': batch_start_date.year, 'month': batch_start_date.month, 'day': batch_start_date.day},
                'end_date': {'year': batch_end_date.year, 'month': batch_end_date.month, 'day': batch_end_date.day}
            }

            custom_logging(f'batch #: {batch_count} - {date_range}')

            # Create network report specifications.
            report_spec = {
                'date_range': date_range,
                'dimensions': dimensions,
                'metrics': metrics,
                'sort_conditions': [sort_conditions],
                'localization_settings': {'currency_code': 'USD'},
            }

            # Create network report request.
            request = {'report_spec': report_spec}

            response = service.accounts().networkReport().generate(
                parent='accounts/{}'.format(PUBLISHER_ID), body=request).execute()


            num_rows = int(response[-1]['footer']['matchingRowCount'])
            custom_logging(f'batch num_rows: {num_rows}')
            if num_rows > 100000:
                logging.warning('Report not fully retrieved from the AdMob API due to number of rows in response exceeding 100k')
                batch_size -= 2 # decrease batch size aggresively until we don't hit the record retrieval limit on the API
                custom_logging(f'new batch_size in days: {batch_size}')
                continue

            # Append the data from the current batch to the overall data list
            data.extend(response[1:-1])
            
            # Increment the offset by the number of days in the current batch
            offset += batch_size
            
            # If the end date of the current batch is equal to the overall end date, we have retrieved all the data and can break out of the loop
            if batch_end_date == end_date:
                break
        

    # Flatten the json and map empty countries to 'Unknown Region' and format date in yyyy-mm-dd format.
    idx = 0
    data_length = len(data)
    while idx < data_length:
        data[idx] = flatten(data[idx]['row'])
        if 'dimensionValues_COUNTRY' in data[idx]: # after flattening the data json, dimensionValues_COUNTRY will only exist if dimensionValues_COUNTRY_value does not exist            
            data[idx].pop('dimensionValues_COUNTRY')
            data[idx]['dimensionValues_COUNTRY_value'] = 'Unknown Region'
        if 'dimensionValues_DATE_value' in data[idx]:
            date_string = data[idx]['dimensionValues_DATE_value']
            year, month, day = int(date_string[:4]), int(date_string[4:6]), int(date_string[6:])
            date = datetime(year, month, day)
            data[idx]['dimensionValues_DATE_value'] = date.strftime("%Y-%m-%d")
        if 'metricValues_IMPRESSIONS_integerValue' in data[idx] and data[idx]['metricValues_IMPRESSIONS_integerValue'] == 0  and 'metricValues_ESTIMATED_EARNINGS_microsValue' in data[idx] and data[idx]['metricValues_ESTIMATED_EARNINGS_microsValue'] == 0:
            del data[idx]
            data_length -= 1
            idx -= 1
        idx += 1

    if dry_run:
        try:
            # Test out by saving the data to a local json file.
            json_string = json.dumps(data)
            with open(f'admob_reports {PUBLISHER_ID}.json', 'w') as outfile:
                outfile.write(json_string)
            custom_logging("dry run complete (local machine) \n")
        except Exception:
            custom_logging("dry run complete (cloud function) \n")
    else:
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON, autodetect=True, write_disposition=bigquery.WriteDisposition.WRITE_APPEND)

        # max retries for load job = 5
        for i in range(5):
            try:
                job = client.load_table_from_json(
                    data, table_id, job_config=job_config)
                custom_logging(f'job id: {job.job_id}')
                job.result()  # Waits for the job to complete.
                break
            except Exception as e:
                custom_logging(f"Error loading job: {e}")
                time.sleep(10 * (2 ** i)) # retry delay = 10

        table = client.get_table(table_id)  # Make an API request.
        custom_logging(
            "{} rows and {} columns in {} \n".format(
                table.num_rows, len(table.schema), table_id
            )
        )


"""
This function generates a network report for Admob.

Args:
    cloud_event: The Cloud Event that triggered the function.

Raises:
    ValueError: If the start date is after the end date.

**Pub/Sub message attributes:**

* `backfill`: Whether to generate a backfill report.
* `dry_run`: Whether to run the function in dry-run mode.
* `populate_apps_list`: Whether to call the `list_apps()` function.
* `pub_id`: The publisher ID for the Admob account.
* `start_date`: The start date.
* `end_date`: The end date (optional).

"""
# Triggered from a message on a Cloud Pub/Sub topic.
@functions_framework.cloud_event
def admob_report_main(cloud_event):
    token_files = admob_utils.list_files_with_prefix('token')
    global TOTAL_TOKENS
    TOTAL_TOKENS = len(token_files)

    errors_occurred = False
    
    for token_num, token_f in enumerate(token_files):
        try:
            global PUBLISHER_ID
            PUBLISHER_ID = admob_utils.extract_publisher_id(token_f)

            global TOKEN_NUMBER
            TOKEN_NUMBER = token_num + 1

            service = admob_utils.authenticate(token_f)
            pub_id = None
            backfill = False
            dry_run = False
            populate_apps_list = False
            start_date_year = '2022'
            start_date_month = '1'
            start_date_day = '1'
            end_date_year = None
            end_date_month = None
            end_date_day = None

            # Get the attributes from the Cloud Event.
            if "attributes" in cloud_event.data["message"] and cloud_event.data["message"]["attributes"] is not None:
                attr_dic = cloud_event.data["message"]["attributes"]

                if "backfill" in attr_dic:
                    backfill = attr_dic["backfill"]
                if "dry_run" in attr_dic:
                    dry_run = attr_dic["dry_run"]
                if "populate_apps_list" in attr_dic:
                    populate_apps_list = attr_dic["populate_apps_list"]
                if "pub_id" in attr_dic:
                    pub_id = attr_dic["pub_id"]
                if "start_date" in attr_dic:
                    start_date_year, start_date_month, start_date_day = attr_dic["start_date"].split('-')
                if "end_date" in attr_dic:
                    end_date_year, end_date_month, end_date_day = attr_dic["end_date"].split('-')

            # Check if the publisher ID in the Cloud Event matches the current publisher ID.
            if pub_id:
                TOTAL_TOKENS = 1
                TOKEN_NUMBER = 1
                if pub_id != PUBLISHER_ID:
                    return

            # Check if backfill is enabled.
            if backfill == 'True' or backfill == 'true' or backfill == 'TRUE':
                backfill = True

            # Check if list_apps should be called.
            if populate_apps_list == 'True' or populate_apps_list == 'true' or populate_apps_list == 'TRUE':
                list_apps(service, dry_run)

            # Check if the start date is after the end date.
            if end_date_year:
                start_date = date(int(start_date_year), int(start_date_month), int(start_date_day))
                end_date = date(int(end_date_year), int(end_date_month), int(end_date_day))
                if start_date > end_date:
                    raise ValueError("Start date must be before end date.")
            
            # Generate the network report.
            stop_function = generate_network_report(service, backfill, dry_run, start_date_year, start_date_month, start_date_day, end_date_year, end_date_month, end_date_day)

            if stop_function:
                return
        except Exception as e:
            print(f"Error occurred in loop iteration: {e}")
            # Mark that an error occurred during the loop
            errors_occurred = True
            continue
    # Check if any errors occurred during the loop and raise an error if needed
    if errors_occurred:
      raise Exception("One or more errors occurred during the loop.")

"""
This script generates a network report for Admob.

Important variables:

    token_files: A list of files containing Admob tokens.
    PUBLISHER_ID: The publisher ID for the Admob account.
    service: The Admob service object.

Raises:
    ValueError: If the start date is after the end date.

Command line flags:

    --generate-token-only=true: Only generate the Admob token file.
    --apps-list=true: List and store list of all apps for the Admob account.
    --dry-run=[true,false]: Run/not run the script in dry-run mode.
    --backfill=[true,custom]: Do a backfill for the AdMob data (use `custom` to use a custom END_DATE value from the environment variable).

Environment variables:

    GCP_PROJECT: GCP project id (REQUIRED)
    START_DATE: start date for the backfill (optional - will default to 2022-01-01. May need to set when using backfill=custom to control backfill start and end dates)
    END_DATE: end date for the backfill (required when backfill is set to `custom`)
"""
if __name__ == "__main__":
    # Check if we only need to generate the AdMob refresh token 
    if len(sys.argv) > 1 and '--generate-token-only=true' in sys.argv:
        admob_utils.authenticate()
        exit()

    token_files = admob_utils.list_files_with_prefix('token')
    TOTAL_TOKENS = len(token_files)

    errors_occurred = False

    for token_num, token_f in enumerate(token_files):
        try:
            PUBLISHER_ID = admob_utils.extract_publisher_id(token_f)
            TOKEN_NUMBER = token_num + 1

            # if there is a pub id declared as an env variable and that pub id doesn't match the loop's current pub id, then skip that loop instance
            if 'PUB_ID' in os.environ:
                TOTAL_TOKENS = 1
                TOKEN_NUMBER = 1
                if os.environ.get('PUB_ID') != PUBLISHER_ID:
                    continue

            service = admob_utils.authenticate(token_f)
            backfill = False
            dry_run = True
            start_date_year = '2022'
            start_date_month = '1'
            start_date_day = '1'
            end_date_year = None
            end_date_month = None
            end_date_day = None

            # Check if it's not a dry run
            if len(sys.argv) > 1 and '--dry-run=false' in sys.argv:
                dry_run = False
            
            # Check if list_apps should be called.
            if len(sys.argv) > 1 and '--apps-list=true' in sys.argv:
                list_apps(service, dry_run)

            # Check if backfill is enabled.
            if len(sys.argv) > 1 and ('--backfill=true' in sys.argv or '--backfill=custom' in sys.argv):
                backfill = True
            
            if 'START_DATE' in os.environ:
                start_date_year, start_date_month, start_date_day = os.environ.get('START_DATE').split('-')
            if len(sys.argv) > 1 and '--backfill=custom' in sys.argv and 'END_DATE' in os.environ:
                end_date_year, end_date_month, end_date_day = os.environ.get('END_DATE').split('-')
            
            # Check if the start date is after the end date.
            if end_date_year:
                start_date = date(int(start_date_year), int(start_date_month), int(start_date_day))
                end_date = date(int(end_date_year), int(end_date_month), int(end_date_day))
                if start_date > end_date:
                    raise ValueError("Start date must be before end date.")
            
            # Generate the network report.
            stop_function = generate_network_report(service, backfill, dry_run, start_date_year, start_date_month, start_date_day, end_date_year, end_date_month, end_date_day)

            if stop_function:
                break
        except Exception as e:
            print(f"Error occurred in loop iteration: {e}")
            # Mark that an error occurred during the loop
            errors_occurred = True
            continue
    # Check if any errors occurred during the loop and raise an error if needed
    if errors_occurred:
      raise Exception("One or more errors occurred during the loop.")
