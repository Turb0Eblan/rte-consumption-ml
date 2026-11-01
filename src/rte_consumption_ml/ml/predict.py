import os
from datetime import date

import joblib
import pandas as pd

from rte_consumption_ml.ml.banner import LOGO
from rte_consumption_ml.ml.settings import MODELS_DIR, ON_DATABRICKS, paris_today
from rte_consumption_ml.ml.storage import append_rows, read_rows
from rte_consumption_ml.ml.train_search import (
    FEATURES,
    add_calendar,
    load_data,
)

#================================================================================================

def load_d1_gold(verbose=False):
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "Europe/Paris")

    d1 = (
        spark.table("workspace.gold.features")
        .where("consumption_mw IS NULL")                 # contract: serving rows
        .orderBy("ts")
        .toPandas()
    )

    # gold keeps only one forecast day; if gold was not rebuilt today,
    # these rows are for an old day and we must not predict them
    tomorrow = (paris_today() + pd.Timedelta(days=1))
    days = d1["ts"].dt.date.unique()
    if list(days) != [tomorrow]:
        raise ValueError(f"Gold D+1 rows are for {days}, expected {tomorrow}")

    if verbose:
        print('gold')
        print(d1)
        print("=" * 100)

    return d1

#================================================================================================

def get_d1_features(verbose=False):
    if ON_DATABRICKS:
        return load_d1_gold(verbose)
    return make_d1_features(verbose)

def load_model(path=f"{MODELS_DIR}/best_model.joblib"):

    return joblib.load(path)

#================================================================================================

def load_best_name(path=f"{MODELS_DIR}/best_models.csv"):

    log = read_rows("workspace.gold.best_models", path)

    log = log.sort_values("logged_at")

    return log["estimator"].iloc[-1]

#================================================================================================

def make_d1_features(verbose=False):

    tomorrow = pd.Timestamp(date.today()) + pd.Timedelta(days=1)
    df = pd.DataFrame({"ts": pd.date_range(tomorrow, periods=48, freq="30min")})
    df = add_calendar(df)

    # history: consumption by timestamp, to look up the past
    history = load_data().set_index("ts")["consumption_mw"]

    df["lag_364d"] = (df["ts"] - pd.Timedelta(days=364)).map(history)

    past_days = [(df["ts"] - pd.Timedelta(days=d)).map(history) for d in range(364, 371)]
    past = pd.concat(past_days, axis=1)
    df["rolling_mean_7d_364"] = past.mean(axis=1).where(past.count(axis=1) >= 5)

    if verbose:
        print('d1 features')
        print(df)
        print("=" * 100)

    return df

#================================================================================================

def save_predictions(ts, y_pred, estimator, path=f"{MODELS_DIR}/predictions.csv"):

    os.makedirs(os.path.dirname(path), exist_ok=True)

    rows = pd.DataFrame({
        "ts": ts,
        "prediction_mw": y_pred,
        "estimator": estimator,
        "predicted_on": paris_today(),
    })

    append_rows(rows, "workspace.gold.predictions", path)

#================================================================================================

def main():

    print_setting = False # True for debugging

    print(LOGO)
    print("=" * 100)

    model = load_model(path=f"{MODELS_DIR}/best_model.joblib")
    name = load_best_name(path=f"{MODELS_DIR}/best_models.csv")

    if print_setting:
        print('model')
        print(model)
        print("=" * 100)

    print("Loaded:", name)

    d1 = get_d1_features(verbose=print_setting)
    X = d1[FEATURES]

    if X.isna().any().any():
        raise ValueError("D+1 features have missing values, cannot predict")

    y_pred = model.predict(X)

    if print_setting:
        print('d1')
        print(d1)
        print("=" * 100)

    save_predictions(d1["ts"], y_pred, name)


if __name__ == "__main__":
    main()