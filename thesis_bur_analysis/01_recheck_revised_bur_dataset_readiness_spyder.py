"""Recheck whether the Mandanas-Garcia BUR datasets are ready for thesis analysis.

This Spyder-friendly script reads the latest validator run, classifies which
analysis scopes are allowed, and writes a compact readiness gate for Chapters
3-5. Its most important guardrail is separating LGU fiscal proxy analysis from
national official true-BUR analysis.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Project paths used by the revised BUR pipeline. All outputs are written to
# a new timestamped run folder so canonical validator outputs are not changed.
BASE_DIR = Path(r"D:/am/research_journals_pdf_library/data")
RUNS_DIR = BASE_DIR / "analysis" / "runs"
SCRIPT_NAME = "revised_bur_dataset_readiness_recheck"


def make_run_dirs() -> dict[str, Path]:
    # Create the standard output structure used by the thesis analysis scripts.
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = RUNS_DIR / f"{run_id}_{SCRIPT_NAME}"
    paths = {
        "root": root,
        "final": root / "final",
        "validated": root / "validated",
        "figures": root / "figures",
        "logs": root / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def latest_validator_dir() -> Path:
    # Use the newest validator result as the authoritative readiness source.
    hits = sorted(RUNS_DIR.glob("*_true_bur_thesis_validator"), reverse=True)
    if not hits:
        raise FileNotFoundError("No *_true_bur_thesis_validator run folder found.")
    return hits[0]


def read_csv(path: Path) -> pd.DataFrame:
    # Missing optional audit files are represented by an empty table so the
    # readiness report can still be produced with partial evidence.
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def dataset_profile(df: pd.DataFrame, name: str) -> dict[str, object]:
    # Build row counts, year coverage, LGU coverage, duplicate checks, and
    # missingness indicators for key fiscal/BUR fields.
    out: dict[str, object] = {"dataset": name, "rows": int(len(df)), "columns": int(len(df.columns))}
    if df.empty:
        out.update({"year_min": np.nan, "year_max": np.nan, "distinct_years": 0, "distinct_lgus": 0, "duplicate_lgu_year_rows": np.nan})
        return out
    if "year" in df.columns:
        years = to_num(df["year"]).dropna()
        out["year_min"] = int(years.min()) if not years.empty else np.nan
        out["year_max"] = int(years.max()) if not years.empty else np.nan
        out["distinct_years"] = int(years.astype(int).nunique()) if not years.empty else 0
    if "lgu_key" in df.columns:
        out["distinct_lgus"] = int(df["lgu_key"].nunique(dropna=True))
    elif "lgu_id" in df.columns:
        out["distinct_lgus"] = int(df["lgu_id"].nunique(dropna=True))
    else:
        out["distinct_lgus"] = np.nan
    key_cols = [c for c in ["lgu_key", "year"] if c in df.columns]
    out["duplicate_lgu_year_rows"] = int(df.duplicated(key_cols).sum()) if len(key_cols) == 2 else np.nan
    for col in [
        "total_income",
        "total_expenditure",
        "total_current_operating_expenditure",
        "total_non_operating_expenditures",
        "verified_full_absorption_proxy",
        "annual_regular_income",
        "transfer_dependency",
        "local_revenue_ratio",
        "dbm_appropriation_total",
        "bur_true",
        "bur_true_coa",
    ]:
        if col in df.columns:
            out[f"nonmissing_{col}"] = int(df[col].notna().sum())
            out[f"missing_rate_{col}"] = float(df[col].isna().mean())
    return out


def build_readiness_rows(decision: pd.DataFrame, bur_audit: pd.DataFrame, canonical_2024: pd.DataFrame, canonical_2021: pd.DataFrame, nat_bur: pd.DataFrame) -> pd.DataFrame:
    # Convert validator decisions into an explicit gate: which analyses are
    # allowed, which are blocked, and why. This prevents repeated national BUR
    # values from being treated as LGU-year true BUR.
    rows: list[dict[str, object]] = []
    overall = decision[decision.get("dataset", pd.Series(dtype=str)).eq("overall_decision")]
    can_lgu_true = False
    can_nat_true = False
    status = "UNKNOWN"
    reason = "No validator decision row was found."
    if not overall.empty:
        row = overall.iloc[0]
        can_lgu_true = str(row.get("can_run_lgu_true_bur_models", "False")).lower() == "true"
        can_nat_true = str(row.get("can_run_national_true_bur_models", "False")).lower() == "true"
        status = str(row.get("sufficiency_status", "UNKNOWN"))
        reason = str(row.get("reason", ""))

    rows.append({
        "check": "overall_validator_decision",
        "status": status,
        "allowed": bool(can_nat_true or not canonical_2024.empty),
        "detail": reason,
    })
    rows.append({
        "check": "lgu_backbone_models",
        "status": "READY" if not canonical_2024.empty and not canonical_2021.empty else "NOT_READY",
        "allowed": bool(not canonical_2024.empty and not canonical_2021.empty),
        "detail": "Use canonical LGU fiscal backbone for 2009-2021 and 2009-2024 models.",
    })
    rows.append({
        "check": "lgu_true_bur_models",
        "status": "NOT_READY" if not can_lgu_true else "READY",
        "allowed": bool(can_lgu_true),
        "detail": "Do not run LGU-level true-BUR regressions unless validator upgrades BUR scope beyond national-only repeated values.",
    })
    rows.append({
        "check": "national_true_bur_models",
        "status": "READY" if can_nat_true and not nat_bur.empty else "NOT_READY",
        "allowed": bool(can_nat_true and not nat_bur.empty),
        "detail": "Use COA national true BUR as national-by-year evidence, not LGU fixed-effects true BUR.",
    })
    if not bur_audit.empty and "scope_class" in bur_audit.columns:
        for scope, grp in bur_audit.groupby("scope_class", dropna=False):
            rows.append({
                "check": f"bur_scope_{scope}",
                "status": "AUDITED",
                "allowed": False if scope in {"NATIONAL_ONLY_TRUE_BUR", "INSUFFICIENT_TRUE_BUR"} else True,
                "detail": f"Rows in audit scope: {len(grp)}",
            })
    return pd.DataFrame(rows)


def write_markdown(path: Path, readiness: pd.DataFrame, profiles: pd.DataFrame, validator_dir: Path) -> None:
    # Write a human-readable summary that can be copied into thesis methods,
    # limitations, or audit appendices.
    lines = [
        "# Revised BUR Dataset Readiness Recheck",
        "",
        f"Generated: {datetime.now().isoformat()}",
        f"Validator source: `{validator_dir}`",
        "",
        "## Decision",
        "",
    ]
    for _, row in readiness.iterrows():
        lines.append(f"- **{row['check']}**: {row['status']} | allowed={row['allowed']} | {row['detail']}")
    lines.extend([
        "",
        "## Interpretation for Thesis Scope",
        "",
        "- **LGU-level true BUR models are not thesis-ready** under the current validator evidence.",
        "- **LGU fiscal backbone models are allowed** using verified full expenditure / income as an explicitly labeled proxy or absorption measure.",
        "- **National true BUR models are allowed** using COA national-by-year BUR for 2015-2024.",
        "- Do not present repeated national BUR values as LGU-year true BUR.",
        "",
        "## Dataset Profiles",
        "",
    ])
    for _, row in profiles.iterrows():
        lines.append(f"- **{row['dataset']}**: rows={row['rows']}, years={row.get('year_min')} to {row.get('year_max')}, LGUs={row.get('distinct_lgus')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    # Main workflow: load the latest validator artifacts, evaluate readiness,
    # write machine-readable CSV/JSON outputs, and write a thesis-friendly
    # Markdown summary.
    out = make_run_dirs()
    validator_dir = latest_validator_dir()
    decision = read_csv(validator_dir / "validated" / "dataset_sufficiency_decision.csv")
    bur_audit = read_csv(validator_dir / "validated" / "bur_scope_audit.csv")
    missingness = read_csv(validator_dir / "validated" / "source_missingness_summary.csv")
    canonical_2024 = read_csv(validator_dir / "final" / "canonical_panel_2009_2024_for_thesis.csv")
    canonical_2021 = read_csv(validator_dir / "final" / "canonical_panel_2009_2021_for_thesis.csv")
    nat_2024 = read_csv(validator_dir / "final" / "true_bur_national_2015_2024.csv")
    nat_2021 = read_csv(validator_dir / "final" / "true_bur_national_2015_2021.csv")

    readiness = build_readiness_rows(decision, bur_audit, canonical_2024, canonical_2021, nat_2024)
    profiles = pd.DataFrame([
        dataset_profile(canonical_2024, "canonical_panel_2009_2024_for_thesis"),
        dataset_profile(canonical_2021, "canonical_panel_2009_2021_for_thesis"),
        dataset_profile(nat_2024, "true_bur_national_2015_2024"),
        dataset_profile(nat_2021, "true_bur_national_2015_2021"),
    ])

    readiness.to_csv(out["validated"] / "readiness_gate.csv", index=False)
    profiles.to_csv(out["validated"] / "dataset_profiles.csv", index=False)
    decision.to_csv(out["validated"] / "validator_decision_snapshot.csv", index=False)
    bur_audit.to_csv(out["validated"] / "bur_scope_audit_snapshot.csv", index=False)
    missingness.to_csv(out["validated"] / "source_missingness_snapshot.csv", index=False)
    write_markdown(out["final"] / "readiness_summary_for_chapters_3_5.md", readiness, profiles, validator_dir)

    manifest = {
        "created_at": datetime.now().isoformat(),
        "script": Path(__file__).name,
        "validator_dir": str(validator_dir),
        "output_dir": str(out["root"]),
        "decision": readiness.to_dict(orient="records"),
    }
    (out["logs"] / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Readiness recheck complete.")
    print(f"Output directory: {out['root']}")


if __name__ == "__main__":
    main()
