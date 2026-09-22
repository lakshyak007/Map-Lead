# MapLead — Streamlit Community Cloud Edition

A Streamlit version of MapLead, designed for deployment through GitHub + Streamlit Community Cloud.

## Project structure

```text
MapLead_Streamlit/
├── streamlit_app.py
├── requirements.txt
├── packages.txt
├── .streamlit/
│   └── config.toml
└── README.md
```

## Local test

Python 3.10+ recommended.

```bash
python -m venv venv
```

Windows:

```bat
venv\Scripts\activate
```

macOS/Linux:

```bash
source venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
python -m playwright install chromium
streamlit run streamlit_app.py
```

## Deploy to Streamlit Community Cloud

1. Create a GitHub repository.
2. Upload all files from this folder.
3. Go to Streamlit Community Cloud.
4. Create a new app.
5. Select your GitHub repository.
6. Set the entrypoint to:

```text
streamlit_app.py
```

7. Deploy.

The repository includes `requirements.txt` for Python dependencies and `packages.txt` for Linux/Chromium dependencies. Streamlit Community Cloud supports both mechanisms. See the official Streamlit deployment documentation.

## Important browser note

This application uses Playwright + Chromium. The app attempts to install Chromium when it starts if the browser is not available.

Community Cloud has finite CPU, memory, and storage resources. Large scraping jobs can therefore be slow or may hit platform limits. For larger production workloads, a VPS/containerized browser worker is more appropriate.

## Data persistence

The app currently uses SQLite (`maplead.db`). On ephemeral/cloud environments, local files should not be treated as a permanent production database. For a production SaaS, move the leads database to PostgreSQL or another managed database.

## Responsible use

Google Maps can change its interface and can show CAPTCHA or verification. Do not bypass CAPTCHA, authentication, access controls, or other security mechanisms. Use public business information for legitimate purposes and comply with applicable laws and the terms of services you use.

For large-scale or commercial data collection, consider official APIs or a licensed business-data provider.
