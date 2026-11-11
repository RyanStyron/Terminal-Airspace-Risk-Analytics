import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

import duckdb
import pandas as pd


RAW_DIR = os.path.join("data", "raw")
FAA_DIR = os.path.join(RAW_DIR, "faa_registry")
BTS_DIR = os.path.join(RAW_DIR, "bts_ontime")
ASRS_DIR = os.path.join(RAW_DIR, "nasa_asrs")

MANIFEST_PATH = os.path.join("metadata", "sha256_manifest.json")
CURATED_DB_DIR = os.path.join("data", "curated", "duckdb")
DB_PATH = os.path.join(CURATED_DB_DIR, "aviation_safety.duckdb")

EXPECTED_FILES = {
    "faa_master": os.path.join(FAA_DIR, "MASTER.txt"),
    "faa_acftref": os.path.join(FAA_DIR, "ACFTREF.txt"),
    "bts_flights": os.path.join(BTS_DIR, "bts_ontime_2024_q1.csv"),
    "asrs_incidents": os.path.join(ASRS_DIR, "asrs_incidents.csv"),
}


def calculate_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()

    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_manifest_integrity():
    if not os.path.exists(MANIFEST_PATH):
        raise FileNotFoundError(
            f"Manifest file not found: {MANIFEST_PATH}. "
            "Execute fetch and verification steps first."
        )

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest_files = manifest.get("files", {})

    print("Verifying raw file integrity against metadata/sha256_manifest.json...")

    for key, path in EXPECTED_FILES.items():
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing expected input file: {path}")
        rel_key = os.path.relpath(path, ".").replace("\\", "/")

        if rel_key not in manifest_files:
            raise KeyError(f"File {rel_key} is not registered in {MANIFEST_PATH}")
        expected_hash = manifest_files[rel_key].get("sha256")
        actual_hash = calculate_sha256(path)

        if actual_hash.lower() != expected_hash.lower():
            raise ValueError(
                f"Checksum mismatch for {rel_key}!\n"
                f"  Expected: {expected_hash}\n"
                f"  Actual:   {actual_hash}"
            )
        print(f"  [OK] {rel_key} (SHA-256 verified)")

    print("Integrity verification passed.\n")


def transform_faa_registry(con: duckdb.DuckDBPyConnection):
    print("Transforming FAA Civil Aircraft Registry...")
    master_path = EXPECTED_FILES["faa_master"].replace("\\", "/")
    acftref_path = EXPECTED_FILES["faa_acftref"].replace("\\", "/")

    # Select the FAA aircraft-reference columns needed for the dimension table.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE dim_faa_acftref AS
        SELECT 
            TRIM(CODE) AS mfr_mdl_code,
            TRIM(MFR) AS manufacturer,
            TRIM(MODEL) AS model_name,
            TRY_CAST("TYPE-ACFT" AS INTEGER) AS type_acft_code,
            TRY_CAST("NO-ENG" AS INTEGER) AS num_engines,
            TRY_CAST("AC-WEIGHT" AS VARCHAR) AS weight_code
        FROM read_csv(
            '{acftref_path}',
            header=True,
            delim=',',
            quote='"',
            all_varchar=True,
            ignore_errors=True
        );
    """
    )

    # Map aircraft records to standard wake turbulence classes.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE dim_aircraft_registry AS
        SELECT 
            TRIM(m."N-NUMBER") AS n_number,
            TRIM(m."MFR MDL CODE") AS mfr_mdl_code,
            r.manufacturer,
            r.model_name,
            r.num_engines,
            CASE 
                -- Classify the A380 as a Super.
                WHEN r.model_name LIKE '%A380%' THEN 'Super'
                
                -- Classify common widebody aircraft as Heavy.
                WHEN r.model_name LIKE '%767%' 
                  OR r.model_name LIKE '%777%' 
                  OR r.model_name LIKE '%787%' 
                  OR r.model_name LIKE '%747%' 
                  OR r.model_name LIKE '%A330%' 
                  OR r.model_name LIKE '%A340%' 
                  OR r.model_name LIKE '%A350%' 
                  OR r.model_name LIKE '%MD-11%' 
                  OR r.model_name LIKE '%DC-10%' THEN 'Heavy'
                
                -- Classify light aircraft as Small.
                WHEN TRIM(r.weight_code) = 'CLASS 1' THEN 'Small'
                
                -- Treat remaining aircraft as Large.
                ELSE 'Large'
            END AS wake_turbulence_class
        FROM read_csv(
            '{master_path}',
            header=True,
            delim=',',
            quote='"',
            all_varchar=True,
            ignore_errors=True
        ) m
        LEFT JOIN dim_faa_acftref r
            ON TRIM(m."MFR MDL CODE") = r.mfr_mdl_code;
    """
    )

    cnt = con.execute("SELECT COUNT(*) FROM dim_aircraft_registry;").fetchone()[0]

    print(f"  -> dim_aircraft_registry created: {cnt:,} records.")


def transform_bts_operations(con: duckdb.DuckDBPyConnection):
    print("Transforming DOT BTS Flight Operations (Jan–Mar 2024)...")
    bts_path = EXPECTED_FILES["bts_flights"].replace("\\", "/")

    con.execute(
        f"""
        CREATE OR REPLACE TABLE fct_terminal_arrivals AS
        SELECT 
            TRY_CAST(b.FlightDate AS DATE) AS flight_date,
            TRIM(b.Reporting_Airline) AS carrier,
            TRIM(b.Tail_Num) AS tail_num,
            TRIM(b.Origin) AS origin_airport,
            TRIM(b.Dest) AS dest_airport,
            TRY_CAST(b.CRSDepTime AS INTEGER) AS crs_dep_time,
            TRY_CAST(b.DepTime AS INTEGER) AS actual_dep_time,
            TRY_CAST(b.DepDelay AS DOUBLE) AS dep_delay_min,
            TRY_CAST(b.TaxiOut AS DOUBLE) AS taxi_out_min,
            TRY_CAST(b.WheelsOff AS INTEGER) AS wheels_off,
            TRY_CAST(b.WheelsOn AS INTEGER) AS wheels_on,
            TRY_CAST(b.TaxiIn AS DOUBLE) AS taxi_in_min,
            TRY_CAST(b.CRSArrTime AS INTEGER) AS crs_arr_time,
            TRY_CAST(b.ArrTime AS INTEGER) AS actual_arr_time,
            TRY_CAST(b.ArrDelay AS DOUBLE) AS arr_delay_min,
            TRY_CAST(b.CRSElapsedTime AS DOUBLE) AS crs_elapsed_time_min,
            TRY_CAST(b.ActualElapsedTime AS DOUBLE) AS actual_elapsed_time_min,
            TRY_CAST(b.AirTime AS DOUBLE) AS air_time_min,
            -- Calculate the sequencing delay buffer used by the regression model.
            (TRY_CAST(b.ActualElapsedTime AS DOUBLE) - TRY_CAST(b.CRSElapsedTime AS DOUBLE)) AS buffer_delay_min,
            COALESCE(TRY_CAST(b.CarrierDelay AS DOUBLE), 0.0) AS delay_due_carrier,
            COALESCE(TRY_CAST(b.WeatherDelay AS DOUBLE), 0.0) AS delay_due_weather,
            COALESCE(TRY_CAST(b.NASDelay AS DOUBLE), 0.0) AS delay_due_nas,
            COALESCE(TRY_CAST(b.SecurityDelay AS DOUBLE), 0.0) AS delay_due_security,
            COALESCE(TRY_CAST(b.LateAircraftDelay AS DOUBLE), 0.0) AS delay_due_late_aircraft,
            COALESCE(r.wake_turbulence_class, 'Large') AS wake_turbulence_class
        FROM read_csv_auto('{bts_path}', ignore_errors=True) b
        LEFT JOIN dim_aircraft_registry r
            ON REGEXP_REPLACE(TRIM(b.Tail_Num), '^[Nn]', '') = r.n_number
        WHERE b.Cancelled = 0 
          AND b.Diverted = 0
          AND b.Dest IN ('JFK', 'LGA', 'EWR')
          AND b.ActualElapsedTime IS NOT NULL 
          AND b.CRSElapsedTime IS NOT NULL;
    """
    )
    cnt = con.execute("SELECT COUNT(*) FROM fct_terminal_arrivals;").fetchone()[0]

    print(f"  -> fct_terminal_arrivals created: {cnt:,} arrival records.")


def transform_asrs_incidents(con: duckdb.DuckDBPyConnection):
    print("Transforming NASA ASRS Incident Reports...")
    asrs_path = EXPECTED_FILES["asrs_incidents"]
    # Read the two-level report header.
    df = pd.read_csv(asrs_path, header=[0, 1], low_memory=False)

    # Find columns by matching their hierarchical header labels.
    def get_col(parent_pattern, child_pattern):
        matches = [
            (p, c)
            for p, c in df.columns
            if parent_pattern.lower() in str(p).lower()
            and child_pattern.lower() in str(c).lower()
        ]

        return matches[0] if matches else None

    acn_col = get_col("", "acn") or get_col("unnamed", "acn")
    date_col = get_col("time", "date")
    tod_col = get_col("time", "local time of day")
    cond_col = get_col("environment", "flight conditions")
    vis_col = get_col("environment", "weather elements")
    light_col = get_col("environment", "light")
    acft1_model_col = get_col("aircraft 1", "make model name")
    acft1_phase_col = get_col("aircraft 1", "flight phase")
    acft1_far_col = get_col("aircraft 1", "operating under far part")
    acft2_model_col = get_col("aircraft 2", "make model name")
    anomaly_col = get_col("events", "anomaly")
    result_col = get_col("events", "result")
    problem_col = get_col("assessments", "primary problem")

    clean_df = pd.DataFrame()
    clean_df["acn_id"] = df[acn_col].astype("int64") if acn_col else None
    clean_df["year_month"] = df[date_col].astype("str") if date_col else None

    clean_df["time_of_day"] = (
        df[tod_col].fillna("Unknown").str.strip() if tod_col else "Unknown"
    )
    clean_df["flight_conditions"] = (
        df[cond_col].fillna("Unknown").str.strip() if cond_col else "Unknown"
    )
    clean_df["light_condition"] = (
        df[light_col].fillna("Unknown").str.strip() if light_col else "Unknown"
    )
    # Preserve the primary aircraft attributes.
    clean_df["acft1_model"] = (
        df[acft1_model_col].fillna("Unknown").str.strip()
        if acft1_model_col
        else "Unknown"
    )
    clean_df["flight_phase"] = (
        df[acft1_phase_col].fillna("Unknown").str.strip()
        if acft1_phase_col
        else "Unknown"
    )
    clean_df["operating_far_part"] = (
        df[acft1_far_col].fillna("Unknown").str.strip() if acft1_far_col else "Unknown"
    )
    # Flag reports that include a second aircraft.
    if acft2_model_col:
        clean_df["is_multi_aircraft"] = df[acft2_model_col].notna().astype(int)
    else:
        clean_df["is_multi_aircraft"] = 0

    clean_df["primary_problem"] = (
        df[problem_col].fillna("Unknown").str.strip() if problem_col else "Unknown"
    )
    # Encode the incident outcomes used by downstream analysis.
    anomaly_series = (
        df[anomaly_col].fillna("") if anomaly_col else pd.Series([""] * len(df))
    )
    clean_df["is_severe_anomaly"] = anomaly_series.apply(
        lambda x: (
            1
            if any(
                t in x
                for t in [
                    "Conflict NMAC",
                    "Wake Vortex Encounter",
                    "Loss Of Aircraft Control",
                    "CFTT / CFIT",
                ]
            )
            else 0
        )
    )

    result_series = (
        df[result_col].fillna("") if result_col else pd.Series([""] * len(df))
    )
    clean_df["took_evasive_action"] = result_series.apply(
        lambda x: 1 if "Flight Crew Took Evasive Action" in x else 0
    )

    con.execute("CREATE OR REPLACE TABLE fct_asrs_incidents AS SELECT * FROM clean_df;")
    cnt = con.execute("SELECT COUNT(*) FROM fct_asrs_incidents;").fetchone()[0]

    print(f"  -> Curated fct_asrs_incidents created: {cnt:,} incident records.")


def register_file(filepath: str, manifest: dict):
    rel_path = os.path.relpath(filepath, ".").replace("\\", "/")

    manifest["files"][rel_path] = {
        "sha256": calculate_sha256(filepath),
        "bytes": os.path.getsize(filepath),
        "last_modified_utc": datetime.fromtimestamp(
            os.path.getmtime(filepath), timezone.utc
        ).isoformat(),
    }

    print(
        f"  -> Checksum registered [{rel_path}]: {manifest['files'][rel_path]['sha256']}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Verify SHA-256 digests and transform raw aviation data into curated DuckDB tables."
    )

    parser.add_argument(
        "--skip-checksum",
        action="store_true",
        help="Bypass manifest SHA-256 validation (not recommended for production/grading).",
    )
    args = parser.parse_args()

    os.makedirs(CURATED_DB_DIR, exist_ok=True)

    print("=" * 70)
    print("Aviation Airspace Analytics: Transformation & Curation Pipeline")
    print("=" * 70)

    if not args.skip_checksum:
        validate_manifest_integrity()
    else:
        print("[WARN] Skipping SHA-256 manifest verification.\n")
    con = duckdb.connect(database=DB_PATH, read_only=False)

    try:
        transform_faa_registry(con)
        transform_bts_operations(con)
        transform_asrs_incidents(con)

        print("\n" + "-" * 70)
        print("Curated Database Summary:")
        for tbl in [
            "dim_faa_acftref",
            "dim_aircraft_registry",
            "fct_terminal_arrivals",
            "fct_asrs_incidents",
        ]:
            col_count = len(con.execute(f"PRAGMA table_info('{tbl}');").fetchall())
            row_count = con.execute(f"SELECT COUNT(*) FROM {tbl};").fetchone()[0]

            print(f"  * {tbl:25} | {row_count:>10,} rows | {col_count:>2} columns")
        print("-" * 70)
    finally:
        # Close the connection before hashing the database.
        con.close()

    # Register the closed curated database in the manifest.
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print("Registering curated database digest into metadata/sha256_manifest.json...")
    register_file(DB_PATH, manifest)

    manifest["generated_utc"] = datetime.now(timezone.utc).isoformat()

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Curated database written and sealed: {DB_PATH}")


if __name__ == "__main__":
    main()
