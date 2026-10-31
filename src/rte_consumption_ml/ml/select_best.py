import mlflow

from datetime import date
from rte_consumption_ml.ml.banner import LOGO


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


def main():

    print_setting = True # True for debugging  

    best = get_best_run(verbose=print_setting)
    
    print(LOGO)
    print(
            'Best name:', best["params.estimator"], 
            'Best metric:', best["metrics.mape"], 
            "Best id:", best["run_id"]
        )


if __name__ == "__main__":
    main()