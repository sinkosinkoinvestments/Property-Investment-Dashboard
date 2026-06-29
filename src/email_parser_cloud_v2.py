import os
import imaplib
import email
from email import policy
from email.parser import BytesParser
import pandas as pd
from datetime import datetime, timedelta
import re

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")

def check_keywords(text, keyword_list):
    if not text: return False
    return any(kw in text.lower() for kw in keyword_list)

def extract_price(text):
    if not text: return None
    text = text.lower().replace(',', '')
    
    # Check for $1.8M, $1.8 Mil, etc.
    mil_match = re.search(r'\$\s*([0-9.]+)\s*(m|mil|million)', text)
    if mil_match:
        try:
            val = float(mil_match.group(1)) * 1000000
            if val > 10000: return val
        except: pass
        
    # Standard $1,800,000
    prices = re.findall(r'\$([0-9]{4,})', text)
    if prices:
        try:
            val = float(prices[0])
            if val > 10000: # Ignore $1, $2 footer values
                return val
        except: pass
        
    return None

def extract_land_size(text):
    if not text: return None
    match = re.search(r'([0-9,.]+)\s*(sqm|m2|m²|acres|acre|ha|hectares)', text, re.IGNORECASE)
    if match:
        val = match.group(1).replace(',', '')
        unit = match.group(2).lower()
        try:
            num = float(val)
            if unit in ['acres', 'acre']: return num * 4046.86
            if unit in ['ha', 'hectares']: return num * 10000
            return num 
        except: return None
    return None

def extract_address(subject, text):
    combined = f"{subject} {text}"
    match = re.search(r'\b(\d{1,4}\s+[A-Za-z\s]+(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Place|Pl|Lane|Ln|Boulevard|Blvd|Way))\b', combined, re.IGNORECASE)
    if match: return match.group(1).title()
    return None

def main():
    os.makedirs('data', exist_ok=True)
    if not IMAP_USER or not IMAP_PASSWORD:
        print("Missing IMAP credentials.")
        return

    qualified_rows = []
    
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(IMAP_USER, IMAP_PASSWORD)
            imap.select("INBOX", readonly=True)

            # Search ALL emails from the last 3 days (ignores seen/unseen status)
            date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
            status, data = imap.search(None, f'(SINCE {date})')

            for msg_id in data[0].split():
                status, msg_data = imap.fetch(msg_id, '(RFC822)')
                if status == 'OK' and msg_data[0]:
                    msg = BytesParser(policy=policy.default).parsebytes(msg_data[0][1])
                    subject = str(msg.get('subject', ''))
                    
                    body_text = ""
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            body_text += str(part.get_content())

                    combined = f"{subject} {body_text}"
                    
                    address = extract_address(subject, body_text)
                    price = extract_price(combined)
                    land_size = extract_land_size(combined)
                    
                    # Debug block
                    print(f"\nChecking Email: '{subject}'")
                    print(f"Address: {address} | Price: {price} | Land: {land_size}")
                    
                    # Qualification
                    if address and price and land_size:
                        suburb_guess = "Off-Market"
                        
                        qualified_rows.append({
                            "Address": address,
                            "Suburb": suburb_guess,
                            "Property Type": "House",
                            "Asking Price ($)": price,
                            "Land Size (m2)": land_size,
                            "Beds": 3,
                            "Baths": 1,
                            "Cars": 1,
                            "URL": "Email Lead",
                            "Dual Living / Granny Flat": str(check_keywords(combined, ['dual living', 'granny flat', 'dual occupancy'])).lower(),
                            "Subdivision Potential": str(check_keywords(combined, ['subdividable', 'stca', 'subdivision', 'development'])).lower(),
                            "Usable Land": str(check_keywords(combined, ['usable', 'clear', 'flat', 'arable'])).lower(),
                            "Cashflow Status": "Unknown"
                        })
                        
    except Exception as e:
        print(f"IMAP Error: {e}")

    if not qualified_rows:
        print("No qualified property emails found.")
        return

    new_df = pd.DataFrame(qualified_rows)
    file_path = "data/buy_properties_v5.csv"

    if os.path.exists(file_path):
        try:
            existing_df = pd.read_csv(file_path)
            # Combine and drop duplicates based on Address
            df = pd.concat([existing_df, new_df]).drop_duplicates(subset=["Address"], keep='first')
        except pd.errors.EmptyDataError:
            # Handle if the CSV exists but is completely blank
            df = new_df
    else:
        df = new_df

    df.to_csv(file_path, index=False)
    print(f"\nSuccessfully added {len(new_df)} qualified email leads directly to buy_properties_v5.csv.")

if __name__ == "__main__":
    main()
