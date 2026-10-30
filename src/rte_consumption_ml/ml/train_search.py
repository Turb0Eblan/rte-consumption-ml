import holidays
import numpy as np
import pandas as pd

from sklearn.utils import all_estimators
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    root_mean_squared_error,
)

from xgboost import XGBRegressor
from lightgbm import LGBMRegressor

SLOTS_PER_DAY = 48 # recording every 30 min, so 24h * 2
EXCLUDED = {
    "KernelRidge",
    "GaussianProcessRegressor",
    "SVR",
    "NuSVR",
    "QuantileRegressor",
    "TheilSenRegressor",
    "RadiusNeighborsRegressor",
    "StackingRegressor",
    "VotingRegressor",
    "MultiOutputRegressor",
    "RegressorChain",
    "IsotonicRegression",
    "CCA",
    "PLSCanonical"
            } # excluding models that failed with an error

def load_data(verbose: bool = False) -> pd.DataFrame:
    # temporary gold table for test
    rng = np.random.default_rng(42)

    ts = pd.date_range("2020-01-01", "2026-10-29 23:30", freq="30min")
    df = pd.DataFrame({"ts": ts})

    if verbose:
        print('df')
        print(df)
        print("=" * 100)

    #====================================================================
    
    # calendar example (for future table)

    df["half_hour"] = df["ts"].dt.hour * 2 + df["ts"].dt.minute // 30
    df["day_of_week"] = df["ts"].dt.day_of_week
    df["month"] = df["ts"].dt.month

    fr_holidays = holidays.France(years=range(2020, 2027))

    df['is_holiday'] = df["ts"].dt.date.isin(list((fr_holidays.keys())))

    if verbose:
        print('calendar example')
        print(df)
        print("=" * 100)

    #====================================================================

    # fake target: base + seasons + daily cycle + weekend/holiday dip + noise

    day_of_year = df["ts"].dt.dayofyear
    df["consumption_mw"] = (
        50_000
        + 12_000 * np.cos(2 * np.pi * day_of_year / 365.25)  # winter high, summer low
        + 6_000 * np.sin(2 * np.pi * (df["half_hour"] - 14) / SLOTS_PER_DAY)  # day vs night
        - 5_000 * (df["day_of_week"] >= 5)  # weekend
        - 4_000 * df["is_holiday"]
        + rng.normal(0, 1_500, len(df))  # noise
    )

    if verbose:
        print('fake consumption')
        print(df)
        print("=" * 100)

    #====================================================================

    # lag example (for future table)

    df["lag_d1"] = df["consumption_mw"].shift(SLOTS_PER_DAY)
    df["lag_d7"] = df["consumption_mw"].shift(7 * SLOTS_PER_DAY)

    df["rolling_mean_7d"] = df.groupby("half_hour")["consumption_mw"].transform(
        lambda s: s.shift(1).rolling(7).mean()
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

def evaluate(y_true, y_pred, verbose=False):


    metrics = {"mae": mean_absolute_error(y_true, y_pred), 
               "rmse": root_mean_squared_error(y_true, y_pred), 
               "mape": mean_absolute_percentage_error(y_true, y_pred)
    }

    return metrics


if __name__ == "__main__":
    print('info:')
    data = load_data(verbose=True)
    print(data)
    print(data.shape)
    print("=" * 100)

    X_train, X_test, y_train, y_test = split_by_time(data, verbose=True)
    print(X_train.shape, X_test.shape)
    print("=" * 100)

    estimators = get_estimators(verbose=True)
    print(len(estimators))
    print([name for name, _ in estimators])
    print("=" * 100)

    name, cls = estimators[0]
    model = cls()
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    print(name, evaluate(y_test, y_pred, verbose=True))