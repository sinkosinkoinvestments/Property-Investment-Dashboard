import pandas as pd
import datetime
import numpy_financial as npf
from apify_client import ApifyClient
import os
import re

import requests  # used for Domain API calls

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
        resp = requests.post(DOMAIN_AUTH_URL, data=data)
        resp.raise_for_status()
        token = resp.json().get("access_token")
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

        # Domain may use a structured object or plain number
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
            # If numeric or string, assume m2
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
        resp = requests.post(DOMAIN_LISTINGS_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
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

def calculate_financials(price, weekly_rent, build_class, capex=0):
    try:
        price = float(price)
        weekly_rent = float(weekly_rent)
    except (TypeError, ValueError):
        return [None]*16 + ["Unknown", ""]
    if price == 0 or weekly_rent == 0:
        return [None]*16 + ["Unknown", ""]

    annual_rent = weekly_rent * 52
    op_expenses = (annual_rent * MGMT_FEE_PCT) + ANNUAL_RATES + ANNUAL_MAINT
    noi = annual_rent - op_expenses
    cap_rate    = (noi / price) * 100
    gross_yield = (annual_rent / price) * 100
    net_yield   = (noi / price) * 100

    actual_loan = MAX_LOAN_AMOUNT if price >= MAX_LOAN_AMOUNT else price * 0.80
    total_cash_invested = max((price - actual_loan) + STAMP_DUTY_FEES, STAMP_DUTY_FEES) + capex

    monthly_rate = INTEREST_RATE / 12
    months = LOAN_TERM_YEARS * 12
    monthly_repay = actual_loan * (monthly_rate * (1 + monthly_rate)**months) / ((1 + monthly_rate)**months - 1)
    annual_repay  = monthly_repay * 12

    dscr       = noi / annual_repay if annual_repay > 0 else 0
    break_even = ((op_expenses + annual_repay) / annual_rent) * 100 if annual_rent > 0 else 0
    net_annual_cf = noi - annual_repay
    net_weekly_cf = net_annual_cf / 52
    y1_interest   = actual_loan * INTEREST_RATE
    paper_pl      = annual_rent - op_expenses - y1_interest - DEPRECIATION_Y1

    if "New Build" in build_class:
        budget_rule      = "2026 Budget: losses deductible against other income"
        quarantined_loss = 0.0
        tax_benefit      = abs(paper_pl) * TAX_RATE if paper_pl < 0 else -(paper_pl * TAX_RATE)
    else:
        budget_rule      = "2026 Budget: established losses quarantined, no wage offset"
        quarantined_loss = abs(paper_pl) if paper_pl < 0 else 0.0
        tax_benefit      = 0.0

    post_tax_cf = net_annual_cf + tax_benefit
    coc_return  = (net_annual_cf / total_cash_invested) * 100 if total_cash_invested > 0 else 0
    y1_equity   = total_cash_invested + (price * CAPITAL_GROWTH_PCT)
    roe         = (net_annual_cf / y1_equity) * 100 if y1_equity > 0 else 0

    cash_flows = [-total_cash_invested] + [net_annual_cf] * 9
    fv = price * ((1 + CAPITAL_GROWTH_PCT) ** 10)
    pr = (LOAN_TERM_YEARS - 10) * 12
    remaining_loan = monthly_repay * ((1 - (1 + monthly_rate)**-pr) / monthly_rate)
    nominal_gain = max(fv - price, 0)
    cgt = nominal_gain * 0.25 * TAX_RATE if "New Build" in build_class else max(nominal_gain * 0.30 - quarantined_loss, 0)
    net_proceeds = fv - remaining_loan - (fv * SALE_COST_PCT) - cgt
    cash_flows.append(net_annual_cf + net_proceeds)
    try: irr = round(npf.irr(cash_flows) * 100, 2)
    except: irr = None

    status = "Positive" if net_weekly_cf > 0 else "Negative"
    return [round(noi,2), round(cap_rate,2), round(gross_yield,2), round(net_yield,2),
            round(monthly_repay,2), round(dscr,2), round(break_even,2),
            round(net_annual_cf,2), round(net_weekly_cf,2),
            round(quarantined_loss,2), round(tax_benefit,2), round(post_tax_cf,2),
            round(total_cash_invested,2), round(coc_return,2), irr, round(roe,2),
            status, budget_rule]

def fetch_with_apify(client, operation):
    all_items = []

    # Map operation to URL path
    op_path = {"buy": "buy", "rent": "rent", "sold": "sold"}.get(operation, "buy")

    for suburb_name, slug in SUBURBS.items():
        print(f"  Fetching {operation} for {suburb_name}...")
        url = f"https://www.realestate.com.au/{op_path}/property-house-acreage-in-{slug}/list-1?minimumLandSize={MIN_LAND_M2}&sort=list-date"
        try:
            run_input = {
                "startUrls": [url],
                "includeSurroundingSuburbs": False,
                "maxItems": 15,
                "flattenOutput": True
            }
            run   = client.actor("memo23/realestate-au-listings").call(run_input=run_input)
            items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
            print(f"    -> {len(items)} items")
            all_items.extend(items)
        except Exception as e:
            print(f"    -> ERROR: {e}")

    unique = {item.get("url", str(i)): item for i, item in enumerate(all_items)}
    return list(unique.values())

def main():
    os.makedirs("data", exist_ok=True)
    client   = ApifyClient(APIFY_API_TOKEN)
    today    = datetime.datetime.now().strftime("%Y-%m-%d")
    today_dt = datetime.datetime.now()

    print("=== RENT ===")
    rent_raw = fetch_with_apify(client, "rent")
    suburb_medians = {}
    for item in rent_raw:
        rp  = parse_rent_price(item.get("price") or item.get("rentPrice"))
        if rp:
            sub = item.get("suburb") or item.get("address","").split(",")[0].strip() or "Unknown"
            suburb_medians.setdefault(sub, []).append(rp)
    suburb_medians = {k: round(sum(v)/len(v),2) for k,v in suburb_medians.items()}
    pd.DataFrame([
        {"Date Pulled": today, "Suburb": k, "Median Acreage Rent ($)": v, "Interest Rate (%)": INTEREST_RATE*100}
        for k,v in suburb_medians.items()
    ]).to_csv("data/market_data_v5.csv", index=False)
    print(f"  Market data saved: {len(suburb_medians)} suburbs")

    print("=== BUY ===")
    buy_rows = []
    for item in fetch_with_apify(client, "buy"):
        price = parse_price(item.get("price") or item.get("priceText") or item.get("displayPrice") or "")
        suburb  = item.get("suburb") or item.get("address","").split(",")[0].strip() or "Unknown"
        rent    = suburb_medians.get(suburb, 900.0)
        raw_land = str(item.get("landArea") or item.get("landSize") or item.get("features", {}).get("landSize") or "0").lower()
        raw_land = raw_land.replace(',', '').strip()

        if "ha" in raw_land or "hectare" in raw_land:
            # Convert hectares to square meters
            num = float(''.join(c for c in raw_land if c.isdigit() or c == '.'))
            land_m2 = num * 10000
        elif "acre" in raw_land:
            # Convert acres to square meters
            num = float(''.join(c for c in raw_land if c.isdigit() or c == '.'))
            land_m2 = num * 4046.86
        else:
            # Assume square meters
            num_str = ''.join(c for c in raw_land if c.isdigit() or c == '.')
            land_m2 = float(num_str) if num_str else 0.0
        desc    = str(item.get("description") or "")
        title   = str(item.get("title") or item.get("headline") or "")
        build   = classify_build(desc, title)
        m       = calculate_financials(price, rent, build)
        date_listed = item.get("dateListed") or item.get("dateFirstListed")
        dom = None
        if date_listed:
            try:
                listed_dt = datetime.datetime.fromisoformat(str(date_listed).replace("Z","+00:00")).replace(tzinfo=None)
                dom = (today_dt - listed_dt).days
            except: pass
        buy_rows.append({
            "Date Pulled": today, "Address": item.get("address",""), "Suburb": suburb,
            "Property Type": item.get("propertyType",""),
            "Beds": item.get("bedrooms"), "Baths": item.get("bathrooms"), "Cars": item.get("carSpaces"),
            "Land Size (m2)": land_m2, "Asking Price ($)": price,
            "Price Per Acre ($)": round(price/(land_m2/4046.86)) if land_m2 and price else None,
            "Days on Market": dom,
            "Sale Method": "Auction" if item.get("isAuction") else "For Sale",
            "Agency": (item.get("agency") or {}).get("name","Unknown") if isinstance(item.get("agency"),dict) else str(item.get("agency","Unknown")),
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
            "Cashflow Status": m[16], "URL": item.get("url",""),
        })
    # Also fetch listings from Domain API and append to buy_rows
    token = get_domain_access_token()
    if token:
        for suburb_name, slug in SUBURBS.items():
            try:
                postcode = int(slug.split("-")[-1])
            except ValueError:
                postcode = None
            listings = search_domain_listings_for_suburb(token, suburb_name, postcode, min_land_m2=MIN_LAND_M2)
            for listing in listings:
                row = domain_listing_to_buy_row(listing, suburb_medians, today, today_dt)
                buy_rows.append(row)
    if buy_rows:
        pd.DataFrame(buy_rows).to_csv("data/buy_properties_v5.csv", index=False)
    else:
        print("Warning: No buy properties scraped. Skipping overwrite to prevent data loss.")
    print(f"  Buy properties saved: {len(buy_rows)} rows")

    print("=== SOLD ===")
    sold_rows = []
    for item in fetch_with_apify(client, "sold"):
        price = parse_price(item.get("price") or item.get("priceText") or item.get("displayPrice") or "")
        raw_land = str(item.get("landArea") or item.get("landSize") or item.get("features", {}).get("landSize") or "0").lower()
        raw_land = raw_land.replace(',', '').strip()

        if "ha" in raw_land or "hectare" in raw_land:
            # Convert hectares to square meters
            num = float(''.join(c for c in raw_land if c.isdigit() or c == '.'))
            land_m2 = num * 10000
        elif "acre" in raw_land:
            # Convert acres to square meters
            num = float(''.join(c for c in raw_land if c.isdigit() or c == '.'))
            land_m2 = num * 4046.86
        suburb  = item.get("suburb") or item.get("address","").split(",")[0].strip() or "Unknown"
        sold_rows.append({
            "Date Pulled": today, "Address": item.get("address",""), "Suburb": suburb,
            "Property Type": item.get("propertyType",""),
            "Beds": item.get("bedrooms"), "Baths": item.get("bathrooms"), "Cars": item.get("carSpaces"),
            "Land Size (m2)": land_m2, "Sale Price ($)": price,
            "Price Per Acre ($)": round(price/(land_m2/4046.86)) if land_m2 and price else None,
            "Sale Date": item.get("soldDate",""),
            "Agency": (item.get("agency") or {}).get("name","Unknown") if isinstance(item.get("agency"),dict) else str(item.get("agency","Unknown")),
            "URL": item.get("url",""),
        })
    if sold_rows:
        pd.DataFrame(sold_rows).to_csv("data/sold_properties_v5.csv", index=False)
    else:
        print("Warning: No sold properties scraped. Skipping overwrite to prevent data loss.")
    print(f"  Sold properties saved: {len(sold_rows)} rows")
    print("Pipeline complete.")

if __name__ == "__main__":
    main()
