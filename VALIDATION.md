# Validation record — 29 October 2026

- Fresh Python 3.12 environment installed from `uv.lock`; Node dependencies installed from `package-lock.json`.
- Final suite: **33 offline tests passed in 13.46 seconds**.
- All five native framework adapters exercised. LLM judges used explicitly scripted fixture payloads; these are not live model evaluations.
- Native Promptfoo and Inspect assertions checked both correct and incorrect fixture answers.
- Error propagation, incomplete responses, score validity, dataset/configuration matching, fixture upload rejection, and removal of stale reports checked.
- Delta row schemas, SQL parameter binding, idempotent merge definitions, and concurrent-write retry behavior checked.
- Dashboard definitions round-tripped through the typed Lakeview schema; generated SQL parsed as the Databricks dialect.
- Repeated dashboard deployment reuses its ID, checks the current version before updating, and requires an explicit ID when names are ambiguous. Publication preserves viewer credential requirements.
- The default catalog/schema is `workspace.default`, as confirmed in Catalog Explorer; environment overrides remain supported.
- GitHub Actions YAML and Python/JSON source syntax parsed successfully; `uv lock --check` passed.
- MLflow artifact logging and upload-retry run reuse checked against a temporary local SQLite tracking backend.
- Promptfoo replay's network guard separately verified to reject socket, HTTPS, and fetch requests locally. Target and tracking credentials are removed from that subprocess's environment.

**Not executed:** live OpenAI inference or grading, remote MLflow publication, Delta table writes, dashboard deployment, and dashboard rendering in a Databricks workspace. No OpenAI or Databricks account credentials were available. No model ranking, live score, or dashboard URL is claimed by this validation record.
