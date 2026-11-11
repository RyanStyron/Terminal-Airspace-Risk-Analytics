# Terminal Airspace Risk Analytics

Data collection and analytical pipeline analyzing terminal airspace flight anomaly severity and sequencing delay buffers across major New York metropolitan hubs (JFK, LGA, EWR).

## Project Structure

```text
├── data/
│   ├── raw/                     # Raw datasets (gitignored; verified via manifest)
│   │   ├── faa_registry/        # FAA Civil Aircraft Registry (MASTER.txt, ACFTREF.txt)
│   │   ├── bts_ontime/          # DOT BTS On-Time Performance (bts_ontime_2024_q1.csv)
│   │   └── nasa_asrs/           # NASA ASRS Terminal Incident Reports (asrs_incidents.csv)
│   └── processed/               # Local DuckDB database and curated tables
├── metadata/
│   └── sha256_manifest.json     # Cryptographic SHA-256 verification manifest
├── scripts/
│   └── fetch_and_verify.py      # Automated data retrieval and manifest verification
├── environment.yml              # Conda environment specification
└── README.md
```

## Setup & Environment

Create and activate the isolated Conda environment:

```bash
conda env create -f environment.yml
conda activate aero-risk-analytics
```

## Data Retrieval & Integrity Verification

The repository manages raw public records across three primary sources with SHA-256 hash validation:

1. **FAA Civil Aircraft Registry**: Automated download and extraction of `MASTER.txt` and `ACFTREF.txt`.
2. **DOT BTS TranStats (Q1 2024)**: Automated monthly download (Jan–Mar 2024), filtering for JFK, LGA, and EWR arrivals, and concatenation into `bts_ontime_2024_q1.csv`.
3. **NASA ASRS Incident Reports**: Query export placed in `data/raw/nasa_asrs/asrs_incidents.csv` covering terminal flight phases (Descent, Approach, Final Approach, Landing).

### Running Verification

To download automated archives, inspect local source presence, and update the integrity manifest:

```bash
python scripts/ingest/fetch_and_verify.py
```

If valid checksums are already recorded in `metadata/sha256_manifest.json` and files exist locally, the script skips redundant downloads. 

To force a re-fetch or re-hash of specific sources:

```bash
# Force re-download of FAA registry
python scripts/fetch_and_verify.py --fetch-faa-overwrite

# Force re-fetch and concatenation of BTS Q1 2024 records
python scripts/fetch_and_verify.py --fetch-bts-overwrite

# Force full re-verification of all sources (except NASA ASRS, which is manually retrieved)
python scripts/fetch_and_verify.py --overwrite-all
```