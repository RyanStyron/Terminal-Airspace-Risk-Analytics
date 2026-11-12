import os
import duckdb
import numpy as np
import pandas as pd
import scipy.stats as stats
import matplotlib.pyplot as plt
import seaborn as sns

DB_PATH = os.path.join("data", "curated", "duckdb", "aviation_safety.duckdb")
TABLES_DIR = os.path.join("reports", "tables")
FIGURES_DIR = os.path.join("reports", "figures")

for d in [TABLES_DIR, FIGURES_DIR]:
    os.makedirs(d, exist_ok=True)

# Clean academic styling
sns.set_theme(style="whitegrid", font="sans-serif")
plt.rcParams.update({
    "font.size": 11,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "figure.titlesize": 14,
    "figure.dpi": 300
})


def run_h1_asrs_eda(con: duckdb.DuckDBPyConnection):
    """
    Hypothesis 1 EDA: Flight anomaly severity and evasive maneuvers across
    meteorology (VMC/IMC), operational flight phases, FAR parts, and traffic encounters.
    """
    print("\n" + "=" * 82)
    print("HYPOTHESIS 1 EDA: NASA ASRS TERMINAL INCIDENTS (fct_asrs_incidents)")
    print("=" * 82)

    df = con.execute("""
        SELECT
            acn_id,
            flight_conditions,
            light_condition,
            flight_phase,
            operating_far_part,
            is_multi_aircraft,
            primary_problem,
            is_severe_anomaly,
            took_evasive_action
        FROM fct_asrs_incidents;
    """).df()

    # 1. Overall Target Proportions & Class Balance
    n_total = len(df)
    n_severe = df["is_severe_anomaly"].sum()
    n_evasive = df["took_evasive_action"].sum()
    print(f"\n[H1.1] Sample Size & Target Class Balance (N = {n_total:,}):")
    print(f"  - Severe Anomalies (is_severe_anomaly = 1):   {n_severe:,} ({n_severe/n_total*100:.2f}%)")
    print(f"  - Evasive Maneuvers (took_evasive_action = 1): {n_evasive:,} ({n_evasive/n_total*100:.2f}%)")
    print(f"  - Multi-Aircraft Conflicts (Aircraft 2 != Null): {df['is_multi_aircraft'].sum():,} ({df['is_multi_aircraft'].mean()*100:.2f}%)")

    # 2. Meteorological Regime Contingency & Chi-Square Test
    df_wx = df[df["flight_conditions"].isin(["VMC", "IMC"])].copy()
    ct_wx_sev = pd.crosstab(df_wx["flight_conditions"], df_wx["is_severe_anomaly"])
    chi2_wx, p_wx, dof_wx, _ = stats.chi2_contingency(ct_wx_sev)
    wx_summary = df.groupby("flight_conditions").agg(
        total_reports=("acn_id", "count"),
        severe_count=("is_severe_anomaly", "sum"),
        severe_rate_pct=("is_severe_anomaly", lambda s: round(s.mean() * 100, 2)),
        evasive_count=("took_evasive_action", "sum"),
        evasive_rate_pct=("took_evasive_action", lambda s: round(s.mean() * 100, 2))
    ).reset_index().sort_values(by="total_reports", ascending=False)

    print("\n[H1.2] Meteorological Conditions vs. Severity & Evasive Rates:")
    print(wx_summary.to_string(index=False))
    print(f"  -> Chi-Square Test (VMC vs IMC on Severity): chi2 = {chi2_wx:.4f}, p = {p_wx:.4e} (df = {dof_wx})")
    wx_summary.to_csv(os.path.join(TABLES_DIR, "h1_meteorology_summary.csv"), index=False)

    # 3. Flight Phase x FAR Part Interaction (Part 91 vs Part 121)
    terminal_phases = ["Initial Approach", "Final Approach", "Descent", "Landing"]
    df_term = df[
        df["flight_phase"].isin(terminal_phases) &
        df["operating_far_part"].isin(["Part 91", "Part 121"])
    ].copy()

    # Chi-Square test on FAR Part vs Evasive Action in Terminal Phases
    ct_far_ev = pd.crosstab(df_term["operating_far_part"], df_term["took_evasive_action"])
    chi2_far, p_far, dof_far, _ = stats.chi2_contingency(ct_far_ev)
    phase_far_summary = df_term.groupby(["flight_phase", "operating_far_part"]).agg(
        incidents=("acn_id", "count"),
        multi_acft_pct=("is_multi_aircraft", lambda s: round(s.mean() * 100, 1)),
        severe_rate_pct=("is_severe_anomaly", lambda s: round(s.mean() * 100, 1)),
        evasive_rate_pct=("took_evasive_action", lambda s: round(s.mean() * 100, 1))
    ).reset_index()

    # Order phases chronologically along approach profile
    phase_order = ["Descent", "Initial Approach", "Final Approach", "Landing"]
    phase_far_summary["flight_phase"] = pd.Categorical(phase_far_summary["flight_phase"], categories=phase_order, ordered=True)
    phase_far_summary = phase_far_summary.sort_values(["flight_phase", "operating_far_part"])

    print("\n[H1.3] Terminal Flight Phase x Regulatory Regime (Part 91 vs Part 121):")
    print(phase_far_summary.to_string(index=False))
    print(f"  -> Chi-Square Test (Part 91 vs Part 121 on Evasive Action): chi2 = {chi2_far:.4f}, p = {p_far:.4e} (df = {dof_far})")
    phase_far_summary.to_csv(os.path.join(TABLES_DIR, "h1_phase_far_interaction.csv"), index=False)

    # 4. Figure 1: Two-Panel ASRS Risk & Behavioral Profile
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))

    # Panel A: Severe Anomaly Rate across Terminal Phases by FAR Part
    sns.barplot(
        data=phase_far_summary,
        x="flight_phase",
        y="severe_rate_pct",
        hue="operating_far_part",
        palette={"Part 91": "#d95f02", "Part 121": "#1b9e77"},
        ax=axes[0]
    )
    axes[0].set_title("(A) Severe Anomaly Rate by Flight Phase & FAR Regulation")
    axes[0].set_xlabel("Terminal Flight Phase")
    axes[0].set_ylabel("Severe Anomaly Rate (%)")
    axes[0].set_ylim(0, 110)
    axes[0].legend(title="Regulation", frameon=True, loc="upper right")
    for container in axes[0].containers:
        axes[0].bar_label(container, fmt="%.1f%%", fontsize=8.5, padding=2)

    # Panel B: Evasive Maneuver Rate Disparity (Part 91 vs Part 121)
    sns.barplot(
        data=phase_far_summary,
        x="flight_phase",
        y="evasive_rate_pct",
        hue="operating_far_part",
        palette={"Part 91": "#d95f02", "Part 121": "#1b9e77"},
        ax=axes[1]
    )
    axes[1].set_title("(B) Evasive Breakout Rate: Visual (Part 91) vs. IFR SOP (Part 121)")
    axes[1].set_xlabel("Terminal Flight Phase")
    axes[1].set_ylabel("Evasive Action Rate (%)")
    axes[1].set_ylim(0, 110)
    axes[1].legend(title="Regulation", frameon=True, loc="upper right")
    for container in axes[1].containers:
        axes[1].bar_label(container, fmt="%.1f%%", fontsize=8.5, padding=2)

    plt.tight_layout()
    fig1_path = os.path.join(FIGURES_DIR, "fig1_asrs_phase_far_profiles.png")
    plt.savefig(fig1_path)
    plt.close()
    print(f"  -> Saved Figure 1: {fig1_path}")


def run_h2_bts_eda(con: duckdb.DuckDBPyConnection):
    """
    Hypothesis 2 EDA: Continuous predictive features, outlier screening, log transformations,
    non-parametric hub comparisons (Kruskal-Wallis), and wake turbulence separation (Mann-Whitney U).
    """
    print("\n" + "=" * 82)
    print("HYPOTHESIS 2 EDA: DOT BTS & FAA REGISTRY (fct_terminal_arrivals)")
    print("=" * 82)

    df = con.execute("""
        SELECT
            dest_airport,
            carrier,
            wake_turbulence_class,
            dep_delay_min,
            taxi_out_min,
            air_time_min,
            taxi_in_min,
            arr_delay_min,
            crs_elapsed_time_min,
            actual_elapsed_time_min,
            buffer_delay_min,
            delay_due_nas,
            delay_due_weather,
            delay_due_carrier,
            delay_due_late_aircraft
        FROM fct_terminal_arrivals;
    """).df()

    # 1. Summary Statistics for Continuous Model Predictors & Target
    model_vars = [
        "buffer_delay_min",
        "dep_delay_min",
        "taxi_in_min",
        "air_time_min",
        "arr_delay_min",
        "delay_due_nas"
    ]
    summary_rows = []
    for col in model_vars:
        s = df[col].dropna()
        q25, med, q75, q95 = np.percentile(s, [25, 50, 75, 95])
        summary_rows.append({
            "variable": col,
            "n": len(s),
            "mean": round(s.mean(), 2),
            "std": round(s.std(), 2),
            "min": round(s.min(), 2),
            "p25": round(q25, 2),
            "median": round(med, 2),
            "p75": round(q75, 2),
            "p95": round(q95, 2),
            "max": round(s.max(), 2),
            "iqr": round(q75 - q25, 2),
            "skewness": round(stats.skew(s), 2)
        })
    summary_stats_df = pd.DataFrame(summary_rows)
    print("\n[H2.1] Summary Statistics for Predictive Features & Target (buffer_delay_min):")
    print(summary_stats_df.to_string(index=False))
    summary_stats_df.to_csv(os.path.join(TABLES_DIR, "h2_predictor_summary_stats.csv"), index=False)

    # 2. Data Cleaning & Transformation Check: IQR Outlier Bounds & Log-Transform of Taxi-In
    q1_buf, q3_buf = np.percentile(df["buffer_delay_min"], [25, 75])
    iqr_buf = q3_buf - q1_buf
    lower_fence = q1_buf - 1.5 * iqr_buf
    upper_fence = q3_buf + 1.5 * iqr_buf
    outliers_cnt = ((df["buffer_delay_min"] < lower_fence) | (df["buffer_delay_min"] > upper_fence)).sum()

    # Apply log1p transformation to right-skewed taxi_in_min for linear model conditioning
    df["log_taxi_in"] = np.log1p(df["taxi_in_min"].clip(lower=0))
    raw_taxi_skew = stats.skew(df["taxi_in_min"].dropna())
    log_taxi_skew = stats.skew(df["log_taxi_in"].dropna())

    print("\n[H2.2] Data Cleaning & Feature Transformation Diagnostics:")
    print(f"  - Buffer Delay IQR Fences: [{lower_fence:.1f} min, {upper_fence:.1f} min]")
    print(f"  - Extreme Tail Outliers Identified: {outliers_cnt:,} / {len(df):,} ({outliers_cnt/len(df)*100:.2f}%)")
    print(f"  - Taxi-In Skewness Reduction via log1p(): {raw_taxi_skew:.2f} -> {log_taxi_skew:.2f}")

    # 3. Terminal Hub Comparison & Kruskal-Wallis H-Test
    hub_stats = df.groupby("dest_airport").agg(
        flights=("buffer_delay_min", "count"),
        buffer_mean=("buffer_delay_min", lambda s: round(s.mean(), 2)),
        buffer_median=("buffer_delay_min", lambda s: round(s.median(), 2)),
        buffer_iqr=("buffer_delay_min", lambda s: round(np.percentile(s, 75) - np.percentile(s, 25), 2)),
        buffer_p95=("buffer_delay_min", lambda s: round(np.percentile(s, 95), 2)),
        taxi_in_mean=("taxi_in_min", lambda s: round(s.mean(), 2)),
        nas_delay_mean=("delay_due_nas", lambda s: round(s.mean(), 2))
    ).reset_index()

    groups = [g["buffer_delay_min"].values for _, g in df.groupby("dest_airport")]
    kw_stat, kw_p = stats.kruskal(*groups)

    print("\n[H2.3] Terminal Hub Buffer Delay & Friction Comparison:")
    print(hub_stats.to_string(index=False))
    print(f"  -> Kruskal-Wallis Test across (JFK, LGA, EWR): H = {kw_stat:.4f}, p = {kw_p:.4e}")
    hub_stats.to_csv(os.path.join(TABLES_DIR, "h2_airport_hub_comparison.csv"), index=False)

    # 4. Wake Turbulence Class Comparison (JFK & EWR where Heavy widebodies operate)
    df_widebody_hubs = df[df["dest_airport"].isin(["JFK", "EWR"])].copy()
    wake_stats = df_widebody_hubs.groupby(["dest_airport", "wake_turbulence_class"]).agg(
        flights=("buffer_delay_min", "count"),
        buffer_mean=("buffer_delay_min", lambda s: round(s.mean(), 2)),
        buffer_median=("buffer_delay_min", lambda s: round(s.median(), 2)),
        buffer_p95=("buffer_delay_min", lambda s: round(np.percentile(s, 95), 2)),
        taxi_in_mean=("taxi_in_min", lambda s: round(s.mean(), 2)),
        taxi_in_median=("taxi_in_min", lambda s: round(s.median(), 2))
    ).reset_index()

    large_buf = df_widebody_hubs[df_widebody_hubs["wake_turbulence_class"] == "Large"]["buffer_delay_min"]
    heavy_buf = df_widebody_hubs[df_widebody_hubs["wake_turbulence_class"] == "Heavy"]["buffer_delay_min"]
    u_buf, p_buf = stats.mannwhitneyu(large_buf, heavy_buf, alternative="two-sided")
    large_taxi = df_widebody_hubs[df_widebody_hubs["wake_turbulence_class"] == "Large"]["taxi_in_min"].dropna()
    heavy_taxi = df_widebody_hubs[df_widebody_hubs["wake_turbulence_class"] == "Heavy"]["taxi_in_min"].dropna()
    u_taxi, p_taxi = stats.mannwhitneyu(large_taxi, heavy_taxi, alternative="two-sided")

    print("\n[H2.4] Wake Turbulence Stratification at Widebody Hubs (JFK & EWR):")
    print(wake_stats.to_string(index=False))
    print(f"  -> Mann-Whitney U (Buffer Delay Large vs Heavy): U = {u_buf:.1f}, p = {p_buf:.4e}")
    print(f"  -> Mann-Whitney U (Taxi-In Time Large vs Heavy): U = {u_taxi:.1f}, p = {p_taxi:.4e}")
    wake_stats.to_csv(os.path.join(TABLES_DIR, "h2_wake_turbulence_comparison.csv"), index=False)

    # 5. Bivariate Correlation Matrix (Spearman Rank for Non-Normal Metrics)
    corr_cols = ["buffer_delay_min", "dep_delay_min", "taxi_in_min", "air_time_min", "delay_due_nas"]
    spearman_corr = df[corr_cols].corr(method="spearman").round(3)
    print("\n[H2.5] Spearman Rank Correlation Matrix:")
    print(spearman_corr.to_string())
    spearman_corr.to_csv(os.path.join(TABLES_DIR, "h2_spearman_correlations.csv"))

    # 6. Figure 2: Multi-Panel Univariate & Bivariate Diagnostics (BTS)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Panel A: KDE Distribution of Sequencing Buffer Delay (Trimmed to [-45, +65] for visual clarity)
    for airport, color in [("JFK", "#1f77b4"), ("LGA", "#2ca02c"), ("EWR", "#ff7f0e")]:
        sub = df[(df["dest_airport"] == airport) & (df["buffer_delay_min"].between(-45, 65))]
        sns.kdeplot(sub["buffer_delay_min"], ax=axes[0, 0], label=f"{airport} (Med={df[df['dest_airport']==airport]['buffer_delay_min'].median():.1f}m)", color=color, linewidth=2)
    axes[0, 0].axvline(0, color="black", linestyle="--", alpha=0.7, label="Zero Buffer (On-Schedule)")
    axes[0, 0].set_title("(A) Sequencing Buffer Delay (Δt) Density Across Hubs")
    axes[0, 0].set_xlabel("Buffer Delay Δt: Actual - CRS Elapsed Time (min)")
    axes[0, 0].set_ylabel("Density")
    axes[0, 0].legend(frameon=True)

    # Panel B: Boxplot of Buffer Delay by Airport & Wake Class (JFK vs EWR)
    sns.boxplot(
        data=df_widebody_hubs[df_widebody_hubs["buffer_delay_min"].between(-45, 65)],
        x="dest_airport",
        y="buffer_delay_min",
        hue="wake_turbulence_class",
        palette={"Large": "#7293cb", "Heavy": "#d35e60"},
        showmeans=True,
        meanprops={"marker": "o", "markerfacecolor": "white", "markeredgecolor": "black", "markersize": 6},
        ax=axes[0, 1]
    )
    axes[0, 1].set_title("(B) Buffer Delay by Wake Class at Widebody Hubs")
    axes[0, 1].set_xlabel("Destination Hub")
    axes[0, 1].set_ylabel("Buffer Delay Δt (min)")
    axes[0, 1].legend(title="Wake Class", frameon=True)

    # Panel C: Bivariate Association - Taxi-In Duration vs Sequencing Buffer Delay (Hexbin/Regplot sample)
    sample_df = df[df["buffer_delay_min"].between(-40, 80) & df["taxi_in_min"].between(1, 50)].sample(n=min(8000, len(df)), random_state=42)
    sns.regplot(
        data=sample_df,
        x="taxi_in_min",
        y="buffer_delay_min",
        scatter_kws={"alpha": 0.15, "s": 10, "color": "#2b5c8f"},
        line_kws={"color": "#b2182b", "linewidth": 2},
        ax=axes[1, 0]
    )
    r_taxi, _ = stats.spearmanr(df["taxi_in_min"].dropna(), df.loc[df["taxi_in_min"].notna(), "buffer_delay_min"])
    axes[1, 0].set_title(f"(C) Surface Taxi-In vs. Buffer Delay (Spearman r = {r_taxi:.2f})")
    axes[1, 0].set_xlabel("Taxi-In Duration (minutes)")
    axes[1, 0].set_ylabel("Buffer Delay Δt (minutes)")

    # Panel D: Spearman Correlation Heatmap of Predictive Features
    sns.heatmap(
        spearman_corr,
        annot=True,
        fmt=".2f",
        cmap="RdBu_r",
        vmin=-0.6,
        vmax=0.6,
        center=0,
        square=True,
        cbar_kws={"shrink": 0.8},
        ax=axes[1, 1]
    )
    axes[1, 1].set_title("(D) Spearman Rank Correlation Matrix (BTS Predictors)")

    plt.tight_layout()
    fig2_path = os.path.join(FIGURES_DIR, "fig2_bts_eda_diagnostics.png")
    plt.savefig(fig2_path)
    plt.close()
    print(f"  -> Saved Figure 2: {fig2_path}")


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        run_h1_asrs_eda(con)
        run_h2_bts_eda(con)
        print("\n" + "=" * 82)
        print("EDA COMPLETE: All tables written to reports/tables/ and figures to reports/figures/.")
        print("=" * 82)
    finally:
        con.close()


if __name__ == "__main__":
    main()