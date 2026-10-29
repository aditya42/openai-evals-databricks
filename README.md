# OpenAI evaluations in Databricks

A runnable pilot that evaluates three current OpenAI models with five open source frameworks and reports results in **Databricks AI/BI dashboards**, with **MLflow experiments** and **Unity Catalog Delta tables**.

**Delivery status:** integration and offline fixture tests are implemented. Live OpenAI evaluations have not been run, and no dashboard has been deployed to a workspace. This environment had no OpenAI or Databricks credentials. No benchmark scores are supplied as measured results.

## Quick start in this repository

1. Add the four repository secrets at [Actions secrets](https://github.com/aditya42/openai-evals-databricks/settings/secrets/actions): `OPENAI_API_KEY`, `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and `DATABRICKS_WAREHOUSE_ID`. OAuth M2M credentials can replace the token.
2. Open [the evaluation workflow](https://github.com/aditya42/openai-evals-databricks/actions/workflows/evaluate.yml) and choose **Run workflow** on `main`.
3. Open the Databricks dashboard link in the prepare job summary. Refresh it as the 15 evaluation suites finish publishing.

The default destination is `workspace.default`; table names are `eval_runs`, `eval_cases`, and `eval_generations`. Offline validation runs on every push and needs no account credentials.

## Model selection

Verified against official OpenAI documentation on **28 October 2026**. “Latest three” is interpreted as the newest currently maintained general-purpose model lines, using the latest Sol revision rather than the superseded GPT-6 Sol.

| Model under test | Release | Purpose in this comparison |
|---|---|---|
| `gpt-6.1-terra` | 26 October 2026 | Visual reasoning, computer use, writing; this pilot tests text only |
| `gpt-6.1-sol` | 29 September 2026 | Complex reasoning and professional work |
| `gpt-6-luna` | 22 September 2026 | Efficient, high-volume workloads |

All target requests use the **Responses API**, the same `low` reasoning effort, and the same output-token limit. Account access is checked before CI execution; no inaccessible model is silently replaced. Model aliases may evolve: the response's resolved model ID is also retained. For longer-term regression tracking, replace aliases with supported snapshots after checking your account and official documentation.

`gpt-6-astra` is the fixed judge for DeepEval, Ragas, and TruLens. It is separate from the three models being compared. Judge token usage is reported independently. Framework judge prompts and scoring stay native; transport adapters use Responses structured outputs and omit unsupported temperature and log-probability arguments. DeepEval's custom judge uses its score-based GEval path without log-probability weighting.

## Five frameworks

| Framework | Native evaluator used | Eligible cases per model | Gate |
|---|---|---:|---:|
| [Promptfoo](https://github.com/promptfoo/promptfoo) | CLI assertions: exact text, regex, substring, JSON schema | 12 | All assertions pass |
| [DeepEval](https://github.com/confident-ai/deepeval) | `GEval` correctness against reference and instructions | 12 | Score ≥ 0.80 for every case |
| [Ragas](https://github.com/vibrantlabsai/ragas) | Collections `Faithfulness`, checking claims against supplied context | 4 | Score ≥ 0.90 for every eligible case |
| [TruLens](https://github.com/truera/trulens) | `relevance_with_cot_reasons` | 10 | Score ≥ 0.80 for every eligible case |
| [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) | Built-in `match(location="exact")` scorer | 6 | Exact answer, ignoring surrounding whitespace |

There are **15 framework/model suites, 36 target generations, and 132 eligible checks** per full batch. Reports also retain 48 explicit skips. Ragas runs only where a retrieved context exists. TruLens relevance excludes the two refusal/injection cases, where following a refusal policy is not well captured by a generic relevance score. Inspect runs only where an exact textual target exists.

Each model response is generated **once**, then reused by every applicable framework. Native framework scores have different meanings; the dashboard does **not** average them into an overall model score. Pass rates include evaluation errors in their denominator, and coverage is shown alongside average scores. A missing, nonfinite, or out-of-range score becomes an error.

Promptfoo's replay subprocess disables telemetry, update checks, and remote generation. A Node preload guard rejects HTTP, HTTPS, fetch, and socket connections before they open: these cached deterministic assertions need no network access. Live target and judge calls occur only through the explicit OpenAI runner.

The 12 synthetic cases cover four grounded policy questions, arithmetic, an API contract, sentiment classification, two JSON contracts, and two prompt-rule/canary checks. This is an integration pilot, not a statistically meaningful model ranking or a comprehensive security benchmark. Add representative application cases and repeated trials before using it to choose a production model. The fixed judge should also be calibrated against human labels.

## Dashboard

The native dashboard definition is [databricks/openai-evals.lvdash.json](databricks/openai-evals.lvdash.json), generated by `llm_eval/dashboard.py`.

Its two pages provide:

- A 5 × 3 comparison table with score, pass rate, eligible count, skips, failures, errors, and coverage.
- Batch completeness: missing suite uploads show `PENDING`; incomplete batches never show `PASSED`.
- Generation mean/p95 latency, token counts, reasoning tokens, errors, and unknown-usage counts, counted once per model response.
- Failed-case details and reasons, plus a table of every result including skips.
- Five separate 30-day score trends, one per framework, grouped by model and restricted to the latest dataset/configuration hashes.
- Separate judge request/token usage, MLflow run IDs, Git branch/commit, and CI URLs.

The latest received batch is shown even if partially uploaded. Refresh the dashboard after a new run. The generated SQL is available in `databricks/openai-evals.lvdash.sql` for review or modification in the dashboard editor.

## Install

Use Python 3.12, Node.js 24, and [uv](https://docs.astral.sh/uv/). Run from this checkout:

```bash
uv sync --locked --all-extras
npm ci --no-audit --no-fund
uv run --no-sync python -m pytest
```

Python and Node dependencies are locked in `uv.lock` and `package-lock.json`. CI installs each framework separately. Ragas's compatible `langchain-community==0.3.31` is deliberately pinned; the newer community package removed a module imported by Ragas 0.4.3.

## Connect Databricks and deploy the dashboard

Use an existing Unity Catalog catalog and a SQL warehouse. The execution identity needs permission to use that warehouse, create/write the result schema/tables, create an MLflow experiment, and create a dashboard in the selected workspace folder. Dashboard viewers need access to the underlying tables and warehouse because deployment does not embed the publisher's credentials.

Configure these environment variables (see `.env.example`; `.env` files are not automatically loaded):

| Variable | Required value |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key with access to the targets and judge |
| `DATABRICKS_HOST` | Your workspace URL, including `https://` |
| `DATABRICKS_TOKEN` | A PAT; alternatively use `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` for OAuth M2M |
| `DATABRICKS_WAREHOUSE_ID` | SQL warehouse ID, not the HTTP path |
| `DATABRICKS_CATALOG` | Existing catalog; default `workspace` |
| `DATABRICKS_SCHEMA` | Result schema; default `default` |
| `MLFLOW_EXPERIMENT_NAME` | Default `/Shared/openai-llm-evals` |
| `DATABRICKS_DASHBOARD_PARENT` | Existing workspace folder; default `/Shared` |

Then run:

```bash
uv run --no-sync python -m llm_eval.backend bootstrap
uv run --no-sync python -m llm_eval.dashboard deploy
```

The second command builds the definition for your catalog/schema, creates or updates the matching native dashboard in the configured folder, publishes it with `embed_credentials=False`, and prints its URL. Later deployments reuse that dashboard and check its version tag before updating it. If more than one dashboard matches, set `DATABRICKS_DASHBOARD_ID` to choose the intended one. Existing dashboard permissions are retained. The existing parent folder must permit creation. The `build` command creates only local files and requires no credentials:

```bash
uv run --no-sync python -m llm_eval.dashboard build
```

## Run the evaluations

Run the complete batch and publish its results:

```bash
uv run --no-sync python scripts/run_local.py --publish
```

Omit `--publish` to keep live evaluation results local. The command generates responses once per model, runs all five frameworks, and continues collecting results after failures. It exits nonzero for a generation error, failed quality gate, or publishing failure. Target requests and judge requests both use your OpenAI API account; the Astra judge can dominate API usage. Token limits and timeouts are configurable in `config.json`.

To run one framework against an existing, matching response artifact:

```bash
uv run --no-sync python -m llm_eval evaluate \
  --framework ragas --model gpt-6.1-sol \
  --responses responses/gpt-6.1-sol.json \
  --out reports/ragas/gpt-6.1-sol

uv run --no-sync python -m llm_eval.backend publish-results reports/ragas/gpt-6.1-sol/results.json
```

Responses are checked against model, dataset hash, configuration hash, and case IDs before reuse. If manually generating multiple models for one batch, set the same `LLM_EVAL_BATCH_ID` for all three; the full-batch script and GitHub Actions workflow handle that automatically.

## GitHub Actions

`.github/workflows/validate.yml` runs offline tests on pushes and pull requests, using scripted judge payloads and native deterministic framework checks. Those fixtures are marked `fixture`, and the Databricks publisher refuses them.

`.github/workflows/evaluate.yml` runs live evaluations only when manually launched with **Actions → Evaluate OpenAI models and publish to Databricks → Run workflow**. Configure these repository secrets:

- `OPENAI_API_KEY`
- `DATABRICKS_HOST`
- `DATABRICKS_WAREHOUSE_ID`
- `DATABRICKS_TOKEN`, or both `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET`

The repository is configured for catalog **`workspace`** and schema **`default`**, matching your Catalog Explorer. These remain the defaults when repository variables are absent. Optional overrides are `DATABRICKS_CATALOG`, `DATABRICKS_SCHEMA`, `MLFLOW_EXPERIMENT_NAME`, `DATABRICKS_DASHBOARD_PARENT`, and `DATABRICKS_DASHBOARD_ID`.

The prepare job creates the result tables, creates or updates the Databricks dashboard, and places its URL in the GitHub Actions run summary. Dashboard deployment metadata is retained as an artifact. Three generation jobs then feed 15 framework/model jobs. Publication and report retention run even after quality failures. Results are uploaded to MLflow and Delta before the workflow ends; a failed evaluation still fails CI. Missing artifacts or failed uploads remain visible as an incomplete dashboard batch. Generation artifacts are retained for seven days; evaluation artifacts for fourteen.

## Data layout

| Delta table | Grain | Purpose |
|---|---|---|
| `eval_generations` | Batch + model + case | Generation latency, tokens, errors, resolved model |
| `eval_cases` | Batch + model + framework + case | Score, threshold, status, reason, applicability |
| `eval_runs` | Batch + model + framework | Aggregate metrics, hashes, judge usage, MLflow and Git metadata |

Tables are written with parameterized SQL `MERGE` operations through the Databricks Statement Execution API. Re-uploading a report updates the same natural keys. MLflow upload retries reuse the same run. Case details are written before the run summary, so a summary marks a completed upload. No raw prompt or model output text is copied to the Delta tables; raw responses remain in the generation artifact. MLflow receives result JSON and JUnit artifacts.

## Validation and limits

Offline checks exercise all five native framework adapters, successful and unsuccessful assertions, generation errors, incomplete responses, invalid scores, mismatched artifacts, fixture rejection, SQL parameter binding, and the typed dashboard schema. The dashboard SQL is parsed as Databricks SQL. These checks do not establish live OpenAI access, judge quality, workspace permissions, SQL execution, or dashboard rendering in your account; those require your configured accounts.

## Primary documentation

- [OpenAI model catalog](https://developers.openai.com/api/docs/models) and [release changelog](https://developers.openai.com/api/docs/changelog)
- [OpenAI model/API parameter compatibility](https://developers.openai.com/api/docs/guides/latest-model)
- [Databricks dashboard create/publish API](https://docs.databricks.com/aws/en/dashboards/tutorials/dashboard-crud-api)
- [Databricks MLflow tracking](https://docs.databricks.com/aws/en/mlflow/tracking)
- [Databricks SQL Statement Execution API](https://docs.databricks.com/aws/en/dev-tools/sql-execution-tutorial)
- [DeepEval GEval](https://deepeval.com/docs/metrics-llm-evals)
- [Ragas Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)
- [Promptfoo custom providers](https://www.promptfoo.dev/docs/providers/custom-api/)
- [TruLens evaluation provider](https://www.trulens.org/reference/trulens/providers/openai/provider/)
- [Inspect scoring](https://inspect.aisi.org.uk/scoring.html)

The Databricks Labs `lsql` dependency is used for the dashboard's typed serialization schema; deployment and SQL execution use the official Databricks SDK.
