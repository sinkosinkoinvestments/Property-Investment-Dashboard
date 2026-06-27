import os
import imaplib
from email import policy
from email.parser import BytesParser
import pandas as pd
from datetime import datetime

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")

def check_keywords(text, keyword_list):
    if not text: return False
    text = text.lower()
    return any(kw in text for kw in keyword_list)

def main():
    os.makedirs('data', exist_ok=True)
    if not IMAP_USER or not IMAP_PASSWORD:
        return

    rows = []
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(IMAP_USER, IMAP_PASSWORD)
            imap.select("INBOX", readonly=True)
            status, data = imap.search(None, 'UNSEEN')

            for msg_id in data[0].split():
                status, msg_data = imap.fetch(msg_id, '(RFC822)')
                if status == 'OK' and msg_data[0]:
                    msg = BytesParser(policy=policy.default).parsebytes(msg_data[0][1])
                    subject = str(msg.get('subject', ''))
                    sender = str(msg.get('from', ''))

                    body_text = ""
                    part = msg.get_body(preferencelist=('plain', 'html'))
                    if part:
                        body_text = str(part.get_content())

                    combined = f"{subject} {body_text}"

                    rows.append({
                        "Date Parsed": datetime.now().strftime('%Y-%m-%d'),
                        "Sender": sender,
                        "Subject": subject,
                        "Dual Living / Granny Flat": check_keywords(combined, ['dual living', 'granny flat']),
                        "Subdivision Potential": check_keywords(combined, ['subdividable', 'stca']),
                        "Usable Land": check_keywords(combined, ['usable', 'clear', 'flat']),
                        "Source": "Email Lead"
                    })
    except Exception as e:
        print(f"IMAP Error: {e}")

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["Date Parsed", "Sender", "Subject", "Source"])

    if os.path.exists("data/offmarket_leads_v3.csv"):
        existing_df = pd.read_csv("data/offmarket_leads_v3.csv")
        df = pd.concat([existing_df, df]).drop_duplicates(subset=["Subject"])

    df.to_csv("data/offmarket_leads_v3.csv", index=False)

if __name__ == "__main__":
    main()
