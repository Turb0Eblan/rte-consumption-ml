import sys
from pathlib import Path

# the job runs this file directly, so make src/ importable
# Databricks runs this file with exec(), where __file__ is not defined,
# but sys.argv[0] still holds the script path
ROOT = Path(sys.argv[0]).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rte_consumption_ml.ml import predict, select_best, train_search

STEPS = {
    "search": train_search.main,
    "select": select_best.main,
    "predict": predict.main,
}

if __name__ == "__main__":
    step = sys.argv[1]          # job passes the step name as a parameter
    STEPS[step]()
