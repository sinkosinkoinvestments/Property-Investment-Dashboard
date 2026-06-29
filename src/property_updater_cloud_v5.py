import pandas as pd
import datetime
import numpy_financial as npf
from apify_client import ApifyClient
import os
import re
import logging
from pathlib import Path

from urllib import request, parse

DOMAIN_CLIENT_ID = os.getenv("DOMAIN_CLIENT_ID", "")
DOMAIN_CLIENT_SECRET = os.getenv("DOMAIN_CLIENT_SECRET", "")
DOMAIN_SCOPE = os.getenv("DOMAIN_SCOPE", "api_listings_read")
DOMAIN_AUTH_URL = os.getenv("DOMAIN_AUTH_URL", "https://auth.domain.com.au/v1/connect/token")
DOMAIN_LISTINGS_URL = "https://api.domain.com.au/v1/listings/residential/_search"

LOG_DIR = Path(__file__).resolve().parent.parent / "output"
LOG_DIR.mkdir(exist_ok=True)

DOMAIN_LOG_FILE = LOG_DIR / "domain_debug.log"
logging.basicConfig(
    filename=str(DOMAIN_LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)


def get_domain_access_token():
    """Client Credentials grant to get a bearer token from Domain API."""
    if not DOMAIN_CLIENT_ID or not DOMAIN_CLIENT_SECRET:
        print("Domain API credentials missing; skipping Domain fetch.")
        logging.debug("Domain auth response: %s", payload)
        return None

    data = {
        "client_id": DOMAIN_CLIENT_ID,
        "client_secret": DOMAIN_CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": DOMAIN_SCOPE,
    }

    try:
        req = request.Request(
            DOMAIN_AUTH_URL,
            data=parse.urlencode(data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        token = payload.get("access_token")
        if not token:
            print("Domain auth: no access_token in response.")
        return token
    except Exception as e:
        print(f"Domain auth error: {e}")
        return None


def extract_land_size_from_domain(listing):
    """Attempt to derive land size in m2 from Domain listing JSON."""
    try:
        land = listing.get("landArea") or listing.get("propertyDetails", {}).get("landArea")
        if not land:
            return None

        if isinstance(land, dict):
            value = land.get("value")
            unit = str(land.get("unit", "")).lower()
            if value is None:
                return None
            if unit in ("square_meter", "sqm", "m2"):
                return float(value)
            if unit in ("hectare", "ha"):
                return float(value) * 10000
            if unit in ("acre", "acres"):
                return float(value) * 4046.86
        else:
            return float(str(land).replace(",", ""))
    except Exception:
        return None
    return None


def search_domain_listings_for_suburb(token, suburb_name, postcode=None, min_land_m2=2000):
    """Call Domain's residential listings search API for a single suburb and return filtered listings."""
    if not token:
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    logging.debug("Domain listings response for %s %s: %s", suburb_name, postcode, data)
    logging.debug(
    "Domain listings count for %s %s: %s",
    suburb_name,
    postcode,
    len(listings) if listings else 0
    )
    
    loc = {"state": "QLD", "suburb": suburb_name, "includeSurroundingSuburbs": False}
    if postcode:
        loc["postCode"] = postcode

    payload = {
        "listingType": "Sale",
        "propertyTypes": ["House", "Acreage"],
        "locations": [loc],
        "pageSize": 100,
        "pageNumber": 1,
    }

    try:
        req = request.Request(
            DOMAIN_LISTINGS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        listings = data.get("results") if isinstance(data, dict) else data
        if not listings:
            print(f"Domain: no listings for {suburb_name} {postcode}.")
            return []

        filtered = []
        for listing in listings:
            land_m2 = extract_land_size_from_domain(listing)
            if land_m2 is None or land_m2 >= min_land_m2:
                filtered.append(listing)
        print(f"Domain: {len(filtered)} filtered listings for {suburb_name} {postcode}.")
        return filtered

    except Exception as e:
        print(f"Domain listings error for {suburb_name} {postcode}: {e}")
        return []
