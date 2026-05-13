# Revised BUR R methods/results script.
#
# This script is the R-side companion to the Python pipeline. It loads the
# method-input datasets, produces descriptive tables, pre/post tests, LGU
# fiscal absorption proxy models, national true-BUR models, diagnostics,
# counterfactual forecasts, and one quick trend figure.

# Central project folders used for reading inputs and writing timestamped runs.
base_dir <- "D:/am/research_journals_pdf_library/data"
runs_dir <- file.path(base_dir, "analysis", "runs")
lgu_panel_core_controls <- c("post_mandanas", "annual_regular_income", "transfer_dependency", "local_revenue_ratio")
lgu_panel_excluded_controls <- c("poverty_incidence")

latest_dir <- function(pattern) {
  # Return the newest run directory whose folder name matches the requested
  # suffix/pattern. This lets the script pick up the latest method-input run.
  dirs <- list.dirs(runs_dir, recursive = FALSE, full.names = TRUE)
  hits <- dirs[grepl(pattern, basename(dirs))]
  if (length(hits) == 0) return(NA_character_)
  hits[order(hits, decreasing = TRUE)][1]
}

# Small helpers for safe numeric conversion and summary statistics.
safe_num <- function(x) suppressWarnings(as.numeric(x))
safe_mean <- function(x) mean(safe_num(x), na.rm = TRUE)
safe_sd <- function(x) sd(safe_num(x), na.rm = TRUE)

# Prefer the method-input run created by the Python builder. If it is not
# available, fall back to the latest validator output.
method_dir <- latest_dir("_revised_bur_method_inputs$")
if (is.na(method_dir)) {
  method_dir <- latest_dir("_true_bur_thesis_validator$")
}
if (is.na(method_dir)) stop("No method input or validator directory found.")

# Create a fresh output run so previous R results are preserved.
run_id <- format(Sys.time(), "%Y%m%d_%H%M%S")
out_dir <- file.path(runs_dir, paste0(run_id, "_revised_bur_R_methods_results"))
final_dir <- file.path(out_dir, "final")
validated_dir <- file.path(out_dir, "validated")
figures_dir <- file.path(out_dir, "figures")
logs_dir <- file.path(out_dir, "logs")
dir.create(final_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(validated_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(figures_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(logs_dir, recursive = TRUE, showWarnings = FALSE)

# Locate the input files. Method-input files already contain derived proxy
# variables; validator final files are used only as a fallback.
final_source <- file.path(method_dir, "final")
if (file.exists(file.path(final_source, "method_panel_2009_2024.csv"))) {
  panel_2024_path <- file.path(final_source, "method_panel_2009_2024.csv")
  panel_2021_path <- file.path(final_source, "method_panel_2009_2021.csv")
  national_2024_path <- file.path(final_source, "national_true_bur_2015_2024.csv")
  national_2021_path <- file.path(final_source, "national_true_bur_2015_2021.csv")
} else {
  panel_2024_path <- file.path(final_source, "canonical_panel_2009_2024_for_thesis.csv")
  panel_2021_path <- file.path(final_source, "canonical_panel_2009_2021_for_thesis.csv")
  national_2024_path <- file.path(final_source, "true_bur_national_2015_2024.csv")
  national_2021_path <- file.path(final_source, "true_bur_national_2015_2021.csv")
}

must_exist <- function(path) if (!file.exists(path)) stop(paste("Missing input:", path))
must_exist(panel_2024_path)
must_exist(panel_2021_path)
must_exist(national_2024_path)
must_exist(national_2021_path)

# Load LGU panel and national true-BUR inputs.
panel_2024 <- read.csv(panel_2024_path, stringsAsFactors = FALSE, check.names = FALSE)
panel_2021 <- read.csv(panel_2021_path, stringsAsFactors = FALSE, check.names = FALSE)
national_2024 <- read.csv(national_2024_path, stringsAsFactors = FALSE, check.names = FALSE)
national_2021 <- read.csv(national_2021_path, stringsAsFactors = FALSE, check.names = FALSE)

prepare_panel <- function(df) {
  # Standardize the LGU-year panel, rebuild verified full expenditure if
  # needed, create the LGU absorption proxy, and add the post-Mandanas flag.
  df$year <- safe_num(df$year)
  numeric_cols <- c("total_income", "total_expenditure", "verified_full_expenditure", "total_current_operating_expenditure", "total_non_operating_expenditures", "lgu_absorption_proxy", "appropriation_absorption_proxy", "annual_regular_income", "transfer_dependency", "transfer_dependency_final", "local_revenue_ratio", "local_revenue_ratio_final", "poverty_incidence")
  for (nm in intersect(numeric_cols, names(df))) df[[nm]] <- safe_num(df[[nm]])
  if (!("verified_full_expenditure" %in% names(df))) {
    if (all(c("total_current_operating_expenditure", "total_non_operating_expenditures") %in% names(df))) {
      df$verified_full_expenditure <- df$total_current_operating_expenditure + df$total_non_operating_expenditures
    } else {
      df$verified_full_expenditure <- df$total_expenditure
    }
  }
  if (!("lgu_absorption_proxy" %in% names(df))) df$lgu_absorption_proxy <- df$verified_full_expenditure / ifelse(df$total_income == 0, NA, df$total_income)
  if (!("transfer_dependency" %in% names(df)) && "transfer_dependency_final" %in% names(df)) df$transfer_dependency <- df$transfer_dependency_final
  if (!("local_revenue_ratio" %in% names(df)) && "local_revenue_ratio_final" %in% names(df)) df$local_revenue_ratio <- df$local_revenue_ratio_final
  df$post_mandanas <- ifelse(df$year >= 2022, 1, 0)
  df
}

prepare_nat <- function(df) {
  # Standardize the national COA true-BUR series and add time variables for the
  # national interrupted-trend model.
  df$year <- safe_num(df$year)
  if ("bur_true" %in% names(df)) df$bur_true <- safe_num(df$bur_true)
  if (!("coa_true_bur" %in% names(df))) df$coa_true_bur <- df$bur_true
  df$coa_true_bur <- safe_num(df$coa_true_bur)
  df$post_mandanas <- ifelse(df$year >= 2022, 1, 0)
  df$time_index <- seq_len(nrow(df))
  df$time_after_2021 <- ifelse(df$year >= 2022, df$year - 2021, 0)
  df
}

# Apply the preparation functions to both LGU windows and both national BUR
# windows before producing tables/models.
panel_2024 <- prepare_panel(panel_2024)
panel_2021 <- prepare_panel(panel_2021)
national_2024 <- prepare_nat(national_2024)
national_2021 <- prepare_nat(national_2021)

# Analysis variables: LGU fiscal outcomes/proxies and major fiscal-ratio
# controls. These are not LGU-level official true-BUR outcomes.
vars <- c("total_income", "verified_full_expenditure", "lgu_absorption_proxy", "appropriation_absorption_proxy", "transfer_dependency", "local_revenue_ratio")
vars <- vars[vars %in% names(panel_2024)]

# Descriptive statistics for the baseline and full observation windows.
desc_rows <- list()
for (dataset_name in c("panel_2009_2021", "panel_2009_2024")) {
  df <- if (dataset_name == "panel_2009_2021") panel_2021 else panel_2024
  for (v in vars) {
    x <- safe_num(df[[v]])
    desc_rows[[length(desc_rows) + 1]] <- data.frame(dataset = dataset_name, variable = v, n = sum(!is.na(x)), mean = mean(x, na.rm = TRUE), median = median(x, na.rm = TRUE), sd = sd(x, na.rm = TRUE), min = min(x, na.rm = TRUE), max = max(x, na.rm = TRUE))
  }
}
desc <- do.call(rbind, desc_rows)
write.csv(desc, file.path(final_dir, "descriptive_statistics_R.csv"), row.names = FALSE)

# Annual means used for trend and forecast tables.
years <- sort(unique(panel_2024$year))
yearly <- data.frame(year = years, n_lgus = sapply(years, function(y) sum(panel_2024$year == y)))
for (v in vars) yearly[[paste0("mean_", v)]] <- sapply(years, function(y) safe_mean(panel_2024[[v]][panel_2024$year == y]))
write.csv(yearly, file.path(final_dir, "yearly_summary_R.csv"), row.names = FALSE)

# Pre/post comparisons test whether post-2022 values differ from the pre-2022
# LGU panel distribution.
prepost_rows <- list()
for (v in vars) {
  pre <- safe_num(panel_2024[[v]][panel_2024$year <= 2021])
  post <- safe_num(panel_2024[[v]][panel_2024$year >= 2022])
  pre <- pre[!is.na(pre)]
  post <- post[!is.na(post)]
  p_t <- if (length(pre) > 1 && length(post) > 1) tryCatch(t.test(pre, post)$p.value, error = function(e) NA_real_) else NA_real_
  p_w <- if (length(pre) > 1 && length(post) > 1) tryCatch(wilcox.test(pre, post)$p.value, error = function(e) NA_real_) else NA_real_
  prepost_rows[[length(prepost_rows) + 1]] <- data.frame(variable = v, n_pre = length(pre), n_post = length(post), mean_pre = mean(pre), mean_post = mean(post), diff_post_minus_pre = mean(post) - mean(pre), welch_p = p_t, wilcoxon_p = p_w)
}
prepost <- do.call(rbind, prepost_rows)
write.csv(prepost, file.path(final_dir, "pre_post_comparison_R.csv"), row.names = FALSE)

fit_models <- function(df, label) {
  # Fit base R linear models for LGU proxy outcomes using available controls and
  # year fixed effects. Sparse controls are excluded so complete-case samples do
  # not collapse; these are proxy models, not official LGU true-BUR models.
  outcomes <- c("lgu_absorption_proxy", "transfer_dependency", "local_revenue_ratio")
  controls <- lgu_panel_core_controls
  rows <- list()
  resid_rows <- list()
  for (y in outcomes[outcomes %in% names(df)]) {
    rhs <- controls[controls %in% names(df) & controls != y]
    keep <- c(y, rhs, "year")
    d <- df[keep]
    d <- d[complete.cases(d), ]
    excluded_sparse <- paste(lgu_panel_excluded_controls[lgu_panel_excluded_controls %in% names(df)], collapse = ", ")
    predictor_text <- paste(c(rhs, "factor(year)"), collapse = " + ")
    if (nrow(d) < 30 || length(rhs) == 0) {
      rows[[length(rows) + 1]] <- data.frame(dataset = label, outcome = y, term = NA, estimate = NA, std_error = NA, p_value = NA, r_squared = NA, n = nrow(d), status = "insufficient_complete_cases", predictors = paste(rhs, collapse = " + "), excluded_sparse_controls = excluded_sparse)
      next
    }
    f <- as.formula(paste(y, "~", predictor_text))
    m <- tryCatch(lm(f, data = d), error = function(e) NULL)
    if (is.null(m)) next
    sm <- summary(m)
    rows[[length(rows) + 1]] <- data.frame(dataset = label, outcome = y, term = rownames(sm$coefficients), estimate = sm$coefficients[, 1], std_error = sm$coefficients[, 2], p_value = sm$coefficients[, 4], r_squared = sm$r.squared, n = nrow(d), status = "ok", predictors = predictor_text, excluded_sparse_controls = excluded_sparse)
    resid_rows[[length(resid_rows) + 1]] <- data.frame(dataset = label, outcome = y, year = d$year, fitted = fitted(m), residual = resid(m))
  }
  list(coefs = if (length(rows)) do.call(rbind, rows) else data.frame(), residuals = if (length(resid_rows)) do.call(rbind, resid_rows) else data.frame())
}

# Run the LGU proxy models separately for the 2009-2021 baseline panel and the
# 2009-2024 observed panel.
m2021 <- fit_models(panel_2021, "panel_2009_2021")
m2024 <- fit_models(panel_2024, "panel_2009_2024")

# National official true-BUR model. This is the valid true-BUR scope under the
# readiness gate.
nat_d <- national_2024[complete.cases(national_2024[, c("coa_true_bur", "time_index", "post_mandanas", "time_after_2021")]), ]
nat_coef <- data.frame()
nat_resid <- data.frame()
if (nrow(nat_d) >= 5) {
  nat_m <- lm(coa_true_bur ~ time_index + post_mandanas + time_after_2021, data = nat_d)
  nat_sm <- summary(nat_m)
  nat_coef <- data.frame(dataset = "national_true_bur_2015_2024", outcome = "coa_true_bur", term = rownames(nat_sm$coefficients), estimate = nat_sm$coefficients[, 1], std_error = nat_sm$coefficients[, 2], p_value = nat_sm$coefficients[, 4], r_squared = nat_sm$r.squared, n = nrow(nat_d), status = "ok", predictors = "time_index + post_mandanas + time_after_2021", excluded_sparse_controls = "")
  nat_resid <- data.frame(dataset = "national_true_bur_2015_2024", outcome = "coa_true_bur", year = nat_d$year, fitted = fitted(nat_m), residual = resid(nat_m))
}

# Save model coefficients and residuals for tables and diagnostics.
coefs <- rbind(m2021$coefs, m2024$coefs, nat_coef)
resids <- rbind(m2021$residuals, m2024$residuals, nat_resid)
write.csv(coefs, file.path(final_dir, "model_coefficients_R.csv"), row.names = FALSE)
write.csv(resids, file.path(validated_dir, "model_residuals_R.csv"), row.names = FALSE)

# Lightweight residual diagnostics implemented with base R.
dw_stat <- function(r) if (length(r) > 2) sum(diff(r)^2, na.rm = TRUE) / sum(r^2, na.rm = TRUE) else NA_real_
diag_rows <- list()
if (nrow(resids) > 0) {
  keys <- unique(paste(resids$dataset, resids$outcome, sep = "||"))
  for (key in keys) {
    parts <- strsplit(key, "\\|\\|")[[1]]
    d <- resids[paste(resids$dataset, resids$outcome, sep = "||") == key, ]
    r <- safe_num(d$residual)
    diag_rows[[length(diag_rows) + 1]] <- data.frame(dataset = parts[1], outcome = parts[2], diagnostic = "residual_mean", value = mean(r, na.rm = TRUE), p_value = NA, interpretation = "Near zero is expected")
    diag_rows[[length(diag_rows) + 1]] <- data.frame(dataset = parts[1], outcome = parts[2], diagnostic = "durbin_watson", value = dw_stat(r), p_value = NA, interpretation = "Near 2 suggests limited first-order autocorrelation")
    if (length(r) > 4) diag_rows[[length(diag_rows) + 1]] <- data.frame(dataset = parts[1], outcome = parts[2], diagnostic = "lag1_residual_autocorrelation", value = cor(r[-length(r)], r[-1], use = "complete.obs"), p_value = NA, interpretation = "Autocorrelation screen")
  }
}
diagnostics <- if (length(diag_rows)) do.call(rbind, diag_rows) else data.frame()
write.csv(diagnostics, file.path(validated_dir, "diagnostics_summary_R.csv"), row.names = FALSE)

# Counterfactual forecasts use pre-2022 linear trends and compare actual
# 2022-2024 outcomes with the no-ruling path.
forecast_rows <- list()
forecast_cols <- c("mean_lgu_absorption_proxy", "mean_transfer_dependency", "mean_local_revenue_ratio")
for (v in forecast_cols[forecast_cols %in% names(yearly)]) {
  d <- yearly[!is.na(yearly[[v]]), c("year", v)]
  names(d)[2] <- "actual"
  train <- d[d$year <= 2021, ]
  if (nrow(train) >= 4) {
    fm <- lm(actual ~ year, data = train)
    d$forecast_no_ruling <- predict(fm, newdata = d)
    d$gap_actual_minus_forecast <- d$actual - d$forecast_no_ruling
    d$outcome <- v
    forecast_rows[[length(forecast_rows) + 1]] <- d
  }
}
forecast <- if (length(forecast_rows)) do.call(rbind, forecast_rows) else data.frame()
write.csv(forecast, file.path(final_dir, "forecast_counterfactual_R.csv"), row.names = FALSE)

# Simple R visual check for the key LGU fiscal absorption proxy trend.
png(file.path(figures_dir, "trend_lgu_absorption_proxy_R.png"), width = 1000, height = 600)
if ("mean_lgu_absorption_proxy" %in% names(yearly)) {
  plot(yearly$year, yearly$mean_lgu_absorption_proxy, type = "b", xlab = "Year", ylab = "Mean LGU absorption proxy", main = "Mean LGU Fiscal Absorption Proxy")
  abline(v = 2022, col = "red", lty = 2)
}
dev.off()

# Write a short Markdown scope summary and a minimal JSON-like manifest.
summary_text <- "# Revised BUR R Results Summary\n\nLGU models use fiscal-ratio outcomes only: fiscal absorption proxy, transfer dependency, and local revenue ratio. Income and expenditure remain descriptive context only because the absorption proxy is expenditure divided by income. Official true BUR is analyzed at national-year scope. LGU-level true-BUR regressions are intentionally excluded under current readiness evidence.\n"
writeLines(summary_text, file.path(final_dir, "chapter_3_5_R_results_summary.md"))
manifest_text <- paste0(
  "{\n",
  "  \"created_at\": \"", as.character(Sys.time()), "\",\n",
  "  \"script\": \"04_revised_bur_methods_results_R.R\",\n",
  "  \"method_input_dir\": \"", gsub("\\\\", "/", method_dir), "\",\n",
  "  \"output_dir\": \"", gsub("\\\\", "/", out_dir), "\",\n",
  "  \"rows\": {\n",
  "    \"panel_2024\": ", nrow(panel_2024), ",\n",
  "    \"panel_2021\": ", nrow(panel_2021), ",\n",
  "    \"national_2024\": ", nrow(national_2024), "\n",
  "  }\n",
  "}\n"
)
writeLines(manifest_text, file.path(logs_dir, "manifest.json"))
cat("R methods/results complete.\n")
cat("Output directory:", out_dir, "\n")
