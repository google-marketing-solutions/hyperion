# Steps to run the script locally for testing/debugging/experimental purposes
- `$ source ./venv/bin/activate`
- `$ pip install -r requirements.txt --require-hashes`
- `$ export GOOGLE_APPLICATION_CREDENTIALS=path/to/serviceaccount/private/key`
- `$ python3 main.py`

# To deploy the script to GCP
`$ sh deploy.sh --project cloud-project-id`

# To backfill in bulk automatically (define start date as in the format below)
`$ sh backfill.sh YYYY-mm-dd`