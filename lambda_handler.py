"""AWS Lambda handler for Fuel Finder scraper.

Triggered by EventBridge schedule (e.g. every 30 minutes).
Credentials should be stored in AWS Secrets Manager or Lambda env vars.
DATABASE_URL should point to the RDS PostgreSQL instance.
"""

import json
import logging

from scrape import run_scrape

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    log.info("Lambda invoked with event: %s", json.dumps(event, default=str))
    try:
        mode = event.get("mode", "auto") if isinstance(event, dict) else "auto"
        result = run_scrape(mode=mode)
        log.info("Scrape completed: %s", result)
        return {
            "statusCode": 200,
            "body": json.dumps(result),
        }
    except Exception as e:
        log.exception("Scrape failed")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
