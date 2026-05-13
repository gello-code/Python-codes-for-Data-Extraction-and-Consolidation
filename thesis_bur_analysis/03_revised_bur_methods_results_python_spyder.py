"""Run the Python-side revised BUR methods and results pipeline.

This script reads the method-input datasets, produces descriptive statistics,
LGU proxy models, national true-BUR models, diagnostics, counterfactual
forecasts, sensitivity checks, and figures for Chapters 3-5. LGU outcomes are
kept as fiscal absorption/utilization proxies; official true BUR is modeled
only at national-year scope.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Optional plotting dependencies. If unavailable, the script still writes CSV
# tables and skips figure generation rather than failing the whole pipeline.
try:
    import matplotlib.pyplot as plt
    import seaborn as sns
except ImportError:
    plt = None
    sns = None

# Optional econometric dependencies. If statsmodels is unavailable, descriptive
# outputs still run and model tables report that the dependency is missing.
try:
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
    from statsmodels.stats.diagnostic import acorr_ljungbox, het_breuschpagan
    from statsmodels.stats.stattools import durbin_watson
    from statsmodels.tsa.stattools import adfuller
except ImportError:
    sm = None
    smf = None
    acorr_ljungbox = None
    het_breuschpagan = None
    durbin_watson = None
    adfuller = None

# Shared project paths and run-folder naming convention.
BASE_DIR = Path(r"D:/am/research_journals_pdf_library/data")
RUNS_DIR = BASE_DIR / "analysis" / "runs"
SCRIPT_NAME = "revised_bur_python_methods_results"
LGU_PANEL_CORE_CONTROLS = ["post_mandanas", "annual_regular_income", "transfer_dependency", "local_revenue_ratio"]
LGU_PANEL_EXCLUDED_CONTROLS = ["poverty_incidence"]


def make_run_dirs() -> dict[str, Path]:
    # Create a timestamped output folder for final tables, diagnostics, figures,
    # and logs so repeated runs do not overwrite previous evidence.
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = RUNS_DIR / f"{run_id}_{SCRIPT_NAME}"
    paths = {"root": root, "final": root / "final", "validated": root / "validated", "figures": root / "figures", "logs": root / "logs"}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def latest_method_dir() -> Path:
    # Prefer the newest method-input run; fall back to validator final files if
    # the method-input builder has not been executed yet.
    hits = sorted(RUNS_DIR.glob("*_revised_bur_method_inputs"), reverse=True)
    if hits:
        return hits[0]
    validators = sorted(RUNS_DIR.glob("*_true_bur_thesis_validator"), reverse=True)
    if not validators:
        raise FileNotFoundError("No method-input or validator run folder found.")
    return validators[0]


def read_inputs(method_dir: Path) -> dict[str, pd.DataFrame]:
    # Load either the derived method-input files or the raw validator final
    # files, depending on what exists in the latest source folder.
    final = method_dir / "final"
    if (final / "method_panel_2009_2024.csv").exists():
        return {
            "panel_2024": pd.read_csv(final / "method_panel_2009_2024.csv", low_memory=False),
            "panel_2021": pd.read_csv(final / "method_panel_2009_2021.csv", low_memory=False),
            "national_2024": pd.read_csv(final / "national_true_bur_2015_2024.csv", low_memory=False),
            "national_2021": pd.read_csv(final / "national_true_bur_2015_2021.csv", low_memory=False),
        }
    return {
        "panel_2024": pd.read_csv(final / "canonical_panel_2009_2024_for_thesis.csv", low_memory=False),
        "panel_2021": pd.read_csv(final / "canonical_panel_2009_2021_for_thesis.csv", low_memory=False),
        "national_2024": pd.read_csv(final / "true_bur_national_2015_2024.csv", low_memory=False),
        "national_2021": pd.read_csv(final / "true_bur_national_2015_2021.csv", low_memory=False),
    }


def prepare_panel(df: pd.DataFrame) -> pd.DataFrame:
    # Standardize fiscal columns, rebuild the verified full expenditure field if
    # needed, and create/fill the LGU fiscal absorption proxy.
    out = df.copy()
    for col in out.columns:
        if col in {"year", "total_income", "total_expenditure", "verified_full_expenditure", "lgu_absorption_proxy", "appropriation_absorption_proxy", "annual_regular_income", "transfer_dependency", "transfer_dependency_final", "local_revenue_ratio", "local_revenue_ratio_final", "dbm_appropriation_total", "poverty_incidence"}:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "verified_full_expenditure" not in out.columns:
        if {"total_current_operating_expenditure", "total_non_operating_expenditures"}.issubset(out.columns):
            out["verified_full_expenditure"] = pd.to_numeric(out["total_current_operating_expenditure"], errors="coerce") + pd.to_numeric(out["total_non_operating_expenditures"], errors="coerce")
        else:
            out["verified_full_expenditure"] = pd.to_numeric(out.get("total_expenditure"), errors="coerce")
    if "lgu_absorption_proxy" not in out.columns:
        out["lgu_absorption_proxy"] = out["verified_full_expenditure"] / pd.to_numeric(out.get("total_income"), errors="coerce").replace({0: np.nan})
    if "post_mandanas" not in out.columns:
        out["post_mandanas"] = (pd.to_numeric(out["year"], errors="coerce") >= 2022).astype(int)
    if "transfer_dependency" not in out.columns and "transfer_dependency_final" in out.columns:
        out["transfer_dependency"] = out["transfer_dependency_final"]
    if "local_revenue_ratio" not in out.columns and "local_revenue_ratio_final" in out.columns:
        out["local_revenue_ratio"] = out["local_revenue_ratio_final"]
    return out


def prepare_national(df: pd.DataFrame) -> pd.DataFrame:
    # Standardize the national COA true-BUR series and add Mandanas time
    # indicators for interrupted-trend style modeling.
    out = df.copy()
    for col in ["year", "bur_true", "coa_true_bur"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "coa_true_bur" not in out.columns:
        out["coa_true_bur"] = pd.to_numeric(out.get("bur_true"), errors="coerce")
    out["post_mandanas"] = (out["year"] >= 2022).astype(int)
    out["time_index"] = np.arange(1, len(out) + 1)
    out["time_after_2021"] = np.where(out["year"] >= 2022, out["year"] - 2021, 0)
    return out


def descriptive_stats(df: pd.DataFrame, label: str) -> pd.DataFrame:
    # Produce chapter-ready summary statistics for each model window.
    vars_ = ["total_income", "verified_full_expenditure", "lgu_absorption_proxy", "appropriation_absorption_proxy", "transfer_dependency", "local_revenue_ratio"]
    rows = []
    for v in vars_:
        if v not in df.columns:
            continue
        s = pd.to_numeric(df[v], errors="coerce")
        rows.append({"dataset": label, "variable": v, "n": int(s.notna().sum()), "mean": s.mean(), "median": s.median(), "sd": s.std(), "min": s.min(), "max": s.max()})
    return pd.DataFrame(rows)


def yearly_summary(df: pd.DataFrame) -> pd.DataFrame:
    # Collapse the LGU panel to annual averages used by trend, forecast, and
    # counterfactual figures.
    vars_ = ["total_income", "verified_full_expenditure", "lgu_absorption_proxy", "appropriation_absorption_proxy", "transfer_dependency", "local_revenue_ratio"]
    rows = []
    for year, grp in df.groupby("year"):
        row = {"year": int(year), "n_lgus": len(grp)}
        for v in vars_:
            if v in grp.columns:
                row[f"mean_{v}"] = pd.to_numeric(grp[v], errors="coerce").mean()
                row[f"median_{v}"] = pd.to_numeric(grp[v], errors="coerce").median()
        rows.append(row)
    return pd.DataFrame(rows).sort_values("year")


def prepost_comparison(df: pd.DataFrame) -> pd.DataFrame:
    # Compare pre-implementation observations with 2022-2024 observations.
    rows = []
    for v in ["total_income", "verified_full_expenditure", "lgu_absorption_proxy", "appropriation_absorption_proxy", "transfer_dependency", "local_revenue_ratio"]:
        if v not in df.columns:
            continue
        pre = pd.to_numeric(df.loc[df["year"] <= 2021, v], errors="coerce").dropna()
        post = pd.to_numeric(df.loc[df["year"] >= 2022, v], errors="coerce").dropna()
        rows.append({"variable": v, "n_pre": len(pre), "n_post": len(post), "mean_pre": pre.mean(), "mean_post": post.mean(), "diff_post_minus_pre": post.mean() - pre.mean(), "pct_change": ((post.mean() - pre.mean()) / abs(pre.mean()) * 100) if pre.mean() not in [0, np.nan] else np.nan})
    return pd.DataFrame(rows)


def fallback_ols(df: pd.DataFrame, outcome: str, rhs: list[str], add_year_effects: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    y = pd.to_numeric(df[outcome], errors="coerce").astype(float)
    x_parts = [pd.Series(1.0, index=df.index, name="Intercept")]
    for col in rhs:
        x_parts.append(pd.to_numeric(df[col], errors="coerce").astype(float).rename(col))
    if add_year_effects:
        years = pd.get_dummies(pd.to_numeric(df["year"], errors="coerce").astype(int), prefix="C(year)", drop_first=True, dtype=float)
        x_parts.append(years)
    X_df = pd.concat(x_parts, axis=1).astype(float)
    mask = y.notna() & X_df.notna().all(axis=1)
    yv = y.loc[mask].to_numpy(dtype=float)
    X_model = X_df.loc[mask].copy()
    standardized_terms = []
    for col in X_model.columns:
        if col == "Intercept":
            standardized_terms.append(col)
            continue
        sd = X_model[col].std(ddof=0)
        if sd and not np.isnan(sd):
            X_model[col] = (X_model[col] - X_model[col].mean()) / sd
        else:
            X_model[col] = 0.0
        standardized_terms.append(f"{col}_z")
    X = X_model.to_numpy(dtype=float)
    terms = standardized_terms
    n, p = X.shape
    beta = np.linalg.pinv(X.T @ X) @ X.T @ yv
    fitted = X @ beta
    resid = yv - fitted
    xtx_inv = np.linalg.pinv(X.T @ X)
    scale = n / max(n - p, 1)
    meat = X.T @ np.diag(resid ** 2) @ X
    cov = scale * xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    tvals = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se != 0)
    pvals = np.array([math.erfc(abs(t) / math.sqrt(2)) if not np.isnan(t) else np.nan for t in tvals])
    sse = float(np.sum(resid ** 2))
    sst = float(np.sum((yv - np.mean(yv)) ** 2))
    r2 = 1 - (sse / sst) if sst else np.nan
    coefs = pd.DataFrame({"term": terms, "estimate": beta, "std_error_hc1": se, "p_value": pvals, "r_squared": r2, "n": n})
    residuals = pd.DataFrame({"year": df.loc[mask, "year"].to_numpy(), "fitted": fitted, "residual": resid})
    return coefs, residuals


def run_panel_models(df: pd.DataFrame, label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Estimate LGU-year proxy models with year effects and HC1 robust standard
    # errors. Sparse controls are excluded so complete-case samples do not
    # collapse; these are not LGU true-BUR models.
    outcomes = ["lgu_absorption_proxy", "transfer_dependency", "local_revenue_ratio"]
    controls = [c for c in LGU_PANEL_CORE_CONTROLS if c in df.columns]
    coef_rows = []
    residual_rows = []
    for outcome in outcomes:
        if outcome not in df.columns:
            continue
        rhs = [c for c in controls if c != outcome]
        d = df[[outcome, "year", *rhs]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 30 or not rhs:
            coef_rows.append({"dataset": label, "outcome": outcome, "status": "insufficient_complete_cases", "n": len(d), "predictors": " + ".join(rhs), "excluded_sparse_controls": ", ".join([c for c in LGU_PANEL_EXCLUDED_CONTROLS if c in df.columns])})
            continue
        formula = outcome + " ~ " + " + ".join(rhs) + " + C(year)"
        try:
            if smf is not None:
                model = smf.ols(formula, data=d).fit(cov_type="HC1")
                for term in model.params.index:
                    coef_rows.append({"dataset": label, "outcome": outcome, "term": term, "estimate": model.params[term], "std_error_hc1": model.bse[term], "p_value": model.pvalues[term], "r_squared": model.rsquared, "n": int(model.nobs), "status": "ok", "predictors": " + ".join(rhs) + " + C(year)", "excluded_sparse_controls": ", ".join([c for c in LGU_PANEL_EXCLUDED_CONTROLS if c in df.columns])})
                residual_rows.extend({"dataset": label, "outcome": outcome, "year": y, "fitted": f, "residual": r} for y, f, r in zip(d["year"], model.fittedvalues, model.resid))
            else:
                coefs, resids = fallback_ols(d, outcome, rhs, True)
                for _, term_row in coefs.iterrows():
                    coef_rows.append({"dataset": label, "outcome": outcome, "term": term_row["term"], "estimate": term_row["estimate"], "std_error_hc1": term_row["std_error_hc1"], "p_value": term_row["p_value"], "r_squared": term_row["r_squared"], "n": int(term_row["n"]), "status": "ok_fallback_ols", "predictors": " + ".join(rhs) + " + C(year)", "excluded_sparse_controls": ", ".join([c for c in LGU_PANEL_EXCLUDED_CONTROLS if c in df.columns])})
                residual_rows.extend({"dataset": label, "outcome": outcome, "year": y, "fitted": f, "residual": r} for y, f, r in zip(resids["year"], resids["fitted"], resids["residual"]))
        except Exception as exc:
            coef_rows.append({"dataset": label, "outcome": outcome, "status": "model_error", "error": str(exc), "n": len(d), "predictors": " + ".join(rhs) + " + C(year)", "excluded_sparse_controls": ", ".join([c for c in LGU_PANEL_EXCLUDED_CONTROLS if c in df.columns])})
    return pd.DataFrame(coef_rows), pd.DataFrame(residual_rows)


def run_national_true_bur(nat: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Estimate the national official true-BUR trend/interruption model.
    d = nat[["year", "coa_true_bur", "time_index", "post_mandanas", "time_after_2021"]].dropna()
    if len(d) < 5:
        return pd.DataFrame([{"dataset": "national_true_bur", "status": "insufficient_years", "n": len(d)}]), pd.DataFrame()
    if smf is not None:
        model = smf.ols("coa_true_bur ~ time_index + post_mandanas + time_after_2021", data=d).fit(cov_type="HC1")
        coefs = pd.DataFrame({"dataset": "national_true_bur_2015_2024", "outcome": "coa_true_bur", "term": model.params.index, "estimate": model.params.values, "std_error_hc1": model.bse.values, "p_value": model.pvalues.values, "r_squared": model.rsquared, "n": int(model.nobs), "status": "ok"})
        residuals = pd.DataFrame({"dataset": "national_true_bur_2015_2024", "outcome": "coa_true_bur", "year": d["year"], "fitted": model.fittedvalues, "residual": model.resid})
    else:
        coefs_raw, resids_raw = fallback_ols(d, "coa_true_bur", ["time_index", "post_mandanas", "time_after_2021"], False)
        coefs = coefs_raw.assign(dataset="national_true_bur_2015_2024", outcome="coa_true_bur", status="ok_fallback_ols")
        residuals = resids_raw.assign(dataset="national_true_bur_2015_2024", outcome="coa_true_bur")
    return coefs, residuals


def diagnostics(residuals: pd.DataFrame) -> pd.DataFrame:
    # Turn model residuals into diagnostic checks for autocorrelation,
    # heteroscedasticity, stationarity screens, and residual centering.
    rows = []
    if residuals.empty:
        return pd.DataFrame()
    for (dataset, outcome), grp in residuals.groupby(["dataset", "outcome"]):
        r = pd.to_numeric(grp["residual"], errors="coerce").dropna()
        f = pd.to_numeric(grp["fitted"], errors="coerce").loc[r.index] if len(r) else pd.Series(dtype=float)
        rows.append({"dataset": dataset, "outcome": outcome, "diagnostic": "residual_mean", "value": r.mean(), "p_value": np.nan, "interpretation": "Near zero is expected."})
        rows.append({"dataset": dataset, "outcome": outcome, "diagnostic": "residual_std", "value": r.std(), "p_value": np.nan, "interpretation": "Residual dispersion."})
        if durbin_watson is not None and len(r) > 2:
            rows.append({"dataset": dataset, "outcome": outcome, "diagnostic": "durbin_watson", "value": float(durbin_watson(r)), "p_value": np.nan, "interpretation": "Near 2 suggests limited first-order autocorrelation."})
        if acorr_ljungbox is not None and len(r) > 4:
            lb = acorr_ljungbox(r, lags=[min(3, len(r) - 2)], return_df=True)
            rows.append({"dataset": dataset, "outcome": outcome, "diagnostic": "ljung_box", "value": float(lb["lb_stat"].iloc[0]), "p_value": float(lb["lb_pvalue"].iloc[0]), "interpretation": "Small p-value suggests residual autocorrelation."})
        if sm is not None and het_breuschpagan is not None and len(r) > 10 and len(f) == len(r):
            x = sm.add_constant(f)
            bp = het_breuschpagan(r, x)
            rows.append({"dataset": dataset, "outcome": outcome, "diagnostic": "breusch_pagan", "value": float(bp[0]), "p_value": float(bp[1]), "interpretation": "Small p-value suggests heteroscedasticity."})
        if adfuller is not None and len(r) > 7:
            try:
                adf = adfuller(r, autolag=None, maxlag=1)
                rows.append({"dataset": dataset, "outcome": outcome, "diagnostic": "adf_residual", "value": float(adf[0]), "p_value": float(adf[1]), "interpretation": "Stationarity screen; interpret cautiously for short annual series."})
            except Exception:
                pass
    return pd.DataFrame(rows)


def forecast_counterfactual(yearly: pd.DataFrame, nat: pd.DataFrame) -> pd.DataFrame:
    # Fit simple pre-2022 linear trends and compare observed post-2022 outcomes
    # with the no-ruling counterfactual path.
    rows = []
    series_map = [(yearly, "mean_lgu_absorption_proxy"), (yearly, "mean_transfer_dependency"), (yearly, "mean_local_revenue_ratio"), (nat.rename(columns={"coa_true_bur": "mean_coa_true_bur"}), "mean_coa_true_bur")]
    for data, col in series_map:
        if col not in data.columns:
            continue
        d = data[["year", col]].dropna().copy()
        train = d[d["year"] <= 2021]
        if len(train) < 4:
            continue
        fit = np.polyfit(train["year"], train[col], 1)
        d["forecast_no_ruling"] = np.polyval(fit, d["year"])
        d["gap_actual_minus_forecast"] = d[col] - d["forecast_no_ruling"]
        d["outcome"] = col
        rows.append(d.rename(columns={col: "actual"}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def sensitivity_excluding_2020(yearly: pd.DataFrame) -> pd.DataFrame:
    # Refit forecast trends without 2020 to check whether pandemic-year data
    # materially changes the 2022-2024 forecast gaps.
    rows = []
    for col in ["mean_lgu_absorption_proxy", "mean_transfer_dependency", "mean_local_revenue_ratio"]:
        if col not in yearly.columns:
            continue
        d = yearly[["year", col]].dropna()
        base_train = d[d["year"] <= 2021]
        sens_train = base_train[base_train["year"] != 2020]
        if len(base_train) < 4 or len(sens_train) < 4:
            continue
        base = np.polyfit(base_train["year"], base_train[col], 1)
        sens = np.polyfit(sens_train["year"], sens_train[col], 1)
        post = d[d["year"].between(2022, 2024)]
        gap = np.abs(np.polyval(base, post["year"]) - np.polyval(sens, post["year"])).mean()
        rows.append({"outcome": col, "sensitivity": "exclude_2020", "mean_abs_forecast_difference_2022_2024": gap})
    return pd.DataFrame(rows)


def make_figures(yearly: pd.DataFrame, forecast: pd.DataFrame, residuals: pd.DataFrame, figures_dir: Path) -> None:
    # Save visual checks: annual trends, actual-vs-counterfactual paths, and
    # residual diagnostic plots.
    if plt is None:
        return
    if sns is not None:
        sns.set_theme(style="whitegrid")
    for col in ["mean_lgu_absorption_proxy", "mean_transfer_dependency", "mean_local_revenue_ratio"]:
        if col not in yearly.columns:
            continue
        plt.figure(figsize=(9, 5))
        plt.plot(yearly["year"], yearly[col], marker="o")
        plt.axvline(2022, color="red", linestyle="--")
        plt.title(col.replace("_", " ").title())
        plt.tight_layout()
        plt.savefig(figures_dir / f"trend_{col}.png", dpi=150)
        plt.close()
    if not forecast.empty:
        for outcome, grp in forecast.groupby("outcome"):
            safe = "".join(ch if ch.isalnum() else "_" for ch in outcome)
            plt.figure(figsize=(9, 5))
            plt.plot(grp["year"], grp["actual"], marker="o", label="Actual")
            plt.plot(grp["year"], grp["forecast_no_ruling"], marker="x", label="No-ruling forecast")
            plt.axvline(2022, color="red", linestyle="--")
            plt.legend()
            plt.title(f"Actual vs Counterfactual: {outcome}")
            plt.tight_layout()
            plt.savefig(figures_dir / f"forecast_{safe}.png", dpi=150)
            plt.close()
    if not residuals.empty:
        for (dataset, outcome), grp in residuals.groupby(["dataset", "outcome"]):
            safe = "".join(ch if ch.isalnum() else "_" for ch in f"{dataset}_{outcome}")[:120]
            plt.figure(figsize=(7, 5))
            plt.scatter(grp["fitted"], grp["residual"], alpha=0.35)
            plt.axhline(0, color="red", linestyle="--")
            plt.title(f"Residuals vs Fitted: {outcome}")
            plt.tight_layout()
            plt.savefig(figures_dir / f"residuals_vs_fitted_{safe}.png", dpi=150)
            plt.close()
            if sm is not None:
                sm.qqplot(pd.to_numeric(grp["residual"], errors="coerce").dropna(), line="45")
                plt.title(f"QQ Plot: {outcome}")
                plt.tight_layout()
                plt.savefig(figures_dir / f"qq_{safe}.png", dpi=150)
                plt.close()


def write_summary(path: Path, readiness_note: str) -> None:
    # Write a short Markdown summary that explains how to interpret the outputs.
    path.write_text(f"""# Revised BUR Python Results Summary

{readiness_note}

## Scope

- LGU models use `lgu_absorption_proxy`, `transfer_dependency`, and `local_revenue_ratio` as the main fiscal-ratio outcomes.
- `total_income` and `verified_full_expenditure` remain descriptive context only because the absorption proxy is expenditure divided by income.
- National true BUR uses `coa_true_bur` only at national-year level.
- LGU-level true-BUR regressions are intentionally not produced.

## Main Output Groups

- Descriptive statistics and yearly trends.
- 2009-2021 baseline and 2009-2024 observed model coefficients.
- 2022-2024 actual-vs-counterfactual forecast gaps.
- Residual, heteroscedasticity, autocorrelation, stationarity, and sensitivity diagnostics.
""", encoding="utf-8")


def main() -> None:
    # Main workflow: load inputs, prepare panels, create tables/models/figures,
    # and record the source/output manifest.
    out = make_run_dirs()
    method_dir = latest_method_dir()
    inputs = read_inputs(method_dir)
    panel_2024 = prepare_panel(inputs["panel_2024"])
    panel_2021 = prepare_panel(inputs["panel_2021"])
    nat_2024 = prepare_national(inputs["national_2024"])

    desc = pd.concat([descriptive_stats(panel_2021, "panel_2009_2021"), descriptive_stats(panel_2024, "panel_2009_2024")], ignore_index=True)
    yearly = yearly_summary(panel_2024)
    prepost = prepost_comparison(panel_2024)
    coef_2021, resid_2021 = run_panel_models(panel_2021, "panel_2009_2021")
    coef_2024, resid_2024 = run_panel_models(panel_2024, "panel_2009_2024")
    coef_nat, resid_nat = run_national_true_bur(nat_2024)
    coefs = pd.concat([coef_2021, coef_2024, coef_nat], ignore_index=True)
    residuals = pd.concat([resid_2021, resid_2024, resid_nat], ignore_index=True)
    diag = diagnostics(residuals)
    forecast = forecast_counterfactual(yearly, nat_2024)
    sensitivity = sensitivity_excluding_2020(yearly)

    desc.to_csv(out["final"] / "descriptive_statistics.csv", index=False)
    yearly.to_csv(out["final"] / "yearly_summary_2009_2024.csv", index=False)
    prepost.to_csv(out["final"] / "pre_post_comparison.csv", index=False)
    coefs.to_csv(out["final"] / "model_coefficients.csv", index=False)
    forecast.to_csv(out["final"] / "forecast_counterfactual_2022_2024.csv", index=False)
    residuals.to_csv(out["validated"] / "model_residuals.csv", index=False)
    diag.to_csv(out["validated"] / "diagnostics_summary.csv", index=False)
    sensitivity.to_csv(out["validated"] / "sensitivity_exclude_2020.csv", index=False)
    make_figures(yearly, forecast, residuals, out["figures"])
    write_summary(out["final"] / "chapter_3_5_results_summary.md", "Dataset readiness requires honest scope: LGU fiscal absorption proxy models are allowed; national true BUR models are allowed; LGU true-BUR models are not allowed.")

    manifest = {"created_at": datetime.now().isoformat(), "script": Path(__file__).name, "method_input_dir": str(method_dir), "output_dir": str(out["root"]), "rows": {"panel_2024": len(panel_2024), "panel_2021": len(panel_2021), "national_2024": len(nat_2024)}}
    (out["logs"] / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Python methods/results complete.")
    print(f"Output directory: {out['root']}")


if __name__ == "__main__":
    main()
