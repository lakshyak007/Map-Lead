import asyncio
import io
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# -----------------------------
# App configuration
# -----------------------------
st.set_page_config(
    page_title="MapLead — Google Maps Lead Generator",
    page_icon="📍",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "maplead.db"

SOCIALS = {
    "Facebook": "facebook.com",
    "Instagram": "instagram.com",
    "LinkedIn": "linkedin.com",
    "X / Twitter": "x.com",
    "YouTube": "youtube.com",
    "TikTok": "tiktok.com",
}

DB_COLUMNS = [
    "name", "phone", "website", "address", "rating", "reviews",
    "facebook", "instagram", "linkedin", "x_twitter", "youtube",
    "tiktok", "maps_url", "status", "priority", "notes", "created_at"
]


# -----------------------------
# Styling
# -----------------------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: Inter, sans-serif;
}

.block-container {
    max-width: 1500px;
    padding-top: 2rem;
    padding-bottom: 3rem;
}

.maplead-title {
    font-size: 2.1rem;
    font-weight: 800;
    letter-spacing: -1px;
    margin-bottom: 0;
}

.maplead-subtitle {
    color: #7b8497;
    font-size: .88rem;
    margin-top: .25rem;
    margin-bottom: 1.4rem;
}

.metric-card {
    background: white;
    border: 1px solid #e6eaf1;
    border-radius: 15px;
    padding: 17px 18px;
    min-height: 98px;
    box-shadow: 0 8px 25px rgba(24,35,56,.045);
}

.metric-label {
    color: #8a94a8;
    font-size: .68rem;
    font-weight: 800;
    letter-spacing: 1px;
    text-transform: uppercase;
}

.metric-value {
    color: #172033;
    font-size: 1.65rem;
    font-weight: 800;
    margin-top: 7px;
}

div.stButton > button {
    border-radius: 10px;
    font-weight: 700;
}

.primary-action button {
    background: #4f7cff !important;
    color: white !important;
    border: 0 !important;
}

[data-testid="stDataFrame"] {
    border-radius: 12px;
    overflow: hidden;
}

section[data-testid="stSidebar"] {
    border-right: 1px solid #e7eaf0;
}

.small-muted {
    color: #8993a6;
    font-size: .78rem;
}
</style>
""",
    unsafe_allow_html=True,
)


# -----------------------------
# Database
# -----------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            website TEXT,
            address TEXT,
            rating TEXT,
            reviews TEXT,
            facebook TEXT,
            instagram TEXT,
            linkedin TEXT,
            x_twitter TEXT,
            youtube TEXT,
            tiktok TEXT,
            maps_url TEXT UNIQUE,
            status TEXT DEFAULT 'New',
            priority TEXT DEFAULT 'Normal',
            notes TEXT DEFAULT '',
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def get_leads():
    conn = get_db()
    rows = conn.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
    conn.close()
    return pd.DataFrame([dict(row) for row in rows])


def save_lead(lead):
    fields = DB_COLUMNS
    placeholders = ",".join(["?"] * len(fields))
    values = [lead.get(field, "") for field in fields]

    conn = get_db()
    conn.execute(
        f"""
        INSERT OR IGNORE INTO leads ({','.join(fields)})
        VALUES ({placeholders})
        """,
        values,
    )
    conn.commit()
    conn.close()


def update_lead(lead_id, field, value):
    if field not in {"status", "priority", "notes"}:
        return

    conn = get_db()
    conn.execute(
        f"UPDATE leads SET {field}=? WHERE id=?",
        (value, lead_id),
    )
    conn.commit()
    conn.close()


# -----------------------------
# Browser setup
# -----------------------------
@st.cache_resource(show_spinner=False)
def ensure_playwright_browser():
    """
    Community Cloud is Linux-based. Install Chromium once per app
    environment if it isn't already available.
    """
    try:
        from playwright._impl._driver import compute_driver_executable
        # Playwright Python package itself is installed by requirements.txt.
        # Browser installation is separate.
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            capture_output=True,
            text=True,
            timeout=240,
        )
        return True
    except Exception as exc:
        raise RuntimeError(
            "Could not install/start Playwright Chromium. "
            f"Details: {exc}"
        )


# -----------------------------
# Scraper helpers
# -----------------------------
def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_url(url):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if not re.match(r"^https?://", url, re.I):
        return "https://" + url
    return url


def find_socials(website):
    result = {key: "" for key in SOCIALS}

    if not website:
        return result

    try:
        response = requests.get(
            website,
            timeout=10,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
                )
            },
        )

        if not response.ok:
            return result

        soup = BeautifulSoup(response.text, "html.parser")

        for anchor in soup.find_all("a", href=True):
            href = urljoin(response.url, anchor["href"])
            lower = href.lower()

            for name, domain in SOCIALS.items():
                if not result[name] and domain in lower:
                    result[name] = href.split("?")[0].split("#")[0]

    except requests.RequestException:
        pass

    return result


async def first_text(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count():
                value = clean(await locator.first.inner_text())
                if value:
                    return value
        except Exception:
            pass

    return ""


async def first_attr(page, selectors, attribute):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count():
                value = clean(await locator.first.get_attribute(attribute))
                if value:
                    return value
        except Exception:
            pass

    return ""


async def extract_business(page):
    name = await first_text(page, ["h1.DUwDvf", "h1"])

    address = await first_attr(
        page,
        ['[data-item-id="address"]'],
        "aria-label",
    )

    if not address:
        address = await first_text(
            page,
            ['[data-item-id="address"]'],
        )

    address = re.sub(
        r"^Address:\s*",
        "",
        address,
        flags=re.I,
    )

    phone = await first_attr(
        page,
        ['[data-item-id^="phone:"]'],
        "aria-label",
    )

    if not phone:
        phone = await first_text(
            page,
            ['[data-item-id^="phone:"]'],
        )

    phone = re.sub(
        r"^Phone:\s*",
        "",
        phone,
        flags=re.I,
    )

    website = await first_attr(
        page,
        [
            '[data-item-id="authority"] a',
            'a[data-item-id="authority"]',
            'a[aria-label*="Website"]',
        ],
        "href",
    )

    website = normalize_url(website)

    rating_block = await first_text(
        page,
        ["div.F7nice"],
    )

    rating = ""
    reviews = ""

    match = re.search(
        r"([0-5](?:\.[0-9])?)",
        rating_block or "",
    )

    if match:
        rating = match.group(1)

    match = re.search(
        r"([\d,]+)\s*(?:reviews|Reviews)",
        rating_block or "",
    )

    if match:
        reviews = match.group(1)

    return {
        "name": name,
        "phone": phone,
        "website": website,
        "address": address,
        "rating": rating,
        "reviews": reviews,
        **{
            key.lower().replace(" / ", "_").replace(" ", "_"): value
            for key, value in find_socials(website).items()
        },
        "maps_url": page.url,
        "status": "New",
        "priority": "Normal",
        "notes": "",
        "created_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def scrape_google_maps(query, limit, progress_callback):
    search_url = (
        "https://www.google.com/maps/search/?api=1&query="
        + quote(query)
    )

    links = []
    seen = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        page = await context.new_page()

        progress_callback(
            0,
            max(limit, 1),
            "Opening Google Maps…",
        )

        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(3000)

        for _ in range(50):
            anchors = page.locator(
                'div[role="feed"] a[href*="/maps/place/"]'
            )

            count = await anchors.count()

            for index in range(count):
                href = await anchors.nth(index).get_attribute("href")

                if (
                    href
                    and "/maps/place/" in href
                    and href not in seen
                ):
                    seen.add(href)
                    links.append(href)

                    if len(links) >= limit:
                        break

            progress_callback(
                0,
                max(limit, 1),
                f"Finding businesses… {len(links)} found",
            )

            if len(links) >= limit:
                break

            try:
                feed = page.locator('div[role="feed"]')

                if await feed.count():
                    await feed.evaluate(
                        "(element) => element.scrollTo(0, element.scrollHeight)"
                    )
                else:
                    await page.mouse.wheel(0, 5000)

            except Exception:
                await page.mouse.wheel(0, 5000)

            await page.wait_for_timeout(1000)

        total = len(links)
        results = []

        for index, link in enumerate(links, 1):
            try:
                await page.goto(
                    link,
                    wait_until="domcontentloaded",
                    timeout=45000,
                )

                await page.wait_for_timeout(1200)

                lead = await extract_business(page)

                if lead["name"]:
                    save_lead(lead)
                    results.append(lead)

                progress_callback(
                    index,
                    max(total, 1),
                    f"Scraping {index}/{total}: "
                    f"{lead.get('name', 'Unknown')}",
                )

            except PlaywrightTimeoutError:
                progress_callback(
                    index,
                    max(total, 1),
                    f"Skipped timeout {index}/{total}",
                )

            except Exception as exc:
                progress_callback(
                    index,
                    max(total, 1),
                    f"Skipped {index}/{total}: {exc}",
                )

        await browser.close()

    return results


# -----------------------------
# UI
# -----------------------------
with st.sidebar:
    st.markdown("## 📍 MapLead")
    st.caption("Google Maps lead-generation workspace")

    st.divider()

    st.markdown("### Search")

    keyword = st.text_input(
        "Keyword",
        placeholder="restaurants, dentists, gyms…",
    )

    city = st.text_input(
        "City / Location",
        placeholder="Gurgaon",
    )

    result_limit = st.slider(
        "Maximum results",
        min_value=5,
        max_value=200,
        value=50,
        step=5,
    )

    start = st.button(
        "🚀 Start Scraping",
        type="primary",
        use_container_width=True,
    )

    st.divider()

    st.markdown("### Export")

    st.download_button(
        "⬇️ Export CSV",
        data=(
            get_leads().to_csv(index=False).encode("utf-8-sig")
            if not get_leads().empty
            else b""
        ),
        file_name="maplead_leads.csv",
        mime="text/csv",
        use_container_width=True,
    )

    current = get_leads()

    excel_buffer = io.BytesIO()

    if not current.empty:
        with pd.ExcelWriter(
            excel_buffer,
            engine="openpyxl",
        ) as writer:
            current.to_excel(
                writer,
                index=False,
                sheet_name="Leads",
            )

    st.download_button(
        "⬇️ Export Excel",
        data=excel_buffer.getvalue(),
        file_name="maplead_leads.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True,
    )

    st.divider()

    st.caption(
        "The scraper uses public business information. "
        "Do not bypass CAPTCHA, authentication, or access controls."
    )


st.markdown(
    '<div class="maplead-title">Lead Dashboard</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="maplead-subtitle">'
    "Find local businesses and turn public business data into organized leads."
    "</div>",
    unsafe_allow_html=True,
)


# Scraping action
if start:
    if not keyword.strip() or not city.strip():
        st.error("Enter both a keyword and a city/location.")
    else:
        query = f"{keyword.strip()} in {city.strip()}"

        try:
            ensure_playwright_browser()

            progress = st.progress(0)
            message = st.empty()

            def progress_callback(done, total, text):
                progress.progress(
                    min(done / max(total, 1), 1.0)
                )
                message.info(text)

            with st.status(
                f"Searching Google Maps for: {query}",
                expanded=True,
            ) as status:
                results = asyncio.run(
                    scrape_google_maps(
                        query,
                        result_limit,
                        progress_callback,
                    )
                )

                status.update(
                    label=(
                        f"Completed — {len(results)} new businesses collected."
                    ),
                    state="complete",
                    expanded=False,
                )

            st.rerun()

        except Exception as exc:
            st.error(
                "The scraper could not start or Google Maps could not be "
                f"processed. Details: {exc}"
            )


# Load data
df = get_leads()

if df.empty:
    st.info(
        "Enter a keyword and city in the sidebar and click "
        "**Start Scraping** to collect your first leads."
    )
else:
    # Metrics
    total = len(df)
    phones = int(
        df["phone"].fillna("").astype(str).str.strip().ne("").sum()
    )
    websites = int(
        df["website"].fillna("").astype(str).str.strip().ne("").sum()
    )

    ratings = pd.to_numeric(
        df["rating"],
        errors="coerce",
    )

    four_plus = int((ratings >= 4).sum())

    metrics = [
        ("Total Leads", total),
        ("With Phone", phones),
        ("With Website", websites),
        ("No Website", total - websites),
        ("4+ Rating", four_plus),
    ]

    cols = st.columns(5)

    for col, (label, value) in zip(cols, metrics):
        col.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">{label}</div>
                <div class="metric-value">{value}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")

    # Filters
    f1, f2, f3, f4, f5 = st.columns([2, 1, 1, 1, 1])

    search_filter = f1.text_input(
        "Search leads",
        placeholder="Business, phone, address…",
    )

    website_filter = f2.selectbox(
        "Website",
        ["All", "Has website", "No website"],
    )

    phone_filter = f3.selectbox(
        "Phone",
        ["All", "Has phone", "No phone"],
    )

    rating_filter = f4.selectbox(
        "Rating",
        ["Any", "4.0+", "4.5+"],
    )

    social_filter = f5.selectbox(
        "Social",
        ["All", "Has social", "No social"],
    )

    filtered = df.copy()

    if search_filter:
        query_lower = search_filter.lower()

        mask = filtered.astype(str).apply(
            lambda col: col.str.lower().str.contains(
                query_lower,
                regex=False,
            )
        ).any(axis=1)

        filtered = filtered[mask]

    if website_filter == "Has website":
        filtered = filtered[
            filtered["website"].fillna("").str.strip().ne("")
        ]

    elif website_filter == "No website":
        filtered = filtered[
            filtered["website"].fillna("").str.strip().eq("")
        ]

    if phone_filter == "Has phone":
        filtered = filtered[
            filtered["phone"].fillna("").str.strip().ne("")
        ]

    elif phone_filter == "No phone":
        filtered = filtered[
            filtered["phone"].fillna("").str.strip().eq("")
        ]

    if rating_filter != "Any":
        minimum = 4.5 if rating_filter == "4.5+" else 4.0
        ratings = pd.to_numeric(
            filtered["rating"],
            errors="coerce",
        )
        filtered = filtered[ratings >= minimum]

    social_columns = [
        "facebook",
        "instagram",
        "linkedin",
        "x_twitter",
        "youtube",
        "tiktok",
    ]

    social_exists = filtered[social_columns].fillna("").astype(str).apply(
        lambda row: row.str.strip().ne("").any(),
        axis=1,
    )

    if social_filter == "Has social":
        filtered = filtered[social_exists]

    elif social_filter == "No social":
        filtered = filtered[~social_exists]

    st.caption(
        f"Showing {len(filtered)} of {len(df)} leads"
    )

    display = filtered[
        [
            "name",
            "phone",
            "website",
            "address",
            "rating",
            "reviews",
            "status",
            "priority",
            "notes",
            "maps_url",
        ]
    ].copy()

    display.columns = [
        "Business",
        "Phone",
        "Website",
        "Address",
        "Rating",
        "Reviews",
        "Status",
        "Priority",
        "Notes",
        "Google Maps",
    ]

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=520,
        column_config={
            "Website": st.column_config.LinkColumn(
                "Website",
                display_text="Open website",
            ),
            "Google Maps": st.column_config.LinkColumn(
                "Google Maps",
                display_text="Open Maps",
            ),
            "Rating": st.column_config.NumberColumn(
                "Rating",
                format="%.1f",
            ),
        },
    )

    st.divider()

    st.subheader("Lead details")

    if not filtered.empty:
        selected = st.selectbox(
            "Select a lead",
            options=filtered["id"].tolist(),
            format_func=lambda lead_id: (
                filtered.loc[
                    filtered["id"] == lead_id,
                    "name",
                ].iloc[0]
            ),
        )

        lead = filtered[
            filtered["id"] == selected
        ].iloc[0]

        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown(f"### {lead['name']}")
            st.write(lead["address"] or "No address")
            if lead["phone"]:
                st.write(f"📞 {lead['phone']}")
            if lead["website"]:
                st.link_button(
                    "Open Website",
                    lead["website"],
                )
            if lead["maps_url"]:
                st.link_button(
                    "Open Google Maps",
                    lead["maps_url"],
                )

        with c2:
            status = st.selectbox(
                "Lead Status",
                [
                    "New",
                    "Contacted",
                    "Interested",
                    "Converted",
                    "Not Interested",
                ],
                index=[
                    "New",
                    "Contacted",
                    "Interested",
                    "Converted",
                    "Not Interested",
                ].index(
                    lead["status"]
                    if lead["status"] in [
                        "New",
                        "Contacted",
                        "Interested",
                        "Converted",
                        "Not Interested",
                    ]
                    else "New"
                ),
            )

            priority = st.selectbox(
                "Priority",
                ["Normal", "High", "Low"],
                index=["Normal", "High", "Low"].index(
                    lead["priority"]
                    if lead["priority"] in ["Normal", "High", "Low"]
                    else "Normal"
                ),
            )

            if st.button("Save Lead", type="primary"):
                update_lead(selected, "status", status)
                update_lead(selected, "priority", priority)
                st.success("Lead updated.")
                st.rerun()

        with c3:
            notes = st.text_area(
                "Notes",
                value=lead["notes"] or "",
                height=130,
            )

            if st.button("Save Notes"):
                update_lead(selected, "notes", notes)
                st.success("Notes saved.")
                st.rerun()

        social_items = [
            ("Facebook", lead["facebook"]),
            ("Instagram", lead["instagram"]),
            ("LinkedIn", lead["linkedin"]),
            ("X / Twitter", lead["x_twitter"]),
            ("YouTube", lead["youtube"]),
            ("TikTok", lead["tiktok"]),
        ]

        available_socials = [
            item for item in social_items if item[1]
        ]

        if available_socials:
            st.markdown("#### Social profiles")
            social_cols = st.columns(
                min(4, len(available_socials))
            )

            for col, (name, url) in zip(
                social_cols,
                available_socials,
            ):
                with col:
                    st.link_button(
                        name,
                        url,
                        use_container_width=True,
                    )


st.divider()

st.caption(
    "MapLead • Local business lead workspace • "
    "Use public business information responsibly and comply with "
    "applicable laws and service terms."
)
