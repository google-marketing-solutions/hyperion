// Copyright 2023, Google Inc. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * @name Export Data to BigQuery
 *
 * @overview The Export Data to BigQuery script sets up a BigQuery
 *       dataset and tables, downloads a report from Google Ads and then
 *       loads the report to BigQuery.
 */

// Set backfill to true if wanting to backfill
var backfill = true

// Set backfill_start_date and backfill_end_date to the date range which you want to backfill. Required if backfill is set to true
var backfill_start_date = '<start date in this format: "2022-02-01">' 
var backfill_end_date = '<end date in this format: "2022-02-01">'

var today = new Date()
var yesterday = new Date(today)
yesterday.setDate(yesterday.getDate() - 1)
var timezone_MCC = '<timezone of the MCC account, e.g. "Asia/Karachi">'
var yesterdayFormatted = Utilities.formatDate(yesterday, timezone_MCC, 'YYYY-MM-dd')

var CONFIG = {
  BIGQUERY_PROJECT_ID: '<project id>',
  BIGQUERY_DATASET_ID: '<dataset id>',

  // Truncate existing data, otherwise will append.
  TRUNCATE_EXISTING_DATASET: false,
  TRUNCATE_EXISTING_TABLES: false,

  // Lists of reports and fields to retrieve from Google Ads.
  REPORTS: [
    {NAME: 'google_ads_report',
     CONDITIONS: 'WHERE IsTargetingLocation IN [true,false]',
     FIELDS: {'Paid_Installs' : 'INT64',
              'Cost' : 'INT64',
              'Country' : 'STRING',
              'App_ID': 'STRING',
              'Currency': 'STRING',
              'Date' : 'DATE'
             }
    }],
};

// Impose a limit on the size of BQ inserts: 10MB - 512Kb for overheads.
var MAX_INSERT_SIZE = 10 * 1024 * 1024 - 512 * 1024;

/**
 * Main method
 */
function main() {
  createDataset();
  for (var i = 0; i < CONFIG.REPORTS.length; i++) {
    var reportConfig = CONFIG.REPORTS[i];
    createTable(reportConfig);
		if (checkIfRowsExist(reportConfig)) {
			return
		}
  }

  var childAccounts = AdsManagerApp.accounts().get()
  while(childAccounts.hasNext()) {
    var childAccount = childAccounts.next()
    AdsManagerApp.select(childAccount);
    var jobIds = processReports();
    waitTillJobsComplete(jobIds);
  }
}

/**
 * Creates a new dataset.
 *
 * If a dataset with the same id already exists and the truncate flag
 * is set, will truncate the old dataset. If the truncate flag is not
 * set, then will not create a new dataset.
 */
function createDataset() {
   if (datasetExists()) {
    if (CONFIG.TRUNCATE_EXISTING_DATASET) {
      BigQuery.Datasets.remove(CONFIG.BIGQUERY_PROJECT_ID,
        CONFIG.BIGQUERY_DATASET_ID, {'deleteContents' : true});
      Logger.log('Truncated dataset.');
    } else {
      Logger.log('Dataset %s already exists.  Will not recreate.',
       CONFIG.BIGQUERY_DATASET_ID);
      return;
    }
  }

  // Create new dataset.
  var dataSet = BigQuery.newDataset();
  dataSet.friendlyName = CONFIG.BIGQUERY_DATASET_ID;
  dataSet.datasetReference = BigQuery.newDatasetReference();
  dataSet.datasetReference.projectId = CONFIG.BIGQUERY_PROJECT_ID;
  dataSet.datasetReference.datasetId = CONFIG.BIGQUERY_DATASET_ID;

  dataSet = BigQuery.Datasets.insert(dataSet, CONFIG.BIGQUERY_PROJECT_ID);
  Logger.log('Created dataset with id %s.', dataSet.id);
}

/**
 * Checks if dataset already exists in project.
 *
 * @return {boolean} Returns true if dataset already exists.
 */
function datasetExists() {
  // Get a list of all datasets in project.
  var datasets = BigQuery.Datasets.list(CONFIG.BIGQUERY_PROJECT_ID);
  var datasetExists = false;
  // Iterate through each dataset and check for an id match.
  if (datasets.datasets != null) {
    for (var i = 0; i < datasets.datasets.length; i++) {
      var dataset = datasets.datasets[i];
      if (dataset.datasetReference.datasetId == CONFIG.BIGQUERY_DATASET_ID) {
        datasetExists = true;
        break;
      }
    }
  }
  return datasetExists;
}

/**
 * Creates a new table.
 *
 * If a table with the same id already exists and the truncate flag
 * is set, will truncate the old table. If the truncate flag is not
 * set, then will not create a new table.
 *
 * @param {Object} reportConfig Report configuration including report name,
 *    conditions, and fields.
 */
function createTable(reportConfig) {
  if (tableExists(reportConfig.NAME)) {
    if (CONFIG.TRUNCATE_EXISTING_TABLES) {
      BigQuery.Tables.remove(CONFIG.BIGQUERY_PROJECT_ID,
          CONFIG.BIGQUERY_DATASET_ID, reportConfig.NAME);
      Logger.log('Truncated table %s.', reportConfig.NAME);
    } else {
      Logger.log('Table %s already exists.  Will not recreate.',
          reportConfig.NAME);
      return;
    }
  }

  // Create new table.
  var table = BigQuery.newTable();
  var schema = BigQuery.newTableSchema();
  var bigQueryFields = [];

  // Add each field to table schema.
  var fieldNames = Object.keys(reportConfig.FIELDS);
  for (var i = 0; i < fieldNames.length; i++) {
    var fieldName = fieldNames[i];
    var bigQueryFieldSchema = BigQuery.newTableFieldSchema();
    bigQueryFieldSchema.description = fieldName;
    bigQueryFieldSchema.name = fieldName;
    bigQueryFieldSchema.type = reportConfig.FIELDS[fieldName];

    bigQueryFields.push(bigQueryFieldSchema);
  }

  schema.fields = bigQueryFields;
  table.schema = schema;
  table.friendlyName = reportConfig.NAME;
  table.timePartitioning = {
    "type": 'DAY',
    "field": 'Date',
  }
  table.requirePartitionFilter = true
  table.clustering = {
    "fields": ['App_ID', 'Country'] 
  }
  table.tableReference = BigQuery.newTableReference();
  table.tableReference.datasetId = CONFIG.BIGQUERY_DATASET_ID;
  table.tableReference.projectId = CONFIG.BIGQUERY_PROJECT_ID;
  table.tableReference.tableId = reportConfig.NAME;

  table = BigQuery.Tables.insert(table, CONFIG.BIGQUERY_PROJECT_ID,
      CONFIG.BIGQUERY_DATASET_ID);

  Logger.log('Created table with id %s.', table.id);
}

/**
 * Checks if table already exists in dataset.
 *
 * @param {string} tableId The table id to check existence.
 *
 * @return {boolean}  Returns true if table already exists.
 */
function tableExists(tableId) {
  // Get a list of all tables in the dataset.
  var tables = BigQuery.Tables.list(CONFIG.BIGQUERY_PROJECT_ID,
      CONFIG.BIGQUERY_DATASET_ID);
  var tableExists = false;
  // Iterate through each table and check for an id match.
  if (tables.tables != null) {
    for (var i = 0; i < tables.tables.length; i++) {
      var table = tables.tables[i];
      if (table.tableReference.tableId == tableId) {
        tableExists = true;
        break;
      }
    }
  }
  return tableExists;
}

/**
 * Checks if data already exists for the given date
 */
function checkIfRowsExist(reportConfig) {
	let dateToCheck = yesterdayFormatted
	if (backfill) {
		dateToCheck = backfill_start_date
	}
	
	// Build the BigQuery SQL query.
	const request = {
		query: "SELECT * " +
					"FROM `" + CONFIG.BIGQUERY_PROJECT_ID + "." + CONFIG.BIGQUERY_DATASET_ID + "." + reportConfig.NAME + "` " +
					"WHERE Date = '" + dateToCheck + "' " +
					"LIMIT 10",
		useLegacySql: false
	};
	
	try {
		// Run the query and get the results.
		let queryResults = BigQuery.Jobs.query(request, CONFIG.BIGQUERY_PROJECT_ID);
		const jobId = queryResults.jobReference.jobId;

		// Check on status of the Query Job.
		let sleepTimeMs = 500;
		while (!queryResults.jobComplete) {
			Utilities.sleep(sleepTimeMs);
			sleepTimeMs *= 2;
			queryResults = BigQuery.Jobs.getQueryResults(CONFIG.BIGQUERY_PROJECT_ID, jobId);
		}
		
		// Get all the rows of results.
		let rows = queryResults.rows;
		
		if (rows) {
			Logger.log("There are already rows in the table with the specified date value: " + dateToCheck);
			return 1;
		}
		
	} catch (error) {
		// Handle any errors that might occur during the query execution.
		Logger.log("Error occurred: " + error.message);
	}
	
	// Return 0 if there are no rows with the specified date value.
	return 0;
}

/**
 * Process all configured reports
 *
 * Iterates through each report to: retrieve Google Ads data,
 * backup data to Drive (if configured), load data to BigQuery.
 *
 * @return {Array.<string>} jobIds The list of all job ids.
 */
function processReports() {
  var jobIds = [];

  // Iterate over each report type.
  for (var i = 0; i < CONFIG.REPORTS.length; i++) {
    var reportConfig = CONFIG.REPORTS[i];
    Logger.log('Running report %s', reportConfig.NAME);
    // Get data as an array of CSV chunks.
    var csvData = retrieveAdsReport(reportConfig);
    for (var j = 0; j < csvData.length; j++) {
      // Convert to Blob format.
      var blobData = Utilities.newBlob(csvData[j], 'application/octet-stream');
      // Load data
      var jobId = loadDataToBigquery(reportConfig, blobData, !j ? 1 : 0);
      jobIds.push(jobId);
    }
  }
  return jobIds;
}

/**
 * Retrieves Google Ads data as csv and formats any fields
 * to BigQuery expected format.
 *
 * @param {Object} reportConfig Report configuration including report name,
 *    conditions, and fields.
 *
 * @return {!Array.<string>} a chunked report in csv format.
 */
function retrieveAdsReport(reportConfig) {
  var fieldNames = Object.keys(reportConfig.FIELDS);
  if (backfill == true) {
    var query = "SELECT metrics.conversions, metrics.cost_micros, geographic_view.country_criterion_id, campaign.app_campaign_setting.app_id, customer.currency_code, segments.date, campaign.app_campaign_setting.app_store FROM geographic_view WHERE segments.date >= '" + backfill_start_date + "' AND segments.date <= '" + backfill_end_date + "' AND metrics.cost_micros > 0 and campaign.app_campaign_setting.app_id is not null and campaign.app_campaign_setting.app_store = 'GOOGLE_APP_STORE'";  }
  else {
    var query = "SELECT metrics.conversions, metrics.cost_micros, geographic_view.country_criterion_id, campaign.app_campaign_setting.app_id, customer.currency_code, segments.date, campaign.app_campaign_setting.app_store FROM geographic_view WHERE segments.date = '" + yesterdayFormatted + "' AND metrics.cost_micros > 0 and campaign.app_campaign_setting.app_id is not null and campaign.app_campaign_setting.app_store = 'GOOGLE_APP_STORE'";
  }
  Logger.log(query);
  
  var report = AdsApp.report(
    query);
  var rows = report.rows();
  var chunks = [];
  var chunkLen = 0;
  var csvRows = [];
  var totalRows = 0;
  // Header row
  var header = fieldNames.join(',');
  csvRows.push(header);
  chunkLen += Utilities.newBlob(header).getBytes().length + 1;

  // Iterate over each row.
  while (rows.hasNext()) {
    var row = rows.next();
    if (chunkLen > MAX_INSERT_SIZE) {
      chunks.push(csvRows.join('\n'));
      totalRows += csvRows.length;
      chunkLen = 0;
      csvRows = [];
    }
    var csvRow = [];
    var fieldNames2 = ['metrics.conversions', 'metrics.cost_micros', 'geographic_view.country_criterion_id', 'campaign.app_campaign_setting.app_id', 'customer.currency_code', 'segments.date', 'campaign.app_campaign_setting.app_store']
    for (var i = 0; i < fieldNames2.length; i++) {
      var fieldName = fieldNames2[i];
      if (fieldName == 'campaign.app_campaign_setting.app_store') {
        continue
      }
      // for debugging purposes
      if (row[fieldName] == undefined) {
        Logger.log(fieldName)
        Logger.log(row)
      }
      var fieldValue = row[fieldName].toString();
      var fieldType = reportConfig.FIELDS[fieldName];
      // Strip off % and perform any other formatting here.
      if (fieldType == 'FLOAT' || fieldType == 'INTEGER') {
        if (fieldValue.charAt(fieldValue.length - 1) == '%') {
          fieldValue = fieldValue.substring(0, fieldValue.length - 1);
        }
        fieldValue = fieldValue.replace(/,/g,'');
      }
      // Add double quotes to any string values.
      if (fieldType == 'STRING') {
        fieldValue = fieldValue.replace(/"/g, '""');
        fieldValue = '"' + fieldValue + '"';
      }
      csvRow.push(fieldValue);
    }
    var rowString = csvRow.join(',');
    csvRows.push(rowString);
    chunkLen += Utilities.newBlob(rowString).getBytes().length + 1;
  }
  if (csvRows) {
    totalRows += csvRows.length;
    chunks.push(csvRows.join('\n'));
  }
  Logger.log('Downloaded ' + reportConfig.NAME + ' with ' + totalRows +
      ' rows, in ' + chunks.length + ' chunks.');
  return chunks;
}

/**
 * Creates a BigQuery insertJob to load csv data.
 *
 * @param {Object} reportConfig Report configuration including report name,
 *    conditions, and fields.
 * @param {Blob} data Csv report data as an 'application/octet-stream' blob.
 * @param {number=} skipLeadingRows Optional number of rows to skip.
 *
 * @return {string} jobId The job id for upload.
 */
function loadDataToBigquery(reportConfig, data, skipLeadingRows) {
  // Create the data upload job.
  var job = {
    configuration: {
      load: {
        destinationTable: {
          projectId: CONFIG.BIGQUERY_PROJECT_ID,
          datasetId: CONFIG.BIGQUERY_DATASET_ID,
          tableId: reportConfig.NAME
        },
        skipLeadingRows: skipLeadingRows ? skipLeadingRows : 0,
        nullMarker: '--'
      }
    }
  };

  var insertJob = BigQuery.Jobs.insert(job, CONFIG.BIGQUERY_PROJECT_ID, data);
  Logger.log('Load job started for %s. Check on the status of it here: ' +
      'https://bigquery.cloud.google.com/jobs/%s', reportConfig.NAME,
       CONFIG.BIGQUERY_PROJECT_ID);
  return insertJob.jobReference.jobId;
}

/**
 * Polls until all jobs are 'DONE'.
 *
 * @param {Array.<string>} jobIds The list of all job ids.
 */
function waitTillJobsComplete(jobIds) {
  var complete = false;
  var remainingJobs = jobIds;
  while (!complete) {
    if (AdsApp.getExecutionInfo().getRemainingTime() < 5){
      Logger.log('Script is about to timeout, jobs ' + remainingJobs.join(',') +
        ' are still incomplete.');
    }
    remainingJobs = getIncompleteJobs(remainingJobs);
    if (remainingJobs.length == 0) {
      complete = true;
    }
    if (!complete) {
      Logger.log(remainingJobs.length + ' jobs still being processed.');
      // Wait 5 seconds before checking status again.
      Utilities.sleep(5000);
    }
  }
  Logger.log('All jobs processed.');
}

/**
 * Iterates through jobs and returns the ids for those jobs
 * that are not 'DONE'.
 *
 * @param {Array.<string>} jobIds The list of job ids.
 *
 * @return {Array.<string>} remainingJobIds The list of remaining job ids.
 */
function getIncompleteJobs(jobIds) {
  var remainingJobIds = [];
  for (var i = 0; i < jobIds.length; i++) {
    var jobId = jobIds[i];
    var getJob = BigQuery.Jobs.get(CONFIG.BIGQUERY_PROJECT_ID, jobId);
    if (getJob.status.state != 'DONE') {
      remainingJobIds.push(jobId);
    }
  }
  return remainingJobIds;
}