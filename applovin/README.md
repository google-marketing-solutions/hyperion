# Steps to run the script locally for testing/debugging/experimental purposes
First, add the API key string to the .env file. Then run the following terminal ccommands in this directory:
- `$ source .venv/bin/activate`
- `$ pip install -r requirements.txt --require-hashes`
- `$ python3 main.py`

# To deploy the script to GCP
`$ sh deploy.sh --project cloud-project-id`