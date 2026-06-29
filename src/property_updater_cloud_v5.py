import pandas as pd
import datetime
import numpy_financial as npf
from apify_client import ApifyClient
import os
import re

import logging
import json
from pathlib import Path
from urllib import request, parse, error

DOMAIN_CLIENT_ID = os.getenv("DOMAIN_CLIENT_ID", "")
DOMAIN_CLIENT_SECRET = os.getenv("DOMAIN_CLIENT_SECRET", "")
DOMAIN_SCOPE = os.getenv("DOMAIN_SCOPE", "api_listings_read")
DOMAIN_AUTH_URL = os.getenv("DOMAIN_AUTH_URL", "https://auth.domain.com.au/v1/connect/token")
DOMAIN_LISTINGS_URL = "https://api.domain.com.au/v1/listings/residential/_search"

LOG_DIR = Path(".github") / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DOMAIN_LOG_FILE = LOG_DIR / "domain_debug.log"
logging.basicConfig(
    filename=str(DOMAIN_LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)

logging.debug("property_updater_cloud_v5 started")
print("property_updater_cloud_v5 started")
logging.debug("Domain env present: client_id=%s secret=%s", bool(DOMAIN_CLIENT_ID), bool(DOMAIN_CLIENT_SECRET))

def get_domain_access_token():
    if not DOMAIN_CLIENT_ID or not DOMAIN_CLIENT_SECRET:
        logging.debug("Domain auth skipped: missing credentials")
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
        logging.debug("Domain auth response: %s", payload)
        return payload.get("access_token")
    except Exception as e:
        if hasattr(e, 'read'):
            print(f"Domain Auth HTTP Error: {e.read().decode('utf-8')}")
        logging.exception("Domain auth error")
        return None

def extract_land_size_from_domain(listing):
    try:
        land = listing.get("landArea") or listing.get("propertyDetails", {}).get("landArea")
        if not land: return None
        if isinstance(land, dict):
            val = land.get("value")
            unit = str(land.get("unit", "")).lower()
            if val is None: return None
            if unit in ("square_meter", "sqm", "m2"): return float(val)
            if unit in ("hectare", "ha"): return float(val) * 10000
            if unit in ("acre", "acres"): return float(val) * 4046.86
        else:
            return float(str(land).replace(",", ""))
    except:
        return None
    return None

def search_domain_listings_for_suburb(token, suburb_name, postcode=None, min_land_m2=2000):
    if not token: return []
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    loc = {"state": "QLD", "suburb": suburb_name, "includeSurroundingSuburbs": False}
    if postcode: loc["postCode"] = postcode
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
        logging.debug("Domain listings response for %s %s: %s", suburb_name, postcode, data)
        listings = data.get("results") if isinstance(data, dict) else data
        if not listings: return []
        filtered = []
        for listing in listings:
            lm2 = extract_land_size_from_domain(listing)
            if lm2 is None or lm2 >= min_land_m2:
                filtered.append(listing)
        return filtered
    except Exception as e:
        if hasattr(e, 'read'):
            try:
                err_body = e.read().decode('utf-8')
                print(f"HTTP Error body for {suburb_name}: {err_body}")
            except: pass
        logging.exception("Domain listings error for %s %s", suburb_name, postcode)
        print(f"Domain listings error for {suburb_name} {postcode}: {e}")
        return []

def domain_listing_to_buy_row(listing, suburb_medians, today_str, today_dt):
    address_info = listing.get("address", {})
    addr = address_info.get("display") or address_info.get("streetAddress")
    suburb = address_info.get("suburb") or "Unknown"
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
        except: pass

    agency = listing.get("agent", {}) or listing.get("agency", {})
    agency_name = agency.get("name") or agency.get("agencyName") if isinstance(agency, dict) else str(agency or "Unknown")
    url = listing.get("listingUrl") or listing.get("url")

    return {
        "Date Pulled": today_str, "Address": addr or "", "Suburb": suburb,
        "Property Type": property_type, "Beds": beds, "Baths": baths, "Cars": cars,
        "Land Size (m2)": land_m2, "Asking Price ($)": price,
        "Price Per Acre ($)": round(price/(land_m2/4046.86)) if land_m2 and price else None,
        "Days on Market": dom, "Sale Method": "Auction" if listing.get("isAuction") else "For Sale",
        "Agency": agency_name,
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
    if not price or price <= 0: return [0]*18
    annual_rent = weekly_rent * 52
    noi = annual_rent * (1 - MGMT_FEE_PCT) - ANNUAL_RATES - ANNUAL_MAINT
    cap_rate = noi / price
    gross_yield = annual_rent / price
    
    total_cost = price + STAMP_DUTY_FEES
    loan_amount = min(price, MAX_LOAN_AMOUNT)
    equity = total_cost - loan_amount
    net_yield = noi / total_cost

    monthly_rate = INTEREST_RATE / 12
    num_payments = LOAN_TERM_YEARS * 12
    monthly_repayment = float(npf.pmt(monthly_rate, num_payments, loan_amount)) * -1 if loan_amount > 0 else 0
    annual_repayment = monthly_repayment * 12

    dscr = noi / annual_repayment if annual_repayment > 0 else 0
    ber = (annual_repayment + ANNUAL_RATES + ANNUAL_MAINT + (annual_rent * MGMT_FEE_PCT)) / annual_rent if annual_rent > 0 else 0

    net_annual_cashflow = noi - annual_repayment
    net_weekly_cashflow = net_annual_cashflow / 52

    rule_applied = ""
    is_new = "New Build" in build_class
    if is_new:
        rule_applied = "Budget 2026: Negative Gearing Allowed (New Build)"
        depreciation = DEPRECIATION_Y1
        taxable_income = net_annual_cashflow - depreciation
        tax_benefit = max(0, -taxable_income * TAX_RATE)
        quarantined_loss = 0
    else:
        rule_applied = "Budget 2026: Negative Gearing Quarantined (Established)"
        tax_benefit = 0
        quarantined_loss = abs(min(0, net_annual_cashflow))

    post_tax_cashflow = net_annual_cashflow + tax_benefit
    cash_on_cash = post_tax_cashflow / equity if equity > 0 else 0
    roe = post_tax_cashflow / equity if equity > 0 else 0

    cashflows = [-equity] + [post_tax_cashflow]*9
    future_value = price * ((1 + CAPITAL_GROWTH_PCT)**10)
    loan_balance_10 = float(npf.fv(monthly_rate, 10*12, monthly_repayment, loan_amount)) * -1
    net_proceeds = future_value * (1 - SALE_COST_PCT) - loan_balance_10
    cashflows.append(post_tax_cashflow + net_proceeds)
    
    try: irr = float(npf.irr(cashflows))
    except: irr = 0

    if net_weekly_cashflow > 0: status = "Positive"
    elif net_weekly_cashflow > -100: status = "Neutral/Slight Negative"
    else: status = "Negative"

    return [
        round(noi), round(cap_rate*100,2), round(gross_yield*100,2), round(net_yield*100,2),
        round(monthly_repayment), round(dscr,2), round(ber*100,2), round(net_annual_cashflow),
        round(net_weekly_cashflow), round(quarantined_loss), round(tax_benefit),
        round(post_tax_cashflow), round(equity), round(cash_on_cash*100,2),
        round(irr*100,2), round(roe*100,2), status, rule_applied
    ]

def fetch_with_apify(client, mode):
    urls = []
    if mode == "buy":
        for val in SUBURBS.values(): urls.append(f"https://www.domain.com.au/sale/?suburb={val}&ptype=house,acreage&excludeunderoffer=1")
    elif mode == "rent":
        for val in SUBURBS.values(): urls.append(f"https://www.domain.com.au/rent/?suburb={val}&ptype=house,acreage")
    elif mode == "sold":
        for val in SUBURBS.values(): urls.append(f"https://www.domain.com.au/sold-listings/?suburb={val}&ptype=house,acreage")
    
    try:
        # We changed the actor ID to sahyog-inv/apifydomain-1
        run_input = {"urls": [{"url": u} for u in urls]} 
        run = client.actor("sahyog-inv/apifydomain-1").call(run_input=run_input)
        return client.dataset(run["defaultDatasetId"]).iterate_items()
    except Exception as e:
        print(f"Apify fetch failed for {mode}: {e}")
        return []

def main():
    if not APIFY_API_TOKEN:
        print("Error: APIFY_API_TOKEN is missing. Pipeline cannot run.")
        return

    client = ApifyClient(APIFY_API_TOKEN)
    today = datetime.date.today().strftime("%Y-%m-%d")
    today_dt = datetime.datetime.now()

    os.makedirs("data", exist_ok=True)
    
    print("=== RENT (MEDIANS) ===")
    suburb_medians = {}
    rent_data = []
    for item in fetch_with_apify(client, "rent"):
        rp = parse_rent_price(item.get("price") or item.get("priceText") or "")
        sub = item.get("suburb") or item.get("address", "").split(",")[0].strip()
        if rp and sub: rent_data.append({"Suburb": sub, "Rent": rp})
    
    if rent_data:
        df_rent = pd.DataFrame(rent_data)
        suburb_medians = df_rent.groupby("Suburb")["Rent"].median().to_dict()
    print(f"  Captured rent baselines for {len(suburb_medians)} suburbs.")

    print("=== BUY ===")
    buy_rows = []
    
    logging.debug("Requesting Domain access token...")
    domain_token = get_domain_access_token()
    if domain_token:
        print("Domain token acquired successfully.")
    else:
        print("Failed to acquire Domain token or skipped.")

    # Domain listings first
    if domain_token:
        for suburb_name, postcode_slug in SUBURBS.items():
            postcode = postcode_slug.split("-")[-1] if "-" in postcode_slug else None
            print(f"Domain: searching {suburb_name} {postcode}...")
            listings = search_domain_listings_for_suburb(domain_token, suburb_name, postcode, MIN_LAND_M2)
            if listings:
                for listing in listings:
                    row = domain_listing_to_buy_row(listing, suburb_medians, today, today_dt)
                    buy_rows.append(row)

    print("Fetching Apify properties...")
    for item in fetch_with_apify(client, "buy"):
        price = parse_price(item.get("price") or item.get("priceText") or item.get("displayPrice") or "")
        
        raw_land = str(item.get("landArea") or item.get("landSize") or item.get("features", {}).get("landSize") or "0").lower()
        raw_land = raw_land.replace(',', '').strip()
        land_m2 = None

        if "ha" in raw_land or "hectare" in raw_land:
            num = float(''.join(c for c in raw_land if c.isdigit() or c == '.'))
            land_m2 = num * 10000
        elif "acre" in raw_land:
            num = float(''.join(c for c in raw_land if c.isdigit() or c == '.'))
            land_m2 = num * 4046.86
        else:
            nums = re.findall(r'\d+(?:\.\d+)?', raw_land)
            if nums:
                land_m2 = float(nums[0])
        
        if land_m2 is None or land_m2 < MIN_LAND_M2:
            continue

        suburb = item.get("suburb") or item.get("address", "").split(",")[0].strip() or "Unknown"
        rent = suburb_medians.get(suburb, 900.0)
        
        desc = item.get("description", "")
        title = item.get("headline", "")
        build = classify_build(desc, title)

        m = calculate_financials(price, rent, build)
        
        date_listed = item.get("dateListed") or item.get("listingDate")
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
    pd.DataFrame(buy_rows).to_csv("data/buy_properties_v5.csv", index=False)
    print(f"  Buy properties saved: {len(buy_rows)} rows")

    print("=== SOLD ===")
    sold_rows = []
    for item in fetch_with_apify(client, "sold"):
        price = parse_price(item.get("price") or item.get("priceText") or item.get("displayPrice") or "")
        raw_land = str(item.get("landArea") or item.get("landSize") or item.get("features", {}).get("landSize") or "0").lower()
        raw_land = raw_land.replace(',', '').strip()

        if "ha" in raw_land or "hectare" in raw_land:
            num = float(''.join(c for c in raw_land if c.isdigit() or c == '.'))
            land_m2 = num * 10000
        elif "acre" in raw_land:
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
    pd.DataFrame(sold_rows).to_csv("data/sold_properties_v5.csv", index=False)
    print(f"  Sold properties saved: {len(sold_rows)} rows")
    print("Pipeline complete.")

if __name__ == "__main__":
    main()
