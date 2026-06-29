import pandas as pd
import datetime
import numpy_financial as npf
from apify_client import ApifyClient
import os
import re

from urllib import request, parse

DOMAIN_CLIENT_ID = os.getenv("DOMAIN_CLIENT_ID", "")
DOMAIN_CLIENT_SECRET = os.getenv("DOMAIN_CLIENT_SECRET", "")
DOMAIN_SCOPE = os.getenv("DOMAIN_SCOPE", "api_listings_read")
DOMAIN_AUTH_URL = os.getenv("DOMAIN_AUTH_URL", "https://auth.domain.com.au/v1/connect/token")
DOMAIN_LISTINGS_URL = "https://api.domain.com.au/v1/listings/residential/_search"


def get_domain_access_token():
    """Client Credentials grant to get a bearer token from Domain API."""
    if not DOMAIN_CLIENT_ID or not DOMAIN_CLIENT_SECRET:
        print("Domain API credentials missing; skipping Domain fetch.")
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


def domain_listing_to_buy_row(listing, suburb_medians, today, today_dt):
    """Convert a Domain listing into the buy_rows dict expected by the pipeline."""
    address_info = listing.get("address", {})
    addr = address_info.get("display") or address_info.get("streetAddress")
    suburb = address_info.get("suburb")
    property_type = listing.get("propertyType") or "House"

    details = listing.get("propertyDetails", {})
    beds = details.get("bedrooms")
    baths = details.get("bathrooms")
    cars = details.get("carspaces")

    price_info = listing.get("priceDetails", {})
    asking_price = price_info.get("displayPrice") or price_info.get("price")
    price = parse_price(asking_price or "")

    land_m2 = extract_land_size_from_domain(listing)

    rent = suburb_medians.get(suburb, 900.0)

    desc = listing.get("description") or ""
    title = listing.get("headline") or listing.get("summary") or ""
    build = classify_build(desc, title)

    m = calculate_financials(price, rent, build)

    date_listed = listing.get("dateListed") or listing.get("listingDate")
    dom = None
    if date_listed:
        try:
            listed_dt = datetime.datetime.fromisoformat(str(date_listed).replace("Z", "+00:00")).replace(tzinfo=None)
            dom = (today_dt - listed_dt).days
        except Exception:
            pass

    agency = listing.get("agent", {}) or listing.get("agency", {})
    agency_name = None
    if isinstance(agency, dict):
        agency_name = agency.get("name") or agency.get("agencyName")
    else:
        agency_name = str(agency) if agency else "Unknown"

    url = listing.get("listingUrl") or listing.get("url")

    return {
        "Date Pulled": today,
        "Address": addr or "",
        "Suburb": suburb or "",
        "Property Type": property_type,
        "Beds": beds, "Baths": baths, "Cars": cars,
        "Land Size (m2)": land_m2, "Asking Price ($)": price,
        "Price Per Acre ($)": round(price/(land_m2/4046.86)) if land_m2 and price else None,
        "Days on Market": dom,
        "Sale Method": "Auction" if listing.get("isAuction") else "For Sale",
        "Agency": agency_name or "Unknown",
        "Dual Living / Granny Flat": check_keywords(desc, DUAL_KEYWORDS),
        "Subdivision Potential": check_keywords(desc, SUBDIV_KEYWORDS),
        "Usable Land": check_keywords(desc, USABLE_KEYWORDS),
        "Build Classification": build, "Budget Rule Applied": m[17],
        "Dynamic Rent Est ($)": rent,
        "NOI ($)": m[0], "Cap Rate (%)": m[1], "Gross Yield (%)": m[2], "Net Yield (%)": m[3],
        "Monthly Repayment ($)": m[4], "DSCR": m[5], "Break-Even Ratio (%)": m[6],
        "Net Annual Cashflow ($)": m[7], "Net Weekly Cashflow ($)": m[8],
        "Quarantined Loss Year 1 ($)": m[9], "Tax Benefit Year 1 ($)": m[10],
        "Post-Tax Cash Flow ($) *Est": m[11], "Est Renovation / Capex ($)": 0,
        "Total Cash Invested ($)": m[12], "Cash-on-Cash Return (%)": m[13],
        "Est 10-Yr IRR (%)": m[14], "Est Year 1 ROE (%)": m[15],
        "Cashflow Status": m[16], "URL": url or "",
    }

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")
INTEREST_RATE = float(os.getenv("CURRENT_INTEREST_RATE", "0.065"))
ASSUME_NEW_BUILD_DEFAULT = os.getenv("ASSUME_NEW_BUILD_DEFAULT", "No").lower() == "yes"

MAX_LOAN_AMOUNT = 1600000
MIN_LAND_M2 = 8000    
LOAN_TERM_YEARS = 30
MGMT_FEE_PCT = 0.08
ANNUAL_RATES = 4500
ANNUAL_MAINT = 2000
STAMP_DUTY_FEES = 60000
CAPITAL_GROWTH_PCT = 0.05
TAX_RATE = 0.37
DEPRECIATION_Y1 = 10000
SALE_COST_PCT = 0.02

import json
import os

# Load suburbs from external JSON file
possible_paths = [
    'src/suburbs.json',
    'suburbs.json',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'suburbs.json')
]

SUBURBS = {}
for path in possible_paths:
    if os.path.exists(path):
        print(f"Found suburbs list at: {path}")
        try:
            with open(path, 'r', encoding='utf-8') as f:
                SUBURBS = json.load(f)
            break
        except Exception as e:
            print(f"Error reading JSON from {path}: {e}")

if not SUBURBS:
    print("Warning: Could not load suburbs.json. Falling back to default list.")
    SUBURBS = {
        "Cooroy": "cooroy-qld-4563",
        "Black Mountain": "black-mountain-qld-4563",
        "Tinbeerwah": "tinbeerwah-qld-4563",
        "Yandina": "yandina-qld-4561",
        "Mapleton": "mapleton-qld-4560"
    }

NEW_BUILD_KEYWORDS = ["new build","brand new","newly built","under construction","house and land","off the plan"]
DUAL_KEYWORDS      = ["dual living","granny flat","dual occupancy","second dwelling"]
SUBDIV_KEYWORDS    = ["subdividable","stca","subdivision","development"]
USABLE_KEYWORDS    = ["usable","clear","flat","fully fenced","cleared"]

def check_keywords(text, kw_list):
    if not text: return False
    return any(k in text.lower() for k in kw_list)

def classify_build(desc, title=""):
    text = f"{title} {desc}".lower()
    if check_keywords(text, NEW_BUILD_KEYWORDS): return "New Build / Likely Eligible"
    return "Established / Assume Quarantined" if not ASSUME_NEW_BUILD_DEFAULT else "New Build / Assumed"

def parse_price(val):
    if isinstance(val, (int, float)):
        return float(val)
    if not isinstance(val, str):
        return 0.0
    cleaned = val.replace('$', '').replace(',', '').lower()
    nums = re.findall(r'\d+(?:\.\d+)?', cleaned)
    if not nums:
        return 0.0
    prices = [float(n) for n in nums if float(n) > 100000]
    if not prices:
        return 0.0
    return max(prices)

def parse_rent_price(val):
    if isinstance(val, (int, float)): return float(val)
    if not isinstance(val, str): return None
    nums = re.findall(r"\d+", val.replace(",", ""))
    if nums:
        n = int(nums[0])
        return float(n) if 200 <= n <= 3000 else None
    return None

def calculate_financials(price, weekly_rent, build_class):
    # keep the rest of your original script here
    ...
