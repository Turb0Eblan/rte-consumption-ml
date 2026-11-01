import os

# Databricks sets this variable on its compute
ON_DATABRICKS = "DATABRICKS_RUNTIME_VERSION" in os.environ

DEFAULT_MODELS_DIR = "/Volumes/workspace/gold/models" if ON_DATABRICKS else "models"

# can be overridden with an environment variable
MODELS_DIR = os.getenv("MODELS_DIR", DEFAULT_MODELS_DIR)

DEFAULT_EXPERIMENT = "/Shared/rte-model-search" if ON_DATABRICKS else "rte-model-search"

EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT", DEFAULT_EXPERIMENT)
