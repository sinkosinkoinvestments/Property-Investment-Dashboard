import os
import imaplib
import email
from email import policy
from email.parser import BytesParser
import pandas as pd
from datetime import datetime
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
    # Look for $ followed by numbers and commas
    prices = re.findall(r'\$([0-9,]+)', text)
    if prices:
        # Convert string like "1,200,000" to float
        clean_price = prices[0].replace(',', '')
        try:
            return float(clean_price)
        except:
            return None
    return None

def extract_land_size(text):
    if not text: return None
    # Look for numbers followed by sqm, m2, acres, ha
    match = re.search(r'([0-9,.]+)\s*(sqm|m2|m²|acres|ha)', text, re.IGNORECASE)
    if match:
        val = match.group(1).replace(',', '')
        unit = match.group(2).lower()
        try:
            num = float(val)
            # Convert to sqm
            if unit == 'acres': return num * 4046.86
            if unit == 'ha': return num * 10000
            return num # sqm or m2
        except:
            return None
    return None

def extract_address(subject, text):
    # Addresses are tricky. Often they are at the start of the subject or body.
    # Look for a number followed by words and Street/Rd/Ave etc.
    combined = f"{subject} {text}"
    match = re.search(r'\b(\d{1,4}\s+[A-Za-z\s]+(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Place|Pl|Lane|Ln|Boulevard|Blvd|Way))\b', combined, re.IGNORECASE)
    if match:
        return match.group(1).title()
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
            imap.select("INBOX", readonly=False)

            status, data = imap.search(None, '(UNSEEN)')

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
                    
                    # 1. Extraction
                    address = extract_address(subject, body_text)
                    price = extract_price(combined)
                    land_size = extract_land_size(combined)
                    
                    # 2. Qualification: MUST have Address, Land Size, and Price
                    if address and price and land_size:
                        
                        # Find suburb (last word of address as fallback, or regex extraction if needed)
                        suburb_guess = "Off-Market"
                        
                        qualified_rows.append({
                            "Address": address,
                            "Suburb": suburb_guess,
                            "Property Type": "House", # Default assumption for off-market
                            "Asking Price ($)": price,
                            "Land Size (m2)": land_size,
                            "Beds": 3,   # Placeholder or build regex for Beds
                            "Baths": 1,  # Placeholder or build regex for Baths
                            "Cars": 1,   # Placeholder or build regex for Cars
                            "URL": "Email Lead",
                            "Dual Living / Granny Flat": str(check_keywords(combined, ['dual living', 'granny flat', 'dual occupancy'])).lower(),
                            "Subdivision Potential": str(check_keywords(combined, ['subdividable', 'stca', 'subdivision', 'development'])).lower(),
                            "Usable Land": str(check_keywords(combined, ['usable', 'clear', 'flat', 'arable'])).lower(),
                            "Cashflow Status": "Unknown"
                        })
                        
                    imap.store(msg_id, '+FLAGS', '\\Seen')

    except Exception as e:
        print(f"IMAP Error: {e}")

    if not qualified_rows:
        print("No qualified property emails found.")
        return

    # 3. Save directly to buy_properties_v5.csv
    new_df = pd.DataFrame(qualified_rows)
    file_path = "data/buy_properties_v5.csv"

    if os.path.exists(file_path):
        existing_df = pd.read_csv(file_path)
        
        # Combine and drop duplicates based on Address to prevent adding the same property twice
        df = pd.concat([existing_df, new_df]).drop_duplicates(subset=["Address"], keep='first')
    else:
        df = new_df

    df.to_csv(file_path, index=False)
    print(f"Added {len(new_df)} qualified email leads directly to buy_properties_v5.csv.")

if __name__ == "__main__":
    main()
