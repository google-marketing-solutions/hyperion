#!/bin/bash

# Define start and end dates
start_date="2023-01-01"
end_date="2024-01-24"

current_date=$start_date

while [ "$current_date" != "$end_date" ]; do
    # Calculate the date two months ahead
    next_date=$(date -j -v+2m -f "%Y-%m-%d" "$current_date" +%Y-%m-%d)

    # Ensure we do not pass the end date
    if [[ $(date -j -f "%Y-%m-%d" "$next_date" +%s) -gt $(date -j -f "%Y-%m-%d" "$end_date" +%s) ]]; then
        next_date=$end_date
    fi

    # Run the gcloud command
    gcloud pubsub topics publish analytics_data --message="send" --attribute=start_date=$current_date,end_date=$next_date

    # Update current_date to the next_date
    current_date=$next_date
done
