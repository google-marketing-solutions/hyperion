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

import pandas as pd
import numpy as np
import re
from io import StringIO, BytesIO
from datetime import datetime
from collections.abc import Mapping
from google.cloud import storage
from google.cloud.storage.blob import Blob
from dateutil.relativedelta import relativedelta
from zipfile import ZipFile


def get_blobs_data_frame_with_date_filter(
    report_type: str,
    blobs: list[Blob],
    date_filter: datetime,
    encoding: str = "utf-16",
    dry_run: bool = False,
) -> pd.DataFrame:
    """Reads report blobs into data frame filtered by date.

    Args:
      report_type: Report type strings such as Crashes and Installs etc.
      blobs: List of report blobs.
      date_filter: Datetime filter to rows in report according to date.
      encoding: Encoding of play report files in Cloud Storage, default is utf-16.
      dry_run: Boolean flag specifying the run is for testing.
    """
    return get_blobs_data_frame_with_date_range(
        report_type, blobs, date_filter, date_filter, encoding, dry_run
    )


def get_blobs_data_frame_with_date_range(
    report_type: str,
    blobs: list[Blob],
    start_date: datetime,
    end_date: datetime,
    encoding: str = "utf-16",
    dry_run: bool = False,
    date_fields: list = [],
) -> pd.DataFrame:
    """Reads report blobs into data frame filtered by date.

    Args:
      report_type: Report type strings such as Crashes and Installs etc.
      blobs: List of report blobs.
      start_date: Start date filter to rows later (including) the date.
      end_date: End date filter to rows earlier (including) the date.
      encoding: Encoding of play report files in Cloud Storage, default is utf-16.
      dry_run: Boolean flag specifying the run is for testing.
    """

    df = pd.DataFrame()

    for blob in blobs:
        if date_fields:
            dtypes = {
                "App Version Name": str,
                "App Version Code": str,
                "Developer Reply Text": str,
            }
        else:
            dtypes = {}
        if report_type == "Earnings":
            dtypes = {
                "Transaction Date": str,
                "Product id": str,
                "Buyer Country": str,
            }
            selected_columns = [
                "Transaction Date",
                "Product id",
                "Buyer Country",
                "Amount (Merchant Currency)",
            ]
            with blob.open(mode="rb") as zip_blob:
                zip_in_memory = BytesIO(zip_blob.read())
            with ZipFile(zip_in_memory, "r") as zip_ref:
                csv_file_name = zip_ref.namelist()[0]
                with zip_ref.open(csv_file_name) as csv_file:
                    df_report = pd.read_csv(
                        csv_file,
                        parse_dates=date_fields,
                        dtype=dtypes,
                        keep_default_na=False,
                        usecols=selected_columns,
                    )
            del zip_in_memory
        else:
            with blob.open(mode="r", encoding=encoding) as f:
                report_csv_string_io = StringIO(f.read())
            df_report = pd.read_csv(
                report_csv_string_io,
                parse_dates=date_fields,
                dtype=dtypes,
                keep_default_na=False,
            )
            del report_csv_string_io
        for date_field in date_fields:
            df_report[date_field] = pd.to_datetime(df_report[date_field])
        if date_fields:
            start_date = pd.to_datetime(start_date, utc=True)
            end_date = pd.to_datetime(end_date, utc=True)
        date_mask = (df_report[date_fields[0]].dt.floor("D") >= start_date) & (
            df_report[date_fields[0]].dt.floor("D") <= end_date
        )
        df_report = df_report.loc[date_mask]
        # if dry_run:
        #   df = pd.concat([df, df_report.head(5)]).reset_index(drop=True)
        # else:
        df = pd.concat([df, df_report]).reset_index(drop=True)

    if not df.empty:
        columns_renamed = dict(
            (column, re.sub("[^0-9a-zA-Z]+", "_", column)) for column in df.columns
        )
        df.rename(columns_renamed, axis=1, inplace=True)

        if date_fields:
            cols_int = [
                "App_Version_Code",
                "Developer_Reply_Millis_Since_Epoch",
                "Review_Submit_Millis_Since_Epoch",
                "Star_Rating",
            ]
            cols_str = [
                "Package_Name",
                "App_Version_Name",
                "Device",
                "Reviewer_Language",
                "Review_Title",
                "Review_Text",
                "Developer_Reply_Text",
                "Review_Link",
            ]
            df[cols_int] = df[cols_int].apply(pd.to_numeric, errors="coerce")
            df[cols_str] = df[cols_str].replace("", np.nan)

    return df


def find_files_from_cloud_storage(
    bucket: str, prefix: str, dimensions: list[str], date_filter: datetime
) -> Mapping[str, list[Blob]]:
    """Finds report file blobs from Cloud Storage.

    Args:
      bucket: Source cloud storage bucket name without gs:// prefix.
      prefix: Prefix used to filter blobs.
      dimensions: List of report dimensions, e.g. app_version, overview etc.
      date_filter: Datetime filter used to filter report names.

    Returns:
      A dict mapping dimension to list of report blobs. For example:

      {'app_version': [
        <Blob: bucket, report_a, 12345>,
        <Blob: bucket, report_b, 67890>]}
    """
    return find_files_from_cloud_storage_with_date_range(
        bucket, prefix, dimensions, date_filter, date_filter
    )


def find_files_from_cloud_storage_with_date_range(
    bucket: str,
    prefix: str,
    dimensions: list[str],
    start_date: datetime,
    end_date: datetime,
) -> Mapping[str, list[Blob]]:
    """Finds report file blobs from Cloud Storage.

    Args:
      bucket: Source cloud storage bucket name without gs:// prefix.
      prefix: Prefix used to filter blobs.
      dimensions: List of report dimensions, e.g. app_version, overview etc.
      start_date: Start date for date range.
      end_date: End date for date range.

    Returns:
      A dict mapping dimension to list of report blobs. For example:

      {'app_version': [
        <Blob: bucket, report_a, 12345>,
        <Blob: bucket, report_b, 67890>]}
    """

    def get_file_patterns_by_dimension(
        dimension: str, month_strings: list[str], prefix: str
    ) -> list[str]:
        """Gets file patterns by dimension.

        Args:
          dimension: Report dimension, e.g. app_version, overview etc.
          month_strings: List of month strings, e.g. ['202301', '202302'].
          prefix: Prefix used to filter blobs.

        Returns:
          A list of file patterns. For example:

          ['_202301_app_version.csv', '_202302_app_version.csv']
        """
        patterns = []
        for month in month_strings:
            if dimension:
                patterns.append("_{}_{}.csv".format(month, dimension))
            elif prefix == "earnings/earnings_":
                patterns.append("{}{}_".format(prefix, month))
            else:
                patterns.append("_{}".format(month))
        return patterns

    storage_client = storage.Client()
    blobs = storage_client.list_blobs(bucket, prefix=prefix)

    blobs_dimensions = {}
    blobs = list(blobs)
    month_strings = get_months_by_date_range(start_date, end_date)

    for dimension in dimensions:
        dimension_patterns = get_file_patterns_by_dimension(
            dimension, month_strings, prefix
        )
        _blobs = []
        for pattern in dimension_patterns:
            _blobs += list(filter(lambda b: re.search(pattern, b.name), blobs))
        blobs_dimensions[dimension] = _blobs
        # logger.log(
        #     'Found {} blobs for dimension {} and year month {}'.format(
        #         len(blobs_dimensions[dimension]), dimension, date_string))

    return blobs_dimensions


def get_months_by_date_range(start_date: datetime, end_date: datetime) -> list[str]:
    """Generates a list of year-month strings within a given date range.

    This function takes two datetime objects representing the start and end dates
    of a range and returns a list of strings in the format "YYYYMM" for each
    month within that range, inclusive of the start and end months.

    Args:
        start_date: The starting datetime object for the range.
        end_date: The ending datetime object for the range.

    Returns:
        A list of strings representing the year-month combinations within the
        date range, in the format "YYYYMM".
    """

    def year_month(dt: datetime) -> datetime:
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_strings = []

    while year_month(start_date) <= year_month(end_date):
        month_strings.append(start_date.strftime("%Y%m"))
        start_date = start_date + relativedelta(months=1)
    return month_strings
