import os
import sys

from openai import OpenAI
from llm_eval.core import load_config, safe_error

required = ("OPENAI_API_KEY", "DATABRICKS_HOST", "DATABRICKS_WAREHOUSE_ID")
missing = [name for name in required if not os.getenv(name)]
if not os.getenv("DATABRICKS_TOKEN") and not (os.getenv("DATABRICKS_CLIENT_ID") and os.getenv("DATABRICKS_CLIENT_SECRET")):
    missing.append("DATABRICKS_TOKEN or DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET")
if missing:
    print("Configure repository secrets: " + ", ".join(missing))
    sys.exit(2)
try:
    client = OpenAI(timeout=30,max_retries=1)
    config = load_config()
    for model in dict.fromkeys(config["models"]+[config["judge_model"]]):
        client.models.retrieve(model)
        print("Model accessible: " + model)
    client.close()
except Exception as exc:
    print("Model access check failed: " + safe_error(exc))
    sys.exit(2)
