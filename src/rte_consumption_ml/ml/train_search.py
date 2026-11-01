import holidays
import numpy as np
import pandas as pd
import mlflow
import time

from sklearn.utils import all_estimators
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    root_mean_squared_error,
)

from xgboost import XGBRegressor
from lightgbm import LGBMRegressor
from datetime import date, datetime
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from rte_consumption_ml.ml.banner import LOGO

SLOTS_PER_DAY = 48 # recording every 30 min, so 24h * 2
EXCLUDED = {
    # too slow or too much memory on ~120k rows
    "KernelRidge",               # n x n kernel matrix
    "GaussianProcessRegressor",  # n x n matrix, O(n^3)
    "SVR",
    "NuSVR",
    "QuantileRegressor",
    "TheilSenRegressor",
    "RadiusNeighborsRegressor",
    # need required constructor args
    "StackingRegressor",
    "VotingRegressor",
    "MultiOutputRegressor",
    "RegressorChain",
    # unsuitable for this task
    "IsotonicRegression",  # only one feature
    "CCA",                 # multi-target models
    "PLSCanonical",
    "MultiTaskElasticNet",
    "MultiTaskElasticNetCV",
    "MultiTaskLasso",
    "MultiTaskLassoCV",
}

#================================================================================================

# same calendar for whole project

def add_calendar(df):

    df["half_hour"] = df["ts"].dt.hour * 2 + df["ts"].dt.minute // 30
    df["day_of_week"] = df["ts"].dt.day_of_week
    df["month"] = df["ts"].dt.month
    df["day_of_year"] = df["ts"].dt.day_of_year

    fr_holidays = holidays.France(years=range(2020, 2028))
    df['is_holiday'] = df["ts"].dt.date.isin(list((fr_holidays.keys())))

    return df

#================================================================================================

def load_data(verbose: bool = False) -> pd.DataFrame:
    # temporary gold table for test
    rng = np.random.default_rng(42)

    ts = pd.date_range("2020-01-01", "2026-10-29 23:30", freq="30min")
    df = pd.DataFrame({"ts": ts})

    if verbose:
        print('df')
        print(df)
        print("=" * 100)

    #=======================================

    df = add_calendar(df)

    if verbose:
        print('calendar')
        print(df)
        print("=" * 100)

    #=======================================

    # fake target: base + seasons + daily cycle + weekend/holiday dip + noise

    df["consumption_mw"] = (
        50_000
        + 12_000 * np.cos(2 * np.pi * df["day_of_year"] / 365.25)  # winter high, summer low
        + 6_000 * np.sin(2 * np.pi * (df["half_hour"] - 14) / SLOTS_PER_DAY)  # day vs night
        - 5_000 * (df["day_of_week"] >= 5)  # weekend
        - 4_000 * df["is_holiday"]
        + rng.normal(0, 1_500, len(df))  # noise
    )

    if verbose:
        print('fake consumption')
        print(df)
        print("=" * 100)

    #========================================

    # lag example (for future table)
    # fake data has no DST (always 48 slots), so shift is ok here
    # real gold joins on (local_date - 364, half_hour)

    df["lag_364d"] = df["consumption_mw"].shift(364 * SLOTS_PER_DAY)

    df["rolling_mean_7d_364"] = df.groupby("half_hour")["consumption_mw"].transform(
        lambda s: s.shift(364).rolling(7).mean()
    )

    if verbose:
        print('lag')
        print(df)
        print("=" * 100)

    return df.dropna().reset_index(drop=True)

#================================================================================================

def split_by_time(df, test_days=30, verbose=False):

    last_ts = df["ts"].max()

    if verbose:
        print('last_ts')
        print(last_ts)
        print("=" * 100)

    border = last_ts - pd.Timedelta(days=test_days)

    if verbose:
        print('border')
        print(border)
        print("=" * 100)

    train = df[df["ts"] <= border]

    if verbose:
        print('train')
        print(train)
        print("=" * 100)

    test = df[df["ts"] > border]

    if verbose:
        print('test')
        print(test)
        print("=" * 100)


    X_train = train.drop(columns=["ts", "consumption_mw"])
    X_test = test.drop(columns=["ts", "consumption_mw"])

    y_train = train["consumption_mw"]
    y_test = test["consumption_mw"]

    if verbose:
        print('X_train')
        print(X_train)
        print("=" * 100)
        print('y_train')
        print(y_train)
        print("=" * 100)


    return X_train, X_test, y_train, y_test

#================================================================================================

def get_estimators(verbose=False):

    all_regs = all_estimators(type_filter="regressor")

    if verbose:
        print('all_regs')
        print(len(all_regs))
        print("=" * 100)

    estimators = [(name, cls) for name, cls in all_regs if name not in EXCLUDED]

    estimators.append(("XGBRegressor", XGBRegressor))
    estimators.append(("LGBMRegressor", LGBMRegressor))


    return estimators

#================================================================================================

def evaluate(y_true, y_pred):


    metrics = {
        "mae": mean_absolute_error(y_true, y_pred), 
        "rmse": root_mean_squared_error(y_true, y_pred), 
        "mape": mean_absolute_percentage_error(y_true, y_pred)
    }


    return metrics

#================================================================================================

def run_search(estimators, X_train, X_test, y_train, y_test, verbose=False):

    mlflow.set_experiment("rte-model-search")
    today = date.today().isoformat()
    search_id = datetime.now().isoformat(timespec="seconds")


    for i, (name, cls) in enumerate(estimators, start=1):
        print(f"[{i}/{len(estimators)}] {i / len(estimators) * 100:.0f}% {name}")
        with mlflow.start_run(run_name=name):
            try:
                mlflow.log_param("estimator", name)
                mlflow.set_tag("search_date", today)
                mlflow.set_tag("search_id", search_id)
                model = make_pipeline(StandardScaler(), cls())

                start = time.perf_counter()
                model.fit(X_train, y_train)
                fit_seconds = time.perf_counter() - start

                y_pred = model.predict(X_test)
                metrics = evaluate(y_test, y_pred)
                metrics["fit_seconds"] = fit_seconds
                mlflow.log_metrics(metrics)
                


                if verbose:
                    print('model:', name)
                    print("=" * 100)
                    print('metrics', metrics)
                    print("=" * 100)

                    
            except Exception as e:
                mlflow.set_tag("status", "failed")
                print('failed:', name, e)
                print("=" * 100)

#================================================================================================

def main():

    print_setting = False # True for debugging

    print('info:')
    data = load_data(verbose=print_setting)
    print(data)
    print(data.shape)
    print("=" * 100)

    X_train, X_test, y_train, y_test = split_by_time(data, verbose=print_setting)
    print(X_train.shape, X_test.shape)
    print("=" * 100)

    estimators = get_estimators(verbose=print_setting)
    print(len(estimators))
    print([name for name, _ in estimators])
    print("=" * 100)

    print(LOGO)
    print(f"RTE model search | {date.today()} | {len(estimators)} estimators")

    run_search(estimators, X_train, X_test, y_train, y_test, verbose=print_setting)

#================================================================================================

if __name__ == "__main__":
    main()