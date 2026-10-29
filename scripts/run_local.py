"""Run one full three-model batch locally, optionally uploading it to Databricks."""
import argparse
import os
import subprocess
import sys
import uuid

from llm_eval.core import FRAMEWORKS, ROOT, load_config

parser=argparse.ArgumentParser()
parser.add_argument("--publish",action="store_true")
args=parser.parse_args()
if not os.getenv("OPENAI_API_KEY"):
    raise SystemExit("Set OPENAI_API_KEY before running live evaluations")
env={**os.environ,"LLM_EVAL_BATCH_ID":str(uuid.uuid4())}
failures=[]

def command(*args):
    result=subprocess.run([sys.executable,*args],cwd=ROOT,env=env)
    if result.returncode:failures.append(list(args))
    return result.returncode

if args.publish and command("-m","llm_eval.backend","bootstrap"):
    raise SystemExit("Databricks setup failed")
config=load_config()
print("Evaluation batch: "+env["LLM_EVAL_BATCH_ID"],flush=True)
for model in config["models"]:
    response=ROOT/"responses"/(model+".json")
    response.unlink(missing_ok=True)
    command("-m","llm_eval","generate","--model",model,"--output",str(response))
    if not response.is_file():continue
    if args.publish:command("-m","llm_eval.backend","publish-generation",str(response))
    for framework in FRAMEWORKS:
        output=ROOT/"reports"/framework/model
        command("-m","llm_eval","evaluate","--model",model,"--framework",framework,"--responses",str(response),"--out",str(output))
        if args.publish and (output/"results.json").is_file():
            command("-m","llm_eval.backend","publish-results",str(output/"results.json"))
if failures:
    print(f"{len(failures)} generation, quality-gate, or publishing steps failed; see results and command output.")
    raise SystemExit(1)
print("All evaluation gates passed" + ("; results published to Databricks" if args.publish else ""))
