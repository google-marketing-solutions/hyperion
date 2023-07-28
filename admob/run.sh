#!/bin/bash

# create virtual environment
python3 -m venv .venv

# activate the virtual environment
source .venv/bin/activate

# install dependencies
pip3 install -r requirements.txt

# run main script
python3 main.py --generate-token-only=true

# deactivate the virtual environment
deactivate
