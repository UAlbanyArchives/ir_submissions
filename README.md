# ir_submissions
A polling service for pulling down IR submissions from Jotform.

Every 5 minutes it checks the Jotform API for new submissions, writes each to the $STORAGE_DIR path, including uploaded files and all data stored in a `metadata.yml`.

## Use
1. Set the environment variables in an `.env` file. `.env.example` is available as an example.
2. `docker compose up -d --build`

Its set up to sent emails but this is currently turned off. Email is handed to the host's local MTA (postfix/sendmail) at `localhost:25`. The container uses `network_mode: host` (Linux only), so the MTA only needs to accept local connections, which is the default.
