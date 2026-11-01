import os
from datetime import date

import joblib
import mlflow
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from rte_consumption_ml.ml.banner import LOGO
from rte_consumption_ml.ml.train_search import get_estimators, load_data


def get_best_run(experiment_name="rte-model-search", verbose=False):

    today = date.today().isoformat()

    runs = mlflow.search_runs(
        experiment_names=[experiment_name],
        filter_string=f"tags.search_date = '{today}'",
        order_by=["metrics.mape ASC"],
    )
    runs = runs.dropna(subset=["metrics.mape"])
    last_search = runs["tags.search_id"].max()
    runs = runs[runs["tags.search_id"] == last_search]

    if runs.empty:
        raise ValueError(f"No successful runs found for {today}")

    if verbose:
        print('runs')
        print(runs[["params.estimator", "metrics.mape", "tags.search_date"]].head(10))
        print("=" * 100)

    return runs.iloc[0]

#================================================================================================

def build_model(estimator_name):

    estimators = dict(get_estimators())
    cls = estimators[estimator_name]

    return make_pipeline(StandardScaler(), cls())

#================================================================================================

def save_model(model, path="models/best_model.joblib"):

    os.makedirs(os.path.dirname(path), exist_ok=True)

    joblib.dump(model, path)

    print("Saved model to", path)

#================================================================================================

def log_best(best, path="models/best_models.csv"):

    row = pd.DataFrame([{ 
        "date": date.today().isoformat(),
        "estimator": best["params.estimator"],
        "mape": best["metrics.mape"],
        "run_id": best["run_id"]
    }])
    row.to_csv(path, mode="a", header=not os.path.exists(path), index=False)

    print("Logged best model to", path)

#================================================================================================

def main():

    print_setting = False # True for debugging  

    best = get_best_run(verbose=print_setting)
    
    print(LOGO)
    print("=" * 100)
    print(
        'Best name:', best["params.estimator"], 
        'Best metric:', best["metrics.mape"], 
        "Best id:", best["run_id"]
    )
    print("=" * 100)

    name = best["params.estimator"]

    data = load_data()
    X = data.drop(columns=["ts", "consumption_mw"])
    y = data["consumption_mw"]

    model = build_model(name)

    if print_setting:
        print('model')
        print(model)
        print("=" * 100)
    
    model.fit(X, y)
    print("Trained:", name, "on", len(X), "rows")

    save_model(model)

    log_best(best)


if __name__ == "__main__":
    main()