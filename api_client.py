"""Fuel Finder API client with OAuth2 client credentials flow."""

import logging
import os
import time
from collections import deque

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.fuel-finder.service.gov.uk"
TOKEN_PATH = "/api/v1/oauth/generate_access_token"
PRICES_PATH = "/api/v1/pfs/fuel-prices"
STATIONS_PATH = "/api/v1/pfs"

# GOV.UK API rate limit: 30 requests per minute
MAX_REQUESTS_PER_MINUTE = 30
RATELIMIT_WINDOW = 60  # seconds

# Retry config for 429 / transient errors
MAX_RETRIES = 5
BACKOFF_BASE = 2  # seconds
RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class FuelFinderClient:
    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id or os.environ["FUEL_API_ID"]
        self.client_secret = client_secret or os.environ["FUEL_API_SECRET"]
        self.session = requests.Session()
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self._token = None
        self._token_expires_at = 0
        self._request_timestamps = deque()

    def _ensure_token(self):
        if self._token and time.time() < self._token_expires_at - 60:
            return
        resp = self.session.post(
            f"{BASE_URL}{TOKEN_PATH}",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["data"]["access_token"]
        self._token_expires_at = time.time() + body["data"]["expires_in"]
        self.session.headers["Authorization"] = f"Bearer {self._token}"

    def _wait_for_rate_limit(self):
        """Block until we have capacity under the 30 RPM limit."""
        now = time.monotonic()
        # Discard timestamps older than the rate-limit window
        while self._request_timestamps and self._request_timestamps[0] <= now - RATELIMIT_WINDOW:
            self._request_timestamps.popleft()
        if len(self._request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
            sleep_until = self._request_timestamps[0] + RATELIMIT_WINDOW
            delay = sleep_until - now
            if delay > 0:
                log.info("Rate limit: sleeping %.1fs to stay under %d RPM", delay, MAX_REQUESTS_PER_MINUTE)
                time.sleep(delay)
        self._request_timestamps.append(time.monotonic())

    def _get_json(self, path, params=None):
        self._ensure_token()
        self._wait_for_rate_limit()
        for attempt in range(MAX_RETRIES + 1):
            resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=60)
            if resp.status_code not in RETRIABLE_STATUS_CODES:
                resp.raise_for_status()
                return resp.json()
            if attempt == MAX_RETRIES:
                resp.raise_for_status()
            delay = BACKOFF_BASE * (2 ** attempt)
            # Prefer Retry-After header if the server provides one
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            log.warning(
                "Request %s returned %d, retrying in %.1fs (attempt %d/%d)",
                path, resp.status_code, delay, attempt + 1, MAX_RETRIES,
            )
            time.sleep(delay)

    def get_fuel_prices(self, batch_number, since_timestamp=None):
        params = {"batch-number": batch_number}
        if since_timestamp:
            params["effective-start-timestamp"] = since_timestamp
        return self._get_json(PRICES_PATH, params)

    def get_stations(self, batch_number, since_timestamp=None):
        params = {"batch-number": batch_number}
        if since_timestamp:
            params["effective-start-timestamp"] = since_timestamp
        return self._get_json(STATIONS_PATH, params)

    def get_all_fuel_prices(self, since_timestamp=None):
        """Fetch all batches of fuel prices, returning a flat list."""
        all_records = []
        batch = 1
        while True:
            try:
                data = self.get_fuel_prices(batch, since_timestamp)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    break
                raise
            if not isinstance(data, list) or len(data) == 0:
                break
            all_records.extend(data)
            batch += 1
        return all_records, batch - 1

    def get_all_stations(self, since_timestamp=None):
        """Fetch all batches of station info, returning a flat list."""
        all_records = []
        batch = 1
        while True:
            try:
                data = self.get_stations(batch, since_timestamp)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    break
                raise
            if not isinstance(data, list) or len(data) == 0:
                break
            all_records.extend(data)
            batch += 1
        return all_records, batch - 1
