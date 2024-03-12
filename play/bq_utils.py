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

from google.cloud import bigquery
from google.cloud.exceptions import NotFound


def table_has_data_for_date_range(
    bq_client: bigquery.Client, table_id: str, start_date: str, end_date: str,
    date_field: str = 'Date'
) -> tuple[int, bool]:
  sql_query = '''
      SELECT COUNT(*) AS num_records
      FROM `{}`
      WHERE DATE({}) BETWEEN DATE('{}') AND DATE('{}');
  '''.format(table_id, date_field, start_date, end_date)
  query_job = bq_client.query(sql_query)
  query_result = [dict(row) for row in query_job]
  return (query_result[0]['num_records'],
          query_result[0]['num_records'] > 0)


def table_has_same_data_for_date_range(
    num_rows: int, bq_client: bigquery.Client, table_id: str,
    start_date: str, end_date: str, date_field: str = 'Date'
) -> bool:
  (num_records, has_data) = table_has_data_for_date_range(
      bq_client, table_id, start_date, end_date, date_field=date_field)
  return has_data and num_records == num_rows

def table_exists(bq_client: bigquery.Client, table_id: str) -> bool:
  try:
    bq_client.get_table(table_id)
    return True
  except NotFound:
    return False
