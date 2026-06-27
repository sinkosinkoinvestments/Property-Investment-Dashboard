import pandas as pd
import datetime
import numpy_financial as npf
from apify_client import ApifyClient
import os
import re

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "YOUR_TOKEN")
SUBURBS = ["Cooroy QLD 4563", "Black Mountain QLD 4563", "Tinbeerwah QLD 4563", "Yandina QLD 4561", "Mapleton QLD 4560"]
MAX_PRICE = 1600000
MIN_LAND_M2 = 4000
MAX_LOAN_AMOUNT = 1600000
INTEREST_RATE = float(os.getenv("CURRENT_INTEREST_RATE", "0.065"))
LOAN_TERM_YEARS = 30
MGMT_FEE_PCT = 0.08
ANNUAL_RATES = 4500
ANNUAL_MAINT = 2000
STAMP_DUTY_FEES = 60000
CAPITAL_GROWTH_PCT = 0.05
TAX_RATE = 0.37
DEPRECIATION_Y1 = 10000
SALE_COST_PCT = 0.02
ASSUME_NEW_BUILD_DEFAULT = os.getenv("ASSUME_NEW_BUILD_DEFAULT", "No").lower() == 'yes'

NEW_BUILD_KEYWORDS = ['new build','brand new','newly built','under construction','house and land','off the plan']
DUAL_KEYWORDS = ['dual living','granny flat','dual occupancy','second dwelling']
SUBDIV_KEYWORDS = ['subdividable','stca','subdivision','development']
USABLE_KEYWORDS = ['usable','clear','flat','fully fenced','cleared']

def check_keywords(text, keyword_list):
    if not text: return False
    return any(k in text.lower() for k in keyword_list)

def classify_build(desc, title=''):
    text = f"{title} {desc}".lower()
    if check_keywords(text, NEW_BUILD_KEYWORDS): return 'New Build / Likely Eligible'
    return 'Established / Assume Quarantined' if not ASSUME_NEW_BUILD_DEFAULT else 'New Build / Assumed'

def parse_rent_price(val):
    if isinstance(val, (int,float)): return val
    if not isinstance(val, str): return None
    nums = re.findall(r'\d+', val.replace(',', ''))
    if nums:
        n = int(nums[0])
        return n if 200 <= n <= 3000 else None
    return None

def calculate_financials(price, weekly_rent, build_class, capex=0):
    if pd.isna(price) or pd.isna(weekly_rent) or price == 0:
        return [None]*15 + ['Unknown', '']
    annual_rent = weekly_rent * 52
    operating_expenses = (annual_rent * MGMT_FEE_PCT) + ANNUAL_RATES + ANNUAL_MAINT
    noi = annual_rent - operating_expenses
    cap_rate = (noi / price) * 100
    gross_yield = (annual_rent / price) * 100
    net_yield = (noi / price) * 100
    actual_loan = MAX_LOAN_AMOUNT if price >= MAX_LOAN_AMOUNT else price * 0.80
    total_cash_invested = max((price - actual_loan) + STAMP_DUTY_FEES, STAMP_DUTY_FEES) + capex
    monthly_rate = INTEREST_RATE / 12
    months = LOAN_TERM_YEARS * 12
    monthly_repay = actual_loan * (monthly_rate * (1 + monthly_rate)**months) / ((1 + monthly_rate)**months - 1)
    annual_repay = monthly_repay * 12
    dscr = noi / annual_repay if annual_repay > 0 else 0
    break_even_ratio = ((operating_expenses + annual_repay) / annual_rent) * 100 if annual_rent > 0 else 0
    net_annual_cashflow = noi - annual_repay
    net_weekly_cashflow = net_annual_cashflow / 52
    y1_interest = actual_loan * INTEREST_RATE
    paper_profit_loss = annual_rent - operating_expenses - y1_interest - DEPRECIATION_Y1

    if 'New Build' in build_class:
        budget_rule = '2026 Budget: losses deductible against other income'
        quarantined_loss = 0
        tax_benefit = abs(paper_profit_loss) * TAX_RATE if paper_profit_loss < 0 else -(paper_profit_loss * TAX_RATE)
    else:
        budget_rule = '2026 Budget: established losses quarantined, no wage offset'
        quarantined_loss = abs(paper_profit_loss) if paper_profit_loss < 0 else 0
        tax_benefit = 0

    post_tax_cashflow = net_annual_cashflow + tax_benefit
    coc_return = (net_annual_cashflow / total_cash_invested) * 100 if total_cash_invested > 0 else 0
    y1_equity = total_cash_invested + (price * CAPITAL_GROWTH_PCT)
    roe = (net_annual_cashflow / y1_equity) * 100 if y1_equity > 0 else 0

    cash_flows = [-total_cash_invested]
    for _ in range(1, 10): cash_flows.append(net_annual_cashflow)
    future_value = price * ((1 + CAPITAL_GROWTH_PCT) ** 10)
    periods_remaining = (LOAN_TERM_YEARS - 10) * 12
    remaining_loan = monthly_repay * ((1 - (1 + monthly_rate)**-periods_remaining) / monthly_rate)
    nominal_gain = max(future_value - price, 0)
    if 'New Build' in build_class:
        cgt_est = nominal_gain * 0.25 * TAX_RATE
    else:
        cgt_est = nominal_gain * 0.30
        cgt_est = max(cgt_est - quarantined_loss, 0)
    net_proceeds = future_value - remaining_loan - (future_value * SALE_COST_PCT) - cgt_est
    cash_flows.append(net_annual_cashflow + net_proceeds)
    try: irr = npf.irr(cash_flows) * 100
    except: irr = None
    status = 'Positive' if net_weekly_cashflow > 0 else 'Negative'
    return [round(noi,2),round(cap_rate,2),round(gross_yield,2),round(net_yield,2),round(monthly_repay,2),round(dscr,2),round(break_even_ratio,2),round(net_annual_cashflow,2),round(net_weekly_cashflow,2),round(quarantined_loss,2),round(tax_benefit,2),round(post_tax_cashflow,2),round(total_cash_invested,2),round(coc_return,2),round(irr,2) if irr is not None else None,round(roe,2),status,budget_rule]

def fetch_properties(client, operation='buy'):
    run_input = {'location': SUBURBS,'operation': operation,'priceMax': MAX_PRICE if operation != 'rent' else None,'landAreaMin': MIN_LAND_M2,'maxItems': 150}
    run = client.actor('fatihtahta/realestate-com-au-scraper').call(run_input=run_input)
    return list(client.dataset(run['defaultDatasetId']).iterate_items())

def main():
    os.makedirs('data', exist_ok=True)
    client = ApifyClient(APIFY_API_TOKEN)
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    today_dt = datetime.datetime.now()
    rent_raw = fetch_properties(client, 'rent')
    suburb_medians = {}
    for item in rent_raw:
        rp = parse_rent_price(item.get('price'))
        if rp:
            sub = item.get('suburb', 'Unknown')
            suburb_medians.setdefault(sub, []).append(rp)
    suburb_medians = {k: sum(v)/len(v) for k,v in suburb_medians.items()}
    market = pd.DataFrame([{'Date Pulled':today,'Suburb':k,'Median Acreage Rent ($)':v,'Interest Rate (%)':INTEREST_RATE*100} for k,v in suburb_medians.items()])
    market.to_csv('data/market_data_v5.csv', index=False)

    buy_rows = []
    for item in fetch_properties(client, 'buy'):
        price = item.get('price', 0)
        suburb = item.get('suburb', 'Unknown')
        rent = suburb_medians.get(suburb, item.get('rent_estimate', 900))
        land_m2 = item.get('landArea', 0) or 0
        desc = item.get('description', '')
        title = item.get('headline', '') if isinstance(item.get('headline',''), str) else ''
        build_class = classify_build(desc, title)
        metrics = calculate_financials(price, rent, build_class, capex=0)
        date_listed = item.get('dateListed')
        dom = None
        if date_listed:
            try:
                listed_dt = datetime.datetime.fromisoformat(date_listed.replace('Z','+00:00')).replace(tzinfo=None)
                dom = (today_dt - listed_dt).days
            except: pass
        price_per_acre = round(price / (land_m2 / 4046.86)) if land_m2 and price else None
        buy_rows.append({
            'Date Pulled': today,
            'Address': item.get('address',''),
            'Suburb': suburb,
            'Property Type': item.get('propertyType',''),
            'Beds': item.get('bedrooms', None),
            'Baths': item.get('bathrooms', None),
            'Cars': item.get('carSpaces', None),
            'Land Size (m2)': land_m2,
            'Asking Price ($)': price,
            'Price Per Acre ($)': price_per_acre,
            'Days on Market': dom,
            'Sale Method': 'Auction' if item.get('isAuction') else 'For Sale',
            'Agency': item.get('agency', {}).get('name', 'Unknown') if isinstance(item.get('agency'), dict) else 'Unknown',
            'Dual Living / Granny Flat': check_keywords(desc, DUAL_KEYWORDS),
            'Subdivision Potential': check_keywords(desc, SUBDIV_KEYWORDS),
            'Usable Land': check_keywords(desc, USABLE_KEYWORDS),
            'Build Classification': build_class,
            'Budget Rule Applied': metrics[17],
            'Dynamic Rent Est ($)': rent,
            'NOI ($)': metrics[0],
            'Cap Rate (%)': metrics[1],
            'Gross Yield (%)': metrics[2],
            'Net Yield (%)': metrics[3],
            'Monthly Repayment ($)': metrics[4],
            'DSCR': metrics[5],
            'Break-Even Ratio (%)': metrics[6],
            'Net Annual Cashflow ($)': metrics[7],
            'Net Weekly Cashflow ($)': metrics[8],
            'Quarantined Loss Year 1 ($)': metrics[9],
            'Tax Benefit Year 1 ($)': metrics[10],
            'Post-Tax Cash Flow ($) *Est': metrics[11],
            'Est Renovation / Capex ($)': 0,
            'Total Cash Invested ($)': metrics[12],
            'Cash-on-Cash Return (%)': metrics[13],
            'Est 10-Yr IRR (%)': metrics[14],
            'Est Year 1 ROE (%)': metrics[15],
            'Cashflow Status': metrics[16],
            'URL': item.get('url','')
        })
    pd.DataFrame(buy_rows).to_csv('data/buy_properties_v5.csv', index=False)

    sold_rows = []
    for item in fetch_properties(client, 'sold'):
        land_m2 = item.get('landArea', 0) or 0
        price = item.get('price', 0) or 0
        price_per_acre = round(price / (land_m2 / 4046.86)) if land_m2 and price else None
        sold_rows.append({
            'Date Pulled': today,
            'Address': item.get('address',''),
            'Suburb': item.get('suburb',''),
            'Property Type': item.get('propertyType',''),
            'Beds': item.get('bedrooms', None),
            'Baths': item.get('bathrooms', None),
            'Cars': item.get('carSpaces', None),
            'Land Size (m2)': land_m2,
            'Sale Price ($)': price,
            'Price Per Acre ($)': price_per_acre,
            'Sale Date': item.get('soldDate',''),
            'Agency': item.get('agency', {}).get('name', 'Unknown') if isinstance(item.get('agency'), dict) else 'Unknown',
            'URL': item.get('url','')
        })
    pd.DataFrame(sold_rows).to_csv('data/sold_properties_v5.csv', index=False)

if __name__ == '__main__':
    main()
