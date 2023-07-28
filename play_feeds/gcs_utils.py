import pandas as pd
import numpy as np
import re
from io import StringIO
from datetime import datetime
from collections.abc import Mapping
from google.cloud import storage
from google.cloud.storage.blob import Blob
from dateutil.relativedelta import relativedelta


def get_blobs_data_frame_with_date_filter(
    blobs: list[Blob], date_filter: datetime,
    encoding: str = 'utf-16', dry_run: bool = False
) -> pd.DataFrame:
  """Reads report blobs into data frame filtered by date.

  Args:
    blobs: List of report blobs.
    date_filter: Datetime filter to rows in report according to date.
    encoding: Encoding of play report files in Cloud Storage, default is utf-16.
    dry_run: Boolean flag specifying the run is for testing.
  """
  df = get_blobs_data_frame_with_date_range(
      blobs, date_filter, date_filter, encoding, dry_run)
  return df


def get_blobs_data_frame_with_date_range(
    blobs: list[Blob], start_date: datetime, end_date: datetime,
    encoding: str = 'utf-16', dry_run: bool = False, date_fields: list = []
) -> pd.DataFrame:
  """Reads report blobs into data frame filtered by date.

  Args:
    blobs: List of report blobs.
    start_date: Start date filter to rows later (including) the date.
    end_date: End date filter to rows earlier (including) the date.
    encoding: Encoding of play report files in Cloud Storage, default is utf-16.
    dry_run: Boolean flag specifying the run is for testing.
  """

  df = pd.DataFrame()

  for blob in blobs:
    with blob.open(mode='r', encoding=encoding) as f:
      report_csv_string_io = StringIO(f.read())
      if len(date_fields) > 1:
        dtypes = {'App Version Name': str,
                  'App Version Code': str,
                  'Developer Reply Text': str}
      else:
        dtypes = {}
      df_report = pd.read_csv(
          report_csv_string_io, parse_dates=date_fields,
          dtype=dtypes, keep_default_na=False)
      for date_field in date_fields:
        df_report[date_field] = pd.to_datetime(df_report[date_field])
      del report_csv_string_io
      if len(date_fields) > 1:
        start_date = pd.to_datetime(start_date, utc=True)
        end_date = pd.to_datetime(end_date, utc=True)
      date_mask = ((df_report[date_fields[0]].dt.floor('D') >= start_date) &
                   (df_report[date_fields[0]].dt.floor('D') <= end_date))
      df_report = df_report.loc[date_mask]
      # if dry_run:
      #   df = pd.concat([df, df_report.head(5)]).reset_index(drop=True)
      # else:
      df = pd.concat([df, df_report]).reset_index(drop=True)

  if not df.empty:
    columns_renamed = dict(
        (column, re.sub('[^0-9a-zA-Z]+', '_', column)) for column in df.columns)
    df.rename(columns_renamed, axis=1, inplace=True)

    if len(date_fields) > 1:
      cols_int = ['App_Version_Code' ,
                  'Developer_Reply_Millis_Since_Epoch',
                  'Review_Submit_Millis_Since_Epoch',
                  'Star_Rating']
      cols_str = ['Package_Name', 'App_Version_Name', 'Device',
                  'Reviewer_Language', 'Review_Title', 'Review_Text',
                  'Developer_Reply_Text', 'Review_Link']
      df[cols_int] = df[cols_int].apply(pd.to_numeric, errors='coerce')
      df[cols_str] = df[cols_str].replace('', np.nan)


  return df


def find_files_from_cloud_storage(
    bucket: str, prefix: str, dimensions: list[str], date_filter: datetime
) ->  Mapping[str, list[Blob]]:
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
  blobs_dimensions = find_files_from_cloud_storage_with_date_range(
      bucket, prefix, dimensions, date_filter, date_filter
  )
  return blobs_dimensions


def find_files_from_cloud_storage_with_date_range(
    bucket: str, prefix: str, dimensions: list[str],
    start_date: datetime, end_date: datetime
) ->  Mapping[str, list[Blob]]:
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
  def get_file_patterns_by_dimension(dimension, month_strings):
    patterns = []
    for month in month_strings:
      if dimension:
        patterns.append('_{}_{}.csv'.format(month, dimension))
      else:
        patterns.append('_{}.csv'.format(month))
    return patterns

  storage_client = storage.Client()
  blobs = storage_client.list_blobs(bucket, prefix=prefix)

  reports = []
  blobs_dimensions = {}
  blobs = list(blobs)
  month_strings = get_months_by_date_range(start_date, end_date)

  for dimension in dimensions:
    dimension_patterns = get_file_patterns_by_dimension(dimension, month_strings)
    _blobs = []
    for pattern in dimension_patterns:
      _blobs += list(filter(lambda b: re.search(pattern, b.name), blobs))
    blobs_dimensions[dimension] = _blobs
    # logger.log(
    #     'Found {} blobs for dimension {} and year month {}'.format(
    #         len(blobs_dimensions[dimension]), dimension, date_string))

  return blobs_dimensions


def get_months_by_date_range(
    start_date: datetime, end_date: datetime
) -> list[str]:
  def year_month(dt: datetime):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

  month_strings = []

  while year_month(start_date) <= year_month(end_date):
    month_strings.append(start_date.strftime('%Y%m'))
    start_date = start_date + relativedelta(months=1)
  return month_strings
