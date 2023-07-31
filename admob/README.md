# Steps for generating refresh token file (token.pickle)
1. Fill client_id, project_id, and client_secret in the client_secrets.json file first before running `./run.sh`
2. Run `chmod +x run.sh` in terminal
3. Now, run `./run.sh` in terminal as well
4. Click on the authorization URL printed in the console and proceed with the auth steps
5. Copy the redirected url and paste somewhere. Take the value of the param `code` inside the url and paste it into the terminal when it says `Enter the code here: ` and hit enter