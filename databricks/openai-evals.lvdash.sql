-- overview
WITH latest AS (SELECT batch_id, dataset_sha256, config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode = 'live' ORDER BY event_time DESC, batch_id DESC LIMIT 1), suites AS (
 SELECT max(r.batch_id) AS batch_id, max(r.event_time) AS event_time,
        count(*) AS received_suites, 15 AS expected_suites,
        sum(r.failed) AS failed_cases, sum(r.errors) AS error_cases
 FROM `workspace`.`default`.`eval_runs` r JOIN latest b ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256 AND r.config_sha256=b.config_sha256
), gen AS (
 SELECT count(*) AS received_generations, 36 AS expected_generations,
        count_if(r.status='error') AS generation_errors
 FROM `workspace`.`default`.`eval_generations` r JOIN latest b ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256 AND r.config_sha256=b.config_sha256
)
SELECT s.*,g.received_generations,g.expected_generations,
       CASE WHEN s.received_suites < 15 OR g.received_generations < 36 THEN 'INCOMPLETE'
            WHEN s.failed_cases+s.error_cases+g.generation_errors > 0 THEN 'FAILED' ELSE 'PASSED' END AS batch_status
FROM suites s CROSS JOIN gen g;

-- comparison
WITH latest AS (SELECT batch_id, dataset_sha256, config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode = 'live' ORDER BY event_time DESC, batch_id DESC LIMIT 1),
models AS (SELECT * FROM VALUES ('gpt-6.1-terra'),('gpt-6.1-sol'),('gpt-6-luna') AS m(model)),
frameworks AS (SELECT * FROM VALUES ('deepeval','correctness'),('ragas','faithfulness'),('promptfoo','assertions'),('trulens','answer_relevance'),('inspect','exact_match') AS f(framework, metric))
SELECT m.model, f.framework, f.metric, r.mean_score, round(100*r.pass_rate,2) AS pass_rate_pct,
       r.eligible, r.skipped, r.failed, r.errors, round(100*r.coverage,2) AS coverage_pct,
       CASE WHEN r.framework IS NULL THEN 'PENDING'
            WHEN r.errors > 0 THEN 'ERROR' WHEN r.failed > 0 THEN 'FAILED' ELSE 'PASSED' END AS gate_status
FROM models m CROSS JOIN frameworks f CROSS JOIN latest b
LEFT JOIN `workspace`.`default`.`eval_runs` r ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256
  AND r.config_sha256=b.config_sha256 AND r.model=m.model AND r.framework=f.framework
ORDER BY f.framework,m.model;

-- failures
WITH latest AS (SELECT batch_id, dataset_sha256, config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode = 'live' ORDER BY event_time DESC, batch_id DESC LIMIT 1)
SELECT r.model, r.framework, r.case_id, r.category, r.status, r.score, r.threshold, r.reason
FROM `workspace`.`default`.`eval_cases` r JOIN latest b ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256 AND r.config_sha256=b.config_sha256 WHERE r.status IN ('failed','error') ORDER BY r.framework,r.model,r.case_id;

-- generation
WITH latest AS (SELECT batch_id, dataset_sha256, config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode = 'live' ORDER BY event_time DESC, batch_id DESC LIMIT 1)
SELECT r.model, count(*) AS generation_attempts,
       count_if(r.status='error') AS generation_errors,
       round(avg(CASE WHEN r.status='ok' THEN r.duration_seconds END),3) AS avg_latency_s,
       round(percentile_approx(CASE WHEN r.status='ok' THEN r.duration_seconds END,0.95),3) AS p95_latency_s,
       sum(r.input_tokens) AS input_tokens, sum(r.output_tokens) AS output_tokens,
       sum(r.reasoning_tokens) AS reasoning_tokens, count_if(NOT r.usage_known) AS unknown_usage_cases
FROM `workspace`.`default`.`eval_generations` r JOIN latest b ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256 AND r.config_sha256=b.config_sha256 GROUP BY r.model ORDER BY r.model;

-- judge_usage
WITH latest AS (SELECT batch_id, dataset_sha256, config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode = 'live' ORDER BY event_time DESC, batch_id DESC LIMIT 1)
SELECT r.model, r.framework, r.judge_model, r.judge_requests,
       r.judge_input_tokens, r.judge_output_tokens, r.mlflow_run_id, r.git_branch, r.commit_sha, r.ci_url
FROM `workspace`.`default`.`eval_runs` r JOIN latest b ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256 AND r.config_sha256=b.config_sha256 ORDER BY r.framework,r.model;

-- all_cases
WITH latest AS (SELECT batch_id, dataset_sha256, config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode = 'live' ORDER BY event_time DESC, batch_id DESC LIMIT 1)
SELECT r.model,r.framework,r.case_id,r.category,r.status,r.score,r.threshold,r.duration_seconds,r.reason
FROM `workspace`.`default`.`eval_cases` r JOIN latest b ON r.batch_id=b.batch_id AND r.dataset_sha256=b.dataset_sha256 AND r.config_sha256=b.config_sha256 ORDER BY r.framework,r.model,r.case_id;

-- history_deepeval
SELECT event_time,model,mean_score,pass_rate,coverage,batch_id,failed,errors,git_branch
FROM `workspace`.`default`.`eval_runs` WHERE mode='live' AND framework='deepeval'
 AND dataset_sha256=(SELECT dataset_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND config_sha256=(SELECT config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND event_time >= current_timestamp() - INTERVAL 30 DAYS ORDER BY event_time;

-- history_ragas
SELECT event_time,model,mean_score,pass_rate,coverage,batch_id,failed,errors,git_branch
FROM `workspace`.`default`.`eval_runs` WHERE mode='live' AND framework='ragas'
 AND dataset_sha256=(SELECT dataset_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND config_sha256=(SELECT config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND event_time >= current_timestamp() - INTERVAL 30 DAYS ORDER BY event_time;

-- history_promptfoo
SELECT event_time,model,mean_score,pass_rate,coverage,batch_id,failed,errors,git_branch
FROM `workspace`.`default`.`eval_runs` WHERE mode='live' AND framework='promptfoo'
 AND dataset_sha256=(SELECT dataset_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND config_sha256=(SELECT config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND event_time >= current_timestamp() - INTERVAL 30 DAYS ORDER BY event_time;

-- history_trulens
SELECT event_time,model,mean_score,pass_rate,coverage,batch_id,failed,errors,git_branch
FROM `workspace`.`default`.`eval_runs` WHERE mode='live' AND framework='trulens'
 AND dataset_sha256=(SELECT dataset_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND config_sha256=(SELECT config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND event_time >= current_timestamp() - INTERVAL 30 DAYS ORDER BY event_time;

-- history_inspect
SELECT event_time,model,mean_score,pass_rate,coverage,batch_id,failed,errors,git_branch
FROM `workspace`.`default`.`eval_runs` WHERE mode='live' AND framework='inspect'
 AND dataset_sha256=(SELECT dataset_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND config_sha256=(SELECT config_sha256 FROM `workspace`.`default`.`eval_runs` WHERE mode='live' ORDER BY event_time DESC LIMIT 1)
 AND event_time >= current_timestamp() - INTERVAL 30 DAYS ORDER BY event_time;
