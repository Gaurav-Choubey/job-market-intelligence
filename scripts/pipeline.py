import os
import re
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd
from dotenv import load_dotenv


# =========================================================
# 1. CONFIGURATION
# =========================================================

load_dotenv()

APP_ID = os.getenv("ADZUNA_APP_ID")
APP_KEY = os.getenv("ADZUNA_APP_KEY")

if not APP_ID or not APP_KEY:
    raise ValueError("Adzuna API credentials not found in .env")

BASE_URL = "https://api.adzuna.com/v1/api/jobs/in/search"

SEARCHES = [
    ("data analyst", "Rajasthan"),
    ("business analyst", "Rajasthan"),
    ("power bi", "Rajasthan"),
    ("sql analyst", "Rajasthan"),
]

MAX_PAGES = 3
RESULTS_PER_PAGE = 50

raw_dir = Path("data/raw")
processed_dir = Path("data/processed")

raw_dir.mkdir(parents=True, exist_ok=True)
processed_dir.mkdir(parents=True, exist_ok=True)


# =========================================================
# 2. API EXTRACTION
# =========================================================

def fetch_jobs(what, where, max_pages=3, results_per_page=50):
    all_jobs = []

    for page in range(1, max_pages + 1):

        url = f"{BASE_URL}/{page}"

        params = {
            "app_id": APP_ID,
            "app_key": APP_KEY,
            "results_per_page": results_per_page,
            "what": what,
            "where": where
        }

        try:
            response = requests.get(
                url,
                params=params,
                timeout=30
            )

            print(
                f"{what} | {where} | "
                f"Page {page} | Status {response.status_code}"
            )

            if response.status_code == 503:
                print("Service unavailable. Waiting 10 seconds...")
                time.sleep(10)
                continue

            if response.status_code != 200:
                print("Request failed. Stopping this search.")
                break

            results = response.json().get("results", [])

            print(f"Jobs returned: {len(results)}")

            if not results:
                break

            all_jobs.extend(results)

            time.sleep(2)

        except requests.RequestException as e:
            print(f"Request error: {e}")
            break

    return all_jobs


# =========================================================
# 3. COLLECT RAW JOBS
# =========================================================

raw_jobs = []

for search_term, location in SEARCHES:

    results = fetch_jobs(
        what=search_term,
        where=location,
        max_pages=MAX_PAGES,
        results_per_page=RESULTS_PER_PAGE
    )

    raw_jobs.extend(results)


print(f"\nRaw jobs collected: {len(raw_jobs)}")


# =========================================================
# 4. SAVE RAW JSON
# =========================================================

raw_file = raw_dir / "jobs_raw.json"

with open(
    raw_file,
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        raw_jobs,
        f,
        ensure_ascii=False,
        indent=2
    )

print(f"Raw data saved: {raw_file}")


# =========================================================
# 5. DEDUPLICATION
# =========================================================

unique_jobs = {}

for job in raw_jobs:

    job_id = job.get("id")

    if job_id is not None:
        unique_jobs[str(job_id)] = job

jobs = list(unique_jobs.values())

print(f"Unique jobs: {len(jobs)}")


# =========================================================
# 6. NORMALIZE JSON → DATAFRAME
# =========================================================

records = []

for job in jobs:

    records.append({

        "job_id": str(job.get("id")),

        "title": job.get("title"),

        "company": job.get(
            "company", {}
        ).get("display_name"),

        "description": job.get("description"),

        "contract_type": job.get("contract_type"),

        "location": job.get(
            "location", {}
        ).get("display_name"),

        "category": job.get(
            "category", {}
        ).get("label"),

        "created": job.get("created"),

        "salary_min": job.get("salary_min"),

        "salary_max": job.get("salary_max"),

        "salary_is_predicted": job.get(
            "salary_is_predicted"
        ),

        "latitude": job.get("latitude"),

        "longitude": job.get("longitude"),

        "redirect_url": job.get("redirect_url")
    })


df = pd.DataFrame(records)


# =========================================================
# 7. BASIC DATA CLEANING
# =========================================================

text_columns = [
    "title",
    "company",
    "description",
    "contract_type",
    "location",
    "category"
]

for column in text_columns:

    df[column] = (
        df[column]
        .fillna("")
        .astype(str)
        .str.strip()
    )


df["created"] = pd.to_datetime(
    df["created"],
    errors="coerce",
    utc=True
)


df = df.drop_duplicates(
    subset="job_id"
).copy()


# =========================================================
# 8. RELEVANCE FILTERING
# =========================================================

relevance_terms = [
    "data analyst",
    "data analysis",
    "business analyst",
    "business intelligence",
    "reporting analyst",
    "data specialist",
    "power bi",
    "sql",
    "tableau",
    "excel",
    "etl",
    "dashboard",
    "analytics"
]

exclude_terms = [
    "telesales",
    "php developer",
    "nodejs developer",
    "full stack developer",
    "automation tester",
    "maps evaluator",
    "telecaller",
    "sales executive"
]


def calculate_relevance(row):

    title = row["title"].lower()
    description = row["description"].lower()

    score = 0

    for term in relevance_terms:

        if term in title:
            score += 2

        elif term in description:
            score += 1


    for term in exclude_terms:

        if term in title or term in description:
            score -= 3


    return score


df["relevance_score"] = df.apply(
    calculate_relevance,
    axis=1
)


df["relevance_level"] = df[
    "relevance_score"
].apply(
    lambda score:
        "High"
        if score >= 4
        else "Medium"
        if score >= 2
        else "Reject"
)


# Keep only relevant jobs

df = df[
    df["relevance_level"].isin(
        ["High", "Medium"]
    )
].copy()


# =========================================================
# 9. RECENT JOB FILTER
# =========================================================

one_year_ago = (
    datetime.now(timezone.utc)
    - pd.DateOffset(years=1)
)

df = df[
    df["created"] >= one_year_ago
].copy()


print(
    f"Recent relevant jobs: {len(df)}"
)


# =========================================================
# 10. NLP TEXT
# =========================================================

df["nlp_text"] = (
    df["title"].fillna("")
    + " "
    + df["description"].fillna("")
).str.lower()


# =========================================================
# 11. SKILL TAXONOMY
# =========================================================

skill_patterns = {

    "SQL": [
        r"\bsql\b",
        r"\bsql server\b",
        r"\bmysql\b",
        r"\bpostgresql\b",
        r"\bsql database\b",
    ],

    "Python": [
        r"\bpython\b",
        r"python programming",
    ],

    "Excel": [
        r"\bexcel\b",
        r"microsoft excel",
        r"ms excel",
    ],

    "Power BI": [
        r"\bpower bi\b",
        r"\bpowerbi\b",
        r"microsoft power bi",
    ],

    "Tableau": [
        r"\btableau\b",
    ],

    "Power Query": [
        r"\bpower query\b",
    ],

    "Pandas": [
        r"\bpandas\b",
    ],

    "NumPy": [
        r"\bnumpy\b",
    ],

    "ETL": [
        r"\betl\b",
        r"extract.*transform.*load",
    ],

    "Data Analysis": [
        r"\bdata analys\w*\b",
        r"\banaly[sz]e data\b",
        r"\banaly[sz]ing data\b",
    ],

    "Reporting": [
        r"\breporting\b",
        r"\breporting analyst\b",
        r"\breports\b",
    ],

    "Data Engineering": [
        r"\bdata engineering\b",
        r"\bdata engineer\w*\b",
    ],

    "Cloud": [
        r"\bcloud\b",
        r"\bcloud computing\b",
        r"\baws\b",
        r"\bazure\b",
    ],

    "AI / ML": [
        r"\bartificial intelligence\b",
        r"\bmachine learning\b",
        r"\bai/ml\b",
    ],

    "Data Visualization": [
        r"\bdata visualization\b",
        r"\bdata visualisation\b",
        r"\bvisuali[sz]ation\b",
    ]
}


def extract_skills(text):

    found = []

    for skill, patterns in skill_patterns.items():

        for pattern in patterns:

            if re.search(
                pattern,
                text
            ):
                found.append(skill)
                break

    return found


df["skills_found"] = df[
    "nlp_text"
].apply(extract_skills)


# =========================================================
# 12. CREATE SKILL TABLE
# =========================================================

skill_rows = []

for _, row in df.iterrows():

    for skill in row["skills_found"]:

        skill_rows.append({

            "job_id": row["job_id"],

            "title": row["title"],

            "company": row["company"],

            "skill": skill

        })


df_skills = pd.DataFrame(
    skill_rows
)


# =========================================================
# 13. SAVE PROCESSED DATA
# =========================================================

jobs_file = (
    processed_dir
    / "jobs_clean.csv"
)

skills_file = (
    processed_dir
    / "job_skills.csv"
)


df.to_csv(
    jobs_file,
    index=False
)


df_skills.to_csv(
    skills_file,
    index=False
)


# =========================================================
# 14. FINAL SUMMARY
# =========================================================

print("\n" + "=" * 50)
print("PIPELINE COMPLETED")
print("=" * 50)

print(
    f"Raw jobs: {len(raw_jobs)}"
)

print(
    f"Unique jobs: {len(jobs)}"
)

print(
    f"Final relevant jobs: {len(df)}"
)

print(
    f"Skill records: {len(df_skills)}"
)

print(
    f"\nJobs file: {jobs_file}"
)

print(
    f"Skills file: {skills_file}"
)

# Keep the jobs table relational and MySQL-friendly
df_jobs_mysql = df.drop(columns=["skills_found"], errors="ignore")

# ============================================================
# MYSQL LOAD
# ============================================================

from sqlalchemy import create_engine

MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_DATABASE = os.getenv(
    "MYSQL_DATABASE",
    "analytics_project"
)

if not MYSQL_PASSWORD:
    raise ValueError("MYSQL_PASSWORD not found in .env")

# Create MySQL connection
engine = create_engine(
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}",
    pool_pre_ping=True
)

# Remove skills_found because it contains Python lists.
# Skills are stored separately in job_skills table.
df_jobs_mysql = df.drop(
    columns=["skills_found"],
    errors="ignore"
).copy()

# Load main jobs table
df_jobs_mysql.to_sql(
    "jobs_clean",
    con=engine,
    if_exists="replace",
    index=False,
    chunksize=500
)

# Load normalized skills table
df_skills.to_sql(
    "job_skills",
    con=engine,
    if_exists="replace",
    index=False,
    chunksize=500
)

print("MySQL load completed successfully.")
print("Tables loaded: jobs_clean, job_skills")