import os
from datetime import date

import joblib
import pandas as pd

from rte_consumption_ml.ml.banner import LOGO
from rte_consumption_ml.ml.train_search import add_calendar, load_data

print_setting = False # True for debugging  

def load_model(path="models/best_model.joblib"):

    return joblib.load(path)

#================================================================================================

def load_best_name(path="models/best_models.csv"):

    log = pd.read_csv(path)

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
    df["rolling_mean_7d_364"] = pd.concat(past_days, axis=1).mean(axis=1)

    if verbose:
        print('d1 features')
        print(df)
        print("=" * 100)

    return df

#================================================================================================

def save_predictions(ts, y_pred, estimator, path="models/predictions.csv"):

    os.makedirs(os.path.dirname(path), exist_ok=True)

    rows = pd.DataFrame({
        "ts": ts,
        "prediction_mw": y_pred,
        "estimator": estimator,
        "predicted_on": date.today().isoformat(),
    })
    rows.to_csv(path, mode="a", header=not os.path.exists(path), index=False)

    print("Saved", len(rows), "predictions to", path)

#================================================================================================

def main():

    print(LOGO)
    print("=" * 100)

    model = load_model()
    name = load_best_name()

    if print_setting:
        print('model')
        print(model)
        print("=" * 100)

    print("Loaded:", name)

    d1 = make_d1_features(verbose=print_setting)
    X = d1.drop(columns=["ts"])
    y_pred = model.predict(X)

    if print_setting:
        print('d1')
        print(d1)
        print("=" * 100)

    save_predictions(d1["ts"], y_pred, name)


if __name__ == "__main__":
    main()