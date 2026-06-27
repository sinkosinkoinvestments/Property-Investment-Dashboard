# Hinterland Property Investment Tracker

An automated, cloud-native data pipeline that scrapes real estate listings, parses off-market email alerts, and calculates professional-grade investment metrics (including DSCR, Cash-on-Cash Return, 10-Yr IRR, and Post-Budget 2026 Tax Rules).

## 📂 Repository Structure

```text
hinterland-property-tracker/
├── .github/workflows/daily_update_v5.yml    # GitHub Action workflow (Runs daily)
├── src/                                     # Python source code
│   ├── property_updater_cloud_v5.py
│   └── email_parser_cloud_v2.py
├── data/                                    # Automated CSV outputs
│   ├── buy_properties_v5.csv
│   ├── sold_properties_v5.csv
│   ├── market_data_v5.csv
│   └── offmarket_leads_v3.csv
├── dashboard/                               # Excel Dashboard
│   └── Property_Investment_Dashboard_V5.xlsx
├── requirements.txt
├── .gitignore
└── README.md
```

## 🚀 Setup Instructions

1. **Upload Files:** Upload this entire structure to a private GitHub repository.
2. **Add Secrets & Variables:** 
   - `Settings > Secrets > Actions`: Add `APIFY_API_TOKEN`, `IMAP_USER`, and `IMAP_PASSWORD`.
   - `Settings > Variables > Actions`: Add `CURRENT_INTEREST_RATE` (e.g., `0.065`) and `ASSUME_NEW_BUILD_DEFAULT` (e.g., `No`).
3. **Run Pipeline:** Go to the `Actions` tab in GitHub, select the workflow, and click `Run workflow`.
4. **Connect Excel:** Open `dashboard/Property_Investment_Dashboard_V5.xlsx` locally. Use `Data > Get Data > From Web` to link the data tabs to the `raw.githubusercontent.com` URLs from your `data/` folder, authenticating via Basic auth with a Personal Access Token (PAT).
