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
