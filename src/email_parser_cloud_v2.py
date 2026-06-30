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
    text = text.lower()
    return any(kw in text for kw in keyword_list)

def extract_urls(text):
    if not text: return ""
    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
    # Filter out common junk URLs
    valid_urls = [u for u in urls if 'w3.org' not in u and 'google.com' not in u and 'w3.org' not in u]
    return " | ".join(valid_urls)

def extract_price(text):
    if not text: return ""
    # Look for $ followed by numbers, commas, optionally M or K
    prices = re.findall(r'\$[0-9,]+(?:\.[0-9]+)?(?:[MmKk])?', text)
    return prices[0] if prices else ""

def parse_price_value(price_str):
    if not price_str: return None
    s = price_str.upper().replace('$', '').replace(',', '')
    if 'M' in s:
        return float(s.replace('M','')) * 1000000
    if 'K' in s:
        return float(s.replace('K','')) * 1000
    try:
        return float(s)
    except:
        return None

def extract_land_m2(text):
    text = text.lower().replace(',', '')
    if 'ha' in text or 'hectare' in text:
        nums = re.findall(r'(\d+(?:\.\d+)?)\s*(?:ha|hectare)', text)
        if nums: return float(nums[0]) * 10000
    if 'acre' in text:
        nums = re.findall(r'(\d+(?:\.\d+)?)\s*acre', text)
        if nums: return float(nums[0]) * 4046.86
    return None

def main():
    os.makedirs('data', exist_ok=True)
    if not IMAP_USER or not IMAP_PASSWORD:
        print("Missing IMAP credentials.")
        return

    rows = []
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(IMAP_USER, IMAP_PASSWORD)
            imap.select("INBOX", readonly=False)

            # Search for BOTH Unseen and Seen emails from the last 3 days to catch missed properties
            # Or just search ALL emails from known real estate agents/keywords
            # For safety, let's search UNSEEN first, but broaden what we extract
            status, data = imap.search(None, '(UNSEEN)')

            for msg_id in data[0].split():
                status, msg_data = imap.fetch(msg_id, '(RFC822)')
                if status == 'OK' and msg_data[0]:
                    msg = BytesParser(policy=policy.default).parsebytes(msg_data[0][1])
                    subject = str(msg.get('subject', ''))
                    sender = str(msg.get('from', ''))

                    body_text = ""
                    # Try to get both plain and html parts
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            body_text += str(part.get_content())
                        elif part.get_content_type() == 'text/html':
                            # Even if it's HTML, we'll convert it to string for basic parsing
                            body_text += str(part.get_content())

                    combined = f"{subject} {body_text}"

                    # Basic filter - only log if it seems property related
                    prop_keywords = ['property', 'off-market', 'off market', 'listing', 'for sale', 'investment', 'acreage']
                    is_property = check_keywords(combined, prop_keywords)

                    if is_property:
                        price_str = extract_price(combined)
                        price_val = parse_price_value(price_str)
                        land_m2 = extract_land_m2(combined)

                        # Filter for Price < 1.8M and Land > 2 Acres
                        if price_val and price_val > 1800000:
                            continue
                        if land_m2 and land_m2 < 8094:
                            continue

                        rows.append({
                            "Date Parsed": datetime.now().strftime('%Y-%m-%d'),
                            "Sender": sender,
                            "Subject": subject,
                            "Price Mentioned": extract_price(combined),
                            "Links": extract_urls(combined),
                            "Dual Living / Granny Flat": check_keywords(combined, ['dual living', 'granny flat', 'dual occupancy', 'second dwelling']),
                            "Subdivision Potential": check_keywords(combined, ['subdividable', 'stca', 'subdivision', 'development']),
                            "Usable Land": check_keywords(combined, ['usable', 'clear', 'flat', 'arable']),
                            "Source": "Email Lead"
                        })

                        # Mark as seen so we don't process it again next run
                        imap.store(msg_id, '+FLAGS', '\\Seen')

    except Exception as e:
        print(f"IMAP Error: {e}")

    if not rows:
        print("No new property emails found.")
        return

    new_df = pd.DataFrame(rows)
    file_path = "data/offmarket_leads_v3.csv"

    if os.path.exists(file_path):
        try:
            existing_df = pd.read_csv(file_path)
            # Combine and drop duplicates based on Address to prevent adding the same property twice
            df = pd.concat([existing_df, new_df]).drop_duplicates(subset=["Address"], keep='first')
        except pd.errors.EmptyDataError:
            df = new_df
        # Check if Subject and Sender already exist to avoid duplicates
        df = pd.concat([existing_df, new_df]).drop_duplicates(subset=["Subject", "Sender"], keep='last')
    else:
        df = new_df

    df.to_csv(file_path, index=False)
    print(f"Added {len(new_df)} new email leads.")

if __name__ == "__main__":
    main()
