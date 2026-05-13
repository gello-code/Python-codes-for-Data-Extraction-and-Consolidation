% Wrapper around the main MATLAB diagnostics workflow.
%
% The main file `revised_bur_diagnostics_forecast.m` creates the core tables
% and figures. This wrapper runs that workflow, finds its newest output folder,
% and adds extra scope guardrails, focused 2022-2024 forecast-gap files, and
% residual histogram summaries for thesis review.

clear;
clc;
close all;

% Project paths and pointer to the main MATLAB workflow.
base_dir = 'D:/am/research_journals_pdf_library/data';
scripts_dir = fullfile(base_dir, 'analysis', 'scripts');
runs_dir = fullfile(base_dir, 'analysis', 'runs');
base_workflow = fullfile(scripts_dir, 'revised_bur_diagnostics_forecast.m');
if ~isfile(base_workflow)
    error('Base MATLAB diagnostics workflow not found: %s', base_workflow);
end

% Run the main diagnostics workflow first. It creates the timestamped
% `_revised_bur_matlab_diagnostics` output folder.
run(base_workflow);
base_workflow = fullfile('D:/am/research_journals_pdf_library/data', 'analysis', 'scripts', 'revised_bur_diagnostics_forecast.m');

% Locate the newest output from the workflow that just ran.
latest_dir = resolveLatestRun(runs_dir, '_revised_bur_matlab_diagnostics');
if strlength(latest_dir) == 0
    error('No revised BUR MATLAB diagnostics output directory found after base workflow run.');
end

final_dir = fullfile(latest_dir, 'final');
validated_dir = fullfile(latest_dir, 'validated');
figures_dir = fullfile(latest_dir, 'figures');
logs_dir = fullfile(latest_dir, 'logs');
ensureDir(final_dir);
ensureDir(validated_dir);
ensureDir(figures_dir);
ensureDir(logs_dir);

% Write a clear guardrail table for readers: LGU official true-BUR models are
% blocked, LGU proxy models are allowed, and national true-BUR models are valid.
scope = table();
scope.item = {'lgu_true_bur_models'; 'lgu_absorption_proxy_models'; 'national_true_bur_models'; 'recommended_thesis_scope'};
scope.status = {'not_allowed'; 'allowed_with_proxy_label'; 'allowed'; 'honest_scope_required'};
scope.note = { ...
    'Current validator evidence does not support LGU-year official true BUR regressions.'; ...
    'Use verified full expenditure / total income as fiscal absorption or utilization proxy only.'; ...
    'Use COA national true BUR as national-year evidence.'; ...
    'Report LGU proxy evidence separately from national true-BUR evidence.'};
writetable(scope, fullfile(final_dir, 'matlab_scope_guardrail.csv'));

% If residuals exist, summarize them and generate quick histogram figures for
% model-quality review.
residual_path = fullfile(validated_dir, 'model_residuals.csv');
if isfile(residual_path)
    residuals = readtable(residual_path, 'VariableNamingRule', 'preserve');
    residual_summary = buildResidualSummary(residuals);
    writetable(residual_summary, fullfile(validated_dir, 'matlab_residual_visual_summary.csv'));
    makeResidualHistogramFigures(residuals, figures_dir);
end

% Keep a focused post-Mandanas forecast-gap table for 2022-2024 only.
forecast_path = fullfile(final_dir, 'forecast_counterfactual_2022_2030.csv');
if isfile(forecast_path)
    forecast_tbl = readtable(forecast_path, 'VariableNamingRule', 'preserve');
    post_gap = forecast_tbl(forecast_tbl.year >= 2022 & forecast_tbl.year <= 2024, :);
    writetable(post_gap, fullfile(final_dir, 'matlab_forecast_gap_focus_2022_2024.csv'));
end

% Write a short Markdown summary explaining how the MATLAB outputs should be
% interpreted in Chapters 3-5.
summary_path = fullfile(final_dir, 'matlab_chapter_3_5_scope_summary.md');
summary_text = [ ...
    "# MATLAB Revised BUR Diagnostics Summary" + newline + newline + ...
    "LGU-year official true BUR models remain excluded. MATLAB diagnostics support the revised thesis by validating LGU fiscal absorption proxy forecasts and national true-BUR trend evidence separately." + newline + newline + ...
    "Use `model_specification_summary.csv`, `model_performance_summary.csv`, `actual_vs_no_ruling_gap_2022_2024.csv`, `figure_index.csv`, and the ordered forecast/gap figures for Chapters 3-5. Income and expenditure are descriptive context only, not main modeled outcomes." + newline];
writeText(summary_path, char(summary_text));

% Record wrapper-level metadata without changing the main workflow manifest.
manifest = struct();
manifest.created_at = char(datetime('now', 'Format', 'yyyy-MM-dd HH:mm:ss'));
manifest.script = '05_revised_bur_diagnostics_forecast_matlab.m';
manifest.base_workflow = base_workflow;
manifest.output_dir = latest_dir;
manifest.scope_guardrail = fullfile(final_dir, 'matlab_scope_guardrail.csv');
writeText(fullfile(logs_dir, 'wrapper_manifest.json'), jsonencode(manifest, 'PrettyPrint', true));

fprintf('MATLAB revised BUR wrapper complete.\n');
fprintf('Output directory: %s\n', latest_dir);

function latest = resolveLatestRun(runs_dir, suffix)
    % Find the newest run folder whose name ends with the requested suffix.
    listing = dir(runs_dir);
    names = strings(0, 1);
    fulls = strings(0, 1);
    for i = 1:numel(listing)
        if listing(i).isdir && ~startsWith(listing(i).name, '.') && endsWith(listing(i).name, suffix)
            names(end + 1, 1) = string(listing(i).name);
            fulls(end + 1, 1) = string(fullfile(runs_dir, listing(i).name));
        end
    end
    if isempty(names)
        latest = "";
        return;
    end
    [~, idx] = sort(names, 'descend');
    latest = char(fulls(idx(1)));
end

function ensureDir(path_value)
    % Create a folder if it does not already exist.
    if ~exist(path_value, 'dir')
        mkdir(path_value);
    end
end

function out = buildResidualSummary(residuals)
    % Summarize residual center, spread, and largest absolute residual by
    % dataset/outcome combination.
    if ~all(ismember({'dataset', 'outcome', 'residual'}, residuals.Properties.VariableNames))
        out = table();
        return;
    end
    key = strcat(string(residuals.dataset), "||", string(residuals.outcome));
    keys = unique(key);
    dataset = strings(numel(keys), 1);
    outcome = strings(numel(keys), 1);
    n = zeros(numel(keys), 1);
    residual_mean = zeros(numel(keys), 1);
    residual_std = zeros(numel(keys), 1);
    max_abs_residual = zeros(numel(keys), 1);
    for i = 1:numel(keys)
        mask = key == keys(i);
        parts = split(keys(i), "||");
        dataset(i) = parts(1);
        outcome(i) = parts(2);
        r = residuals.residual(mask);
        r = r(~isnan(r));
        n(i) = numel(r);
        residual_mean(i) = mean(r, 'omitnan');
        residual_std(i) = std(r, 'omitnan');
        max_abs_residual(i) = max(abs(r), [], 'omitnan');
    end
    out = table(dataset, outcome, n, residual_mean, residual_std, max_abs_residual);
end

function makeResidualHistogramFigures(residuals, figures_dir)
    % Save one histogram per outcome so residual distributions are easy to
    % inspect visually.
    if ~all(ismember({'dataset', 'outcome', 'residual'}, residuals.Properties.VariableNames))
        return;
    end
    key = strcat(string(residuals.dataset), "||", string(residuals.outcome));
    keys = unique(key);
    for i = 1:numel(keys)
        mask = key == keys(i);
        r = residuals.residual(mask);
        r = r(~isnan(r));
        if isempty(r)
            continue;
        end
        safe_name = regexprep(char(keys(i)), '[^A-Za-z0-9_-]', '_');
        fig = figure('Visible', 'off');
        histogram(r, 20);
        title(['Residual Distribution: ' char(keys(i))], 'Interpreter', 'none');
        xlabel('Residual');
        ylabel('Frequency');
        saveas(fig, fullfile(figures_dir, ['residual_histogram_' safe_name '.png']));
        close(fig);
    end
end

function writeText(path_value, text_value)
    % Small file-writing helper used for Markdown and JSON manifest outputs.
    fid = fopen(path_value, 'w');
    if fid < 0
        error('Unable to write file: %s', path_value);
    end
    cleaner = onCleanup(@() fclose(fid));
    fprintf(fid, '%s', text_value);
end
