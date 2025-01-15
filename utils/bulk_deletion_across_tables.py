# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This script deletes rows from all tables in a dataset that match a given condition.

Usage:
    1. Replace "project-id", "dataset-id", "COLUMN", and "value".
    2. Run the script.
"""

from google.cloud import bigquery

# Initialize a BigQuery client
client = bigquery.Client(project="project-id")

# Define your dataset and table name pattern
dataset_id = "dataset-id"
table_prefix = "analytics_report_"

# Define the condition for row deletion
deletion_condition = "COLUMN >= 'value'"  # Example: "date < '2021-01-01'"

# List all tables in the dataset
tables = client.list_tables(dataset_id)
table_names = [table.table_id for table in tables if table_prefix in table.table_id]

# Loop over tables and delete rows matching the condition
for table_name in table_names:
    query = f"""
        DELETE FROM `{dataset_id}.{table_name}`
        WHERE {deletion_condition}
    """
    query_job = client.query(query)  # Run the query
    query_job.result()  # Wait for the job to complete

    print(f"Rows deleted from table {table_name}")

# Complete
print("Deletion complete.")
