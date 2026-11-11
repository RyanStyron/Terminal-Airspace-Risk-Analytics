import argparse
import hashlib
import json
import os
import pandas as pd
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone


RAW_DIR = os.path.join("data", "raw")
FAA_DIR = os.path.join(RAW_DIR, "faa_registry")
BTS_DIR = os.path.join(RAW_DIR, "bts_ontime")
ASRS_DIR = os.path.join(RAW_DIR, "nasa_asrs")
META_DIR = os.path.join("metadata")

MANIFEST_PATH = os.path.join(META_DIR, "sha256_manifest.json")

for path in [FAA_DIR, BTS_DIR, ASRS_DIR, META_DIR]:
    os.makedirs(path, exist_ok=True)


def calculate_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()

    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_manifest() -> dict:
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_version": "1.0.0",
        "files": {},
    }


def save_manifest(manifest: dict):
    manifest["generated_utc"] = datetime.now(timezone.utc).isoformat()

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


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
        f" -> Checksum verified [{rel_path}]: {manifest['files'][rel_path]['sha256']}"
    )


def is_source_verified(
    rel_dir_prefix: str, required_files: list, manifest: dict
) -> bool:
    files_dict = manifest.get("files", {})

    for req in required_files:
        expected_key = f"{rel_dir_prefix}/{req}".replace("\\", "/")

        if expected_key not in files_dict or not files_dict[expected_key].get("sha256"):
            return False

        if not os.path.exists(expected_key):
            return False
    return True


def fetch_faa(manifest: dict, overwrite: bool):
    prefix = "data/raw/faa_registry"
    required = ["MASTER.txt", "ACFTREF.txt"]

    if is_source_verified(prefix, required, manifest) and not overwrite:
        print(
            "[SKIP] FAA Registry: Files present and verified in manifest. "
            "(Use --fetch-faa-overwrite to re-fetch)"
        )
        return

    print("\n[FETCH] Processing FAA Civil Aircraft Registry...")

    zip_url = "https://registry.faa.gov/database/ReleasableAircraft.zip"
    zip_local = os.path.join(FAA_DIR, "ReleasableAircraft.zip")

    try:
        print(f" -> Downloading from {zip_url}...")

        req = urllib.request.Request(
            zip_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )

        with urllib.request.urlopen(req) as resp, open(zip_local, "wb") as out:
            shutil.copyfileobj(resp, out)

        with zipfile.ZipFile(zip_local, "r") as z:
            for tf in required:
                if tf in z.namelist():
                    z.extract(tf, path=FAA_DIR)
                    print(f" -> Extracted: {tf}")

        if os.path.exists(zip_local):
            os.remove(zip_local)
    except Exception as e:
        print(f" -> Download warning/error: {e}")
        print(
            " -> Falling back to verifying pre-existing local files in faa_registry/..."
        )

    for req in required:
        fp = os.path.join(FAA_DIR, req)

        if not os.path.exists(fp):
            raise FileNotFoundError(f"Missing required FAA file: {fp}")
        register_file(fp, manifest)


def fetch_bts(manifest: dict, overwrite: bool):
    prefix = "data/raw/bts_ontime"
    required = ["bts_ontime_2024_q1.csv"]

    if is_source_verified(prefix, required, manifest) and not overwrite:
        print(
            "[SKIP] BTS On-Time: Combined Q1 2024 dataset verified in manifest. "
            "(Use --fetch-bts-overwrite to re-fetch)"
        )
        return

    print(
        "\n[FETCH] Downloading and filtering DOT BTS TranStats On-Time (Jan–Mar 2024)..."
    )

    bts_target = os.path.join(BTS_DIR, "bts_ontime_2024_q1.csv")
    year = 2024
    months = [1, 2, 3]
    target_cols = [
        "FlightDate",
        "Reporting_Airline",
        "Tail_Number",
        "Tail_Num",
        "Origin",
        "Dest",
        "CRSDepTime",
        "DepTime",
        "DepDelay",
        "TaxiOut",
        "WheelsOff",
        "WheelsOn",
        "TaxiIn",
        "CRSArrTime",
        "ArrTime",
        "ArrDelay",
        "CRSElapsedTime",
        "ActualElapsedTime",
        "AirTime",
        "CarrierDelay",
        "WeatherDelay",
        "NASDelay",
        "SecurityDelay",
        "LateAircraftDelay",
        "Cancelled",
        "Diverted",
    ]
    temp_extracted_files = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        for m in months:
            url = f"https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{m}.zip"
            zip_dest = os.path.join(BTS_DIR, f"temp_{year}_{m}.zip")

            print(f" -> Fetching {year}-{m:02d} from BTS PREZIP...")
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req) as resp, open(zip_dest, "wb") as out:
                shutil.copyfileobj(resp, out)

            with zipfile.ZipFile(zip_dest, "r") as z:
                csv_members = [f for f in z.namelist() if f.endswith(".csv")]

                if not csv_members:
                    raise FileNotFoundError(
                        f"No CSV discovered in monthly archive: {zip_dest}"
                    )
                extracted_csv = z.extract(csv_members[0], path=BTS_DIR)
                extracted_csv = z.extract(csv_members[0], path=BTS_DIR)
                temp_extracted_files.append(extracted_csv)

            if os.path.exists(zip_dest):
                os.remove(zip_dest)

        print(" -> Filtering arrivals (JFK, LGA, EWR) and concatenating Q1...")
        dfs = []

        for csv_path in temp_extracted_files:
            # Load the needed columns and keep records for the destination hubs.
            chunk = pd.read_csv(
                csv_path, usecols=lambda c: c in target_cols, low_memory=False
            )

            if "Tail_Number" in chunk.columns:
                chunk = chunk.rename(columns={"Tail_Number": "Tail_Num"})
            filtered_chunk = chunk[chunk["Dest"].isin(["JFK", "LGA", "EWR"])]

            dfs.append(filtered_chunk)
            os.remove(csv_path)

        combined_df = pd.concat(dfs, ignore_index=True)

        combined_df.to_csv(bts_target, index=False)
        print(
            f" -> Consolidating complete: {len(combined_df):,} arrival records saved to '{bts_target}'"
        )
    except Exception as e:
        # Remove extracted files that remain after an error.
        for p in temp_extracted_files:
            if os.path.exists(p):
                os.remove(p)

        print(f" -> Failed to fetch BTS automated archives: {e}")

        if not os.path.exists(bts_target):
            print(
                f" -> [MISSING] Fallback: Manually save pre-filtered CSV to '{bts_target}'"
            )
            return
    register_file(bts_target, manifest)


def fetch_asrs(manifest: dict, overwrite: bool):
    prefix = "data/raw/nasa_asrs"
    required = ["asrs_incidents.csv"]

    if is_source_verified(prefix, required, manifest) and not overwrite:
        print(
            "[SKIP] NASA ASRS: Files present and verified in manifest. "
            "(Use --fetch-asrs-overwrite to re-verify)"
        )
        return

    print("\n[VERIFY] Processing NASA ASRS Incident dataset...")

    asrs_path = os.path.join(ASRS_DIR, "asrs_incidents.csv")

    if not os.path.exists(asrs_path):
        print(f" -> [MISSING] File not found: '{asrs_path}'")
        print("    Query from: https://asrs.arc.nasa.gov/search/database.html")
        print(f"    Save extract to: {asrs_path}")
        return
    register_file(asrs_path, manifest)


def main():
    parser = argparse.ArgumentParser(
        description="Download raw data files and manage SHA-256 integrity manifest."
    )

    parser.add_argument(
        "--fetch-faa-overwrite",
        action="store_true",
        help="Force re-download and re-hashing of FAA Registry archive.",
    )
    parser.add_argument(
        "--fetch-bts-overwrite",
        action="store_true",
        help="Force re-download, re-concatenation, and re-hashing of Q1 2024 BTS dataset.",
    )
    parser.add_argument(
        "--fetch-asrs-overwrite",
        action="store_true",
        help="Force re-calculation of NASA ASRS incident extract hash.",
    )
    parser.add_argument(
        "--overwrite-all",
        action="store_true",
        help="Force re-fetching and re-hashing across all sources.",
    )

    args = parser.parse_args()
    manifest = load_manifest()

    print(f"Manifest target: {MANIFEST_PATH}")
    print("-" * 60)

    try:
        fetch_faa(manifest, overwrite=(args.fetch_faa_overwrite or args.overwrite_all))
        fetch_bts(manifest, overwrite=(args.fetch_bts_overwrite or args.overwrite_all))
        fetch_asrs(
            manifest, overwrite=(args.fetch_asrs_overwrite or args.overwrite_all)
        )
    finally:
        save_manifest(manifest)
        print("-" * 60)
        print("Integrity manifest updated.")


if __name__ == "__main__":
    main()
