"""Build thesis-ready method input datasets for the revised BUR analysis.

This script takes the latest validated canonical files and creates clean
analysis panels for the Methods and Results chapters. It explicitly labels LGU
measures as fiscal absorption proxies and keeps official true BUR at the
national-year scope.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Central project paths. The script reads only from the latest validator run
# and writes new timestamped method-input outputs.
BASE_DIR = Path(r"D:/am/research_journals_pdf_library/data")
RUNS_DIR = BASE_DIR / "analysis" / "runs"
SCRIPT_NAME = "revised_bur_method_inputs"


def make_run_dirs() -> dict[str, Path]:
    # Create a fresh output folder with final, validated, input, and logs
    # subfolders so previous thesis outputs remain untouched.
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = RUNS_DIR / f"{run_id}_{SCRIPT_NAME}"
    paths = {
        "root": root,
        "input": root / "input",
        "final": root / "final",
        "validated": root / "validated",
        "logs": root / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def latest_validator_dir() -> Path:
    # Find the newest validator run because it contains the authoritative
    # canonical panel and true-BUR readiness outputs.
    hits = sorted(RUNS_DIR.glob("*_true_bur_thesis_validator"), reverse=True)
    if not hits:
        raise FileNotFoundError("No *_true_bur_thesis_validator run folder found.")
    return hits[0]


def read_csv(path: Path) -> pd.DataFrame:
    # Required source files should fail loudly if missing, because these method
    # inputs must be built from validated canonical data.
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False)


def numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    # Coerce expected fiscal columns to numeric while leaving text identifiers
    # and categorical fields unchanged.
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def prepare_panel(panel: pd.DataFrame) -> pd.DataFrame:
    # Standardize the LGU-year panel, rebuild verified full expenditure, create
    # the absorption/utilization proxies, and add Mandanas time indicators.
    out = panel.copy()
    out = numeric(out, [
        "year",
        "total_income",
        "total_expenditure",
        "total_current_operating_expenditure",
        "total_non_operating_expenditures",
        "annual_regular_income",
        "transfer_dependency",
        "transfer_dependency_final",
        "local_revenue_ratio",
        "local_revenue_ratio_final",
        "dbm_appropriation_total",
        "population",
        "poverty_incidence",
    ])
    out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
    if {"total_current_operating_expenditure", "total_non_operating_expenditures"}.issubset(out.columns):
        out["verified_full_expenditure"] = out["total_current_operating_expenditure"] + out["total_non_operating_expenditures"]
    elif "total_expenditure" in out.columns:
        out["verified_full_expenditure"] = out["total_expenditure"]
    else:
        out["verified_full_expenditure"] = np.nan
    out["lgu_absorption_proxy"] = out["verified_full_expenditure"] / pd.to_numeric(out.get("total_income"), errors="coerce").replace({0: np.nan})
    if "dbm_appropriation_total" in out.columns:
        out["appropriation_absorption_proxy"] = out["verified_full_expenditure"] / out["dbm_appropriation_total"].replace({0: np.nan})
    else:
        out["appropriation_absorption_proxy"] = np.nan
    out["post_mandanas"] = (out["year"] >= 2022).astype(int)
    out["time_index_2009"] = out["year"] - 2008
    out["time_after_2021"] = np.where(out["year"] >= 2022, out["year"] - 2021, 0)
    out["analysis_measure_scope"] = "LGU_YEAR_PROXY_ONLY_NOT_TRUE_BUR"
    return out


def prepare_national_bur(df: pd.DataFrame) -> pd.DataFrame:
    # Prepare the COA national true-BUR series for national-year analysis. This
    # is the only official true-BUR scope currently supported by the validator.
    out = df.copy()
    out = numeric(out, ["year", "bur_true", "total_appropriations_final", "total_obligations_final", "total_appropriations_actual", "total_obligations_actual"])
    out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
    out["coa_true_bur"] = pd.to_numeric(out.get("bur_true"), errors="coerce")
    out["post_mandanas"] = (out["year"] >= 2022).astype(int)
    out["time_index"] = np.arange(1, len(out) + 1)
    out["time_after_2021"] = np.where(out["year"] >= 2022, out["year"] - 2021, 0)
    out["analysis_measure_scope"] = "NATIONAL_YEAR_TRUE_BUR"
    return out


def completeness_report(df: pd.DataFrame, name: str) -> pd.DataFrame:
    # Summarize missingness for the fields needed by the method chapter and
    # downstream model scripts.
    cols = [
        "total_income",
        "verified_full_expenditure",
        "lgu_absorption_proxy",
        "appropriation_absorption_proxy",
        "annual_regular_income",
        "transfer_dependency",
        "local_revenue_ratio",
        "dbm_appropriation_total",
        "coa_true_bur",
    ]
    rows = []
    for col in cols:
        if col in df.columns:
            rows.append({
                "dataset": name,
                "column": col,
                "rows": len(df),
                "nonmissing": int(df[col].notna().sum()),
                "missing_rate": float(df[col].isna().mean()),
            })
    return pd.DataFrame(rows)


def write_data_dictionary(path: Path) -> None:
    # Save a data dictionary that documents formulas and warns which measures
    # are proxies versus official national true BUR.
    rows = [
        {"variable": "verified_full_expenditure", "formula_or_source": "total_current_operating_expenditure + total_non_operating_expenditures", "scope": "LGU-year", "interpretation": "Verified full expenditure rebuilt from BLGF expenditure components."},
        {"variable": "lgu_absorption_proxy", "formula_or_source": "verified_full_expenditure / total_income", "scope": "LGU-year proxy", "interpretation": "Fiscal absorption/utilization proxy; not official true BUR."},
        {"variable": "appropriation_absorption_proxy", "formula_or_source": "verified_full_expenditure / dbm_appropriation_total", "scope": "LGU-year proxy where DBM appropriation matches", "interpretation": "Supplementary appropriation absorption proxy; not official true BUR."},
        {"variable": "coa_true_bur", "formula_or_source": "COA national BUR series from true_bur_national_2015_2024", "scope": "National-year", "interpretation": "Official/government true BUR evidence currently usable only at national yearly level."},
        {"variable": "post_mandanas", "formula_or_source": "1 if year >= 2022 else 0", "scope": "All model datasets", "interpretation": "Mandanas-Garcia implementation indicator."},
    ]
    pd.DataFrame(rows).to_csv(path, index=False)


def main() -> None:
    # Main workflow: load canonical inputs, derive method variables, split the
    # thesis windows, and write data dictionary/completeness files.
    out = make_run_dirs()
    validator_dir = latest_validator_dir()
    panel_2024 = prepare_panel(read_csv(validator_dir / "final" / "canonical_panel_2009_2024_for_thesis.csv"))
    panel_2021 = prepare_panel(read_csv(validator_dir / "final" / "canonical_panel_2009_2021_for_thesis.csv"))
    national_2024 = prepare_national_bur(read_csv(validator_dir / "final" / "true_bur_national_2015_2024.csv"))
    national_2021 = prepare_national_bur(read_csv(validator_dir / "final" / "true_bur_national_2015_2021.csv"))

    panel_post = panel_2024[panel_2024["year"].between(2022, 2024, inclusive="both")].copy()
    panel_2024.to_csv(out["final"] / "method_panel_2009_2024.csv", index=False)
    panel_2021.to_csv(out["final"] / "method_panel_2009_2021.csv", index=False)
    panel_post.to_csv(out["final"] / "method_panel_2022_2024.csv", index=False)
    national_2024.to_csv(out["final"] / "national_true_bur_2015_2024.csv", index=False)
    national_2021.to_csv(out["final"] / "national_true_bur_2015_2021.csv", index=False)

    comp = pd.concat([
        completeness_report(panel_2024, "method_panel_2009_2024"),
        completeness_report(panel_2021, "method_panel_2009_2021"),
        completeness_report(panel_post, "method_panel_2022_2024"),
        completeness_report(national_2024, "national_true_bur_2015_2024"),
    ], ignore_index=True)
    comp.to_csv(out["validated"] / "method_input_completeness.csv", index=False)
    write_data_dictionary(out["final"] / "method_data_dictionary.csv")

    method_note = """# Method Input Scope Note

The LGU-year panel supports analysis of fiscal absorption/utilization using verified full expenditure measures, but it does not support official LGU-level true-BUR models under the latest validator evidence.

Use `lgu_absorption_proxy` and `appropriation_absorption_proxy` as proxies only. Use `coa_true_bur` only for national-by-year true BUR analysis.
"""
    (out["final"] / "method_scope_note.md").write_text(method_note, encoding="utf-8")
    manifest = {
        "created_at": datetime.now().isoformat(),
        "script": Path(__file__).name,
        "validator_dir": str(validator_dir),
        "output_dir": str(out["root"]),
        "rows": {
            "method_panel_2009_2024": int(len(panel_2024)),
            "method_panel_2009_2021": int(len(panel_2021)),
            "method_panel_2022_2024": int(len(panel_post)),
            "national_true_bur_2015_2024": int(len(national_2024)),
        },
    }
    (out["logs"] / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Method input build complete.")
    print(f"Output directory: {out['root']}")


if __name__ == "__main__":
    main()
