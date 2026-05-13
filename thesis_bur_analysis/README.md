# Revised BUR Thesis Analysis Pipeline

This folder contains the complete multi-language pipeline for analyzing Mandanas-Garcia Budget Utilization Rate (BUR) datasets for thesis Chapters 3–5.

## Overview

The thesis analysis separates **LGU fiscal absorption proxy models** (allowed) from **national official true-BUR models** (allowed) and explicitly excludes **LGU-level true-BUR models** (not supported by current validator evidence).

## Workflow Pipeline

### 1. **Readiness Check** (`01_readiness/`)
- **File:** `01_recheck_revised_bur_dataset_readiness_spyder.py`
- **Purpose:** Validates the latest BUR validator outputs and creates a readiness gate
- **Inputs:** Latest `*_true_bur_thesis_validator` run folder
- **Outputs:** 
  - `readiness_gate.csv` – Decision table for allowed/blocked analyses
  - `dataset_profiles.csv` – Row counts, year coverage, LGU counts, missingness
  - `readiness_summary_for_chapters_3_5.md` – Human-readable scope summary
- **Key Check:** Prevents LGU-year repeated national BUR values from being treated as true-BUR evidence

### 2. **Method Input Builder** (`02_method_inputs/`)
- **File:** `02_build_revised_bur_method_inputs_spyder.py`
- **Purpose:** Derives thesis-ready fiscal proxy variables and creates clean analysis panels
- **Inputs:** Latest validator canonical files
- **Outputs:**
  - `method_panel_2009_2024.csv` – Full LGU-year panel with computed proxies
  - `method_panel_2009_2021.csv` – Baseline window
  - `method_panel_2022_2024.csv` – Post-Mandanas window
  - `national_true_bur_2015_2024.csv` – Official COA true-BUR by year
  - `method_data_dictionary.csv` – Variable definitions and formulas
  - `method_scope_note.md` – Warnings about proxy vs. true-BUR distinction
- **Key Variables Created:**
  - `verified_full_expenditure` – Rebuilt from expenditure components
  - `lgu_absorption_proxy` = verified_full_expenditure / total_income
  - `appropriation_absorption_proxy` = verified_full_expenditure / dbm_appropriation_total
  - `post_mandanas` – Binary Mandanas-Garcia implementation indicator

### 3. **Python Models & Results** (`03_python_models/`)
- **File:** `03_revised_bur_methods_results_python_spyder.py`
- **Purpose:** Descriptive statistics, LGU proxy models, national true-BUR models, diagnostics, forecasts, sensitivity checks
- **Inputs:** Method-input datasets (or validator final files as fallback)
- **Outputs:**
  - `descriptive_statistics.csv` – Summary stats for all fiscal outcomes
  - `yearly_summary_2009_2024.csv` – Annual LGU averages
  - `pre_post_comparison.csv` – Means comparison pre/post 2022
  - `model_coefficients.csv` – OLS results with HC1 robust SE
  - `forecast_counterfactual_2022_2024.csv` – Actual vs. no-ruling trend forecasts
  - `model_residuals.csv` – Fitted values and residuals for diagnostics
  - `diagnostics_summary.csv` – Durbin-Watson, Ljung-Box, Breusch-Pagan, ADF stationarity
  - `sensitivity_exclude_2020.csv` – Robustness without pandemic year
  - **Figures:** Trend plots, residual scatter, Q-Q plots
  - `chapter_3_5_results_summary.md` – Scope and interpretation guide
- **Models:** Year-fixed-effects OLS for LGU proxies; interrupted-trend for national true-BUR
- **Controls:** `post_mandanas`, `annual_regular_income`, `transfer_dependency`, `local_revenue_ratio` (excludes sparse `poverty_incidence` to preserve n)

### 4. **R Models & Results** (`04_r_models/`)
- **File:** `04_revised_bur_methods_results_R.R`
- **Purpose:** Companion R implementation for cross-language validation and additional diagnostics
- **Inputs:** Method-input datasets
- **Outputs:**
  - `descriptive_statistics_R.csv` – Base R summary stats
  - `yearly_summary_R.csv` – Annual LGU means
  - `pre_post_comparison_R.csv` – Welch t-test and Wilcoxon p-values
  - `model_coefficients_R.csv` – LM and national BUR OLS results
  - `model_residuals_R.csv` – Fitted and residual series
  - `diagnostics_summary_R.csv` – Durbin-Watson, lag-1 autocorrelation
  - `forecast_counterfactual_R.csv` – Pre-2022 linear trend forecasts for 2022-2024
  - **Figures:** `trend_lgu_absorption_proxy_R.png` – Main trend visualization
  - `chapter_3_5_R_results_summary.md` – R-side scope summary

### 5. **MATLAB Diagnostics & Forecast** (`05_matlab_diagnostics/`)
- **File:** `05_revised_bur_diagnostics_forecast_matlab.m`
- **Purpose:** Wrapper and supplementary diagnostics for econometric quality assurance
- **Inputs:** Outputs from the main MATLAB diagnostics workflow
- **Outputs:**
  - `matlab_scope_guardrail.csv` – Explicit table of allowed/blocked model types
  - `matlab_residual_visual_summary.csv` – Residual mean, SD, max absolute residual
  - `matlab_forecast_gap_focus_2022_2024.csv` – 2022-2024 only actual vs. counterfactual gaps
  - **Figures:** Residual histograms per outcome
  - `matlab_chapter_3_5_scope_summary.md` – MATLAB interpretation guide
  - `wrapper_manifest.json` – Metadata and file locations

## Key Principles

1. **Honest Scope Labeling**
   - LGU models are **absorption/utilization proxies** (verified full expenditure / income), not official true-BUR
   - National models use **official COA true-BUR** at national-year scope
   - Always report proxy evidence separately from official true-BUR evidence

2. **Complete-Case Bias Prevention**
   - Sparse controls (e.g., `poverty_incidence`) are excluded from main models to preserve sample size
   - Missing controls are listed in output tables for transparency

3. **Readiness Gate**
   - LGU-year true-BUR models remain blocked pending validator upgrade
   - Readiness outputs are created before method inputs to prevent inadvertent true-BUR claims

4. **Timestamped Outputs**
   - Each script creates a new timestamped run folder in `RUNS_DIR / "analysis" / "runs"`
   - This preserves all previous thesis evidence without overwriting

## Running the Pipeline

### Prerequisites
- Python 3.9+ with `pandas`, `numpy`, `statsmodels`, `matplotlib`, `seaborn`
- R 4.0+ (base packages sufficient for the basic analysis)
- MATLAB R2021a+ (if running MATLAB diagnostics)

### Installation (Python)
```bash
pip install -r requirements.txt
```

### Execution Order
1. Ensure the latest `*_true_bur_thesis_validator` run exists
2. Run `01_recheck_revised_bur_dataset_readiness_spyder.py` (Spyder or command line)
3. Run `02_build_revised_bur_method_inputs_spyder.py`
4. Run `03_revised_bur_methods_results_python_spyder.py`
5. Run `04_revised_bur_methods_results_R.R` (in R or RStudio)
6. Run `05_revised_bur_diagnostics_forecast_matlab.m` (in MATLAB, if diagnostics are desired)

Each script prints its output folder path. Use those paths to feed into downstream analyses.

## File Locations

All scripts expect:
- **Base directory:** `D:/am/research_journals_pdf_library/data`
- **Runs folder:** `{BASE_DIR}/analysis/runs`
- **Validator input:** Latest `{RUNS_DIR}/*_true_bur_thesis_validator`
- **Method inputs:** Latest `{RUNS_DIR}/*_revised_bur_method_inputs` (or validator final/ as fallback)

Update path constants at the top of each script if your local directory structure differs.

## Outputs for Thesis Chapters 3–5

### Chapter 3: Methods
- Use `method_data_dictionary.csv` and `method_scope_note.md` for data description
- Use `descriptive_statistics.csv` and `yearly_summary_*.csv` for summary tables
- Use `pre_post_comparison.csv` for Mandanas-Garcia implementation effects

### Chapter 4: Results
- Use `model_coefficients.csv` for regression tables
- Use `forecast_counterfactual_*.csv` for counterfactual trend tables
- Use trend figures, residual plots, and Q-Q plots from `figures/`

### Chapter 5: Diagnostics & Limitations
- Use `diagnostics_summary.csv` for residual checks (autocorrelation, heteroscedasticity, stationarity)
- Use `sensitivity_exclude_2020.csv` for pandemic robustness
- Use `readiness_gate.csv` and `matlab_scope_guardrail.csv` to discuss allowed vs. blocked analyses
- Use `method_scope_note.md` and `chapter_3_5_results_summary.md` for honest scope discussion

## Related Files

- **Readiness outputs:** `{METHOD_DIR}/../*_revised_bur_dataset_readiness_recheck/`
- **Validator outputs:** `{RUNS_DIR}/*_true_bur_thesis_validator/`
  - `final/canonical_panel_2009_2024_for_thesis.csv`
  - `final/true_bur_national_2015_2024.csv`
  - `validated/dataset_sufficiency_decision.csv`
  - `validated/bur_scope_audit.csv`

## Contact & Attribution

These scripts implement the revised BUR analysis framework described in the thesis methodology. Questions about data sources, validator evidence, or model specifications should reference the validator outputs and method notes provided in each run folder.
