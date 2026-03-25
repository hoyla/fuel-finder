"""Fuel Finder API client with OAuth2 client credentials flow."""

import os
import time
import requests


BASE_URL = "https://www.fuel-finder.service.gov.uk"
TOKEN_PATH = "/api/v1/oauth/generate_access_token"
PRICES_PATH = "/api/v1/pfs/fuel-prices"
STATIONS_PATH = "/api/v1/pfs"


class FuelFinderClient:
    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id or os.environ["FUEL_API_ID"]
        self.client_secret = client_secret or os.environ["FUEL_API_SECRET"]
        self.session = requests.Session()
        self._token = None
        self._token_expires_at = 0

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

    def _get_json(self, path, params=None):
        self._ensure_token()
        resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

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
