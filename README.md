# Terminal Airspace Risk Analytics

Data collection and analytical pipeline analyzing terminal airspace flight anomaly severity and sequencing delay buffers across major New York metropolitan hubs (JFK, LGA, EWR).

## Project Structure

```text
├── data/
│   ├── raw/                     # Raw datasets (gitignored; verified via manifest)
│   │   ├── faa_registry/        # FAA Civil Aircraft Registry (MASTER.txt, ACFTREF.txt)
│   │   ├── bts_ontime/          # DOT BTS On-Time Performance (bts_ontime_2024_q1.csv)
│   │   └── nasa_asrs/           # NASA ASRS Terminal Incident Reports (asrs_incidents.csv)
│   └── curated/                 # Local DuckDB database and curated tables
├── metadata/
│   └── sha256_manifest.json     # Cryptographic SHA-256 verification manifest
├── scripts/
│   ├── ingest/                  # Automated data retrieval and manifest verification
│   └── transform/               # Data transformation and cleaning scripts     
├── environment.yml              # Conda environment specification
├── LICENSE.md
├── .gitignore
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

### FAA Civil Aircraft Registry Parsing (`MASTER.txt`, `ACFTREF.txt`)
* **Encoding & BOM Cleansing:** Raw FAA registry files contain UTF-8 byte-order marks (`\ufeff`) on leading key fields (`CODE`, `N-NUMBER`). The pipeline strips leading BOM markers and whitespace before establishing foreign key indexes.
* **Wake Turbulence Classification:** The standard FAA reference file assigns `CLASS 3` (aircraft $\ge 20,000\text{ lbs}$) to all commercial transport aircraft. Relying purely on numerical weight classes caused standard narrowbodies (A320, B737) and regional jets (CRJ-900, E175) to be misclassified as Heavy. To adhere to FAA ATC Order JO 7110.65 wake vortex separation criteria, the pipeline implements model prefix pattern-matching:
  * **Super:** Airbus A380 (`A380`).
  * **Heavy** ($\text{MTOW} > 300,000\text{ lbs}$): True widebody airframes (`767`, `777`, `787`, `747`, `A330`, `A340`, `A350`, `MD-11`, `DC-10`).
  * **Small** ($\text{MTOW} \le 41,000\text{ lbs}$): FAA `CLASS 1` general aviation.
  * **Large** ($41,000\text{ lbs} < \text{MTOW} \le 300,000\text{ lbs}$): Standard commercial narrowbodies and regional jets.
  * *Empirical Validation:* This correctly identifies that LaGuardia (LGA) handles **0.00% Heavy** traffic due to runway length and structural limits, while JFK (**4.46%**) and EWR (**3.50%**) accommodate domestic widebody trunk routes.

### DOT BTS TranStats Integration & Relational Join (`fct_terminal_arrivals`)
* **Key Normalization:** TranStats records aircraft registrations with a standard leading `'N'` (e.g., `N485PX`), whereas the FAA master registry stores raw alphanumeric strings (`485PX`). The pipeline applies regular expression normalization:
  $$\text{CleanNNumber} = \text{REGEXP\_REPLACE}(\text{TRIM}(\text{Tail\_Num}), \text{'}\hat{\phantom{a}}[\text{Nn}]\text{'}, \text{''})$$
  This achieved a **96.49% foreign key match rate** (92,428 out of 95,788 arrival flights linked to active FAA airframe specifications).
* **Target Metric Formulation ($\Delta t$):** The primary continuous metric for terminal airspace sequencing buffer delay is computed at the row level:
  $$\Delta t = \text{ActualElapsedTime} - \text{CRSElapsedTime}$$
  Capturing airborne vectoring, terminal holding, speed restrictions, and surface taxi-in friction.

### NASA ASRS Hierarchical Flattening (`fct_asrs_incidents`)
* **Two-Level Header Extraction:** NASA ASRS data exports use a two-row multi-index header block separating top-level operational categories (`Time`, `Place`, `Environment`, `Aircraft 1`, `Events`, `Assessments`) from specific sub-fields (`Flight Phase`, `Anomaly`, `Primary Problem`).
* **Column Normalization & Indicator Encoding:** A tuple-matching parser flattens the multi-index structure into relational columns and extracts key analytical flags:
  * `is_multi_aircraft`: Evaluates `Aircraft 2` presence to isolate air-to-air traffic proximity conflicts/NMACs (present in 73.5% of incidents).
  * `is_severe_anomaly`: Flags Near Mid-Air Collisions (`Conflict NMAC`), `Wake Vortex Encounter`, `Loss Of Aircraft Control`, and `CFTT / CFIT`.
  * `took_evasive_action`: Flags instances where flight crews initiated evasive breakout maneuvers.

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

## Running the Transformation Pipeline

Once raw data assets are ingested and verified, run `scripts/transform/transform_pipeline.py` to execute all relational joins, feature encodings, table DDLs, and database sealing:

```bash
# Verify raw checksums, transform tables, and seal the curated DuckDB digest
python scripts/transform/transform_pipeline.py
```

## Curated Database Schema Summary

The resulting DuckDB database file (`data/curated/duckdb/aviation_safety.duckdb`) maintains four relational tables:

| Table | Entity Type | Records | Columns | Source Provenance |
|---|---|---:|---:|---|
| **`dim_faa_acftref`** | Dimension | 75,526 | 6 | `data/raw/faa_registry/ACFTREF.txt` |
| **`dim_aircraft_registry`** | Dimension | 318,990 | 6 | `data/raw/faa_registry/MASTER.txt`<br>`data/raw/faa_registry/ACFTREF.txt` |
| **`fct_terminal_arrivals`** | Fact | 95,788 | 25 | `data/raw/bts_ontime/bts_ontime_2024_q1.csv`<br>`data/raw/faa_registry/MASTER.txt`<br>`data/raw/faa_registry/ACFTREF.txt` |
| **`fct_asrs_incidents`** | Fact | 2,392 | 10 | `data/raw/nasa_asrs/asrs_incidents.csv` |

### Database Schema Manifest (`metadata/database_schema_manifest.json`)

To ensure end-to-end data provenance, table-level lineage, and explicit data dictionary governance, the curated database structure is formally tracked in `metadata/database_schema_manifest.json`.

The manifest explicitly documents:
* **Table Metadata**: Entity type (dimension vs. fact), record counts, column counts, primary keys, and source file lineage paths.
* **Foreign Key Bindings**: Explicit join definitions and match conditions (e.g., regex-cleaned `tail_num` referencing `dim_aircraft_registry.n_number`).
* **Column Dictionaries**: Field names, native DuckDB data types (`VARCHAR`, `INTEGER`, `DOUBLE`, `DATE`), nullability constraints, and functional descriptions.
* **Analytical Target Formulations**: Mathematical definitions for downstream modeling targets, including the sequencing buffer delay metric $\Delta t = \text{ActualElapsedTime} - \text{CRSElapsedTime}$ and safety incident severity flags (`is_severe_anomaly`, `took_evasive_action`).

The schema manifest is cryptographically hashed and sealed inside `metadata/sha256_manifest.json` to guarantee reproducible schema validation.