import holidays
import numpy as np
import pandas as pd

SLOTS_PER_DAY = 48 # recording every 30 min, so 24h * 2

def load_data(verbose: bool = False) -> pd.DataFrame:
    # temporary gold table for test
    rng = np.random.default_rng(42)

    ts = pd.date_range("2020-01-01", "2026-10-29 23:30", freq="30min")
    df = pd.DataFrame({"ts": ts})

    if verbose:
        print('df')
        print(df)
        print("=" * 40)

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
        print("=" * 40)

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
        print("=" * 40)

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
        print("=" * 40)

    return df.dropna().reset_index(drop=True)

#================================================================================================

def split_by_time(df, test_days=30, verbose=False):

    last_ts = df["ts"].max()

    if verbose:
        print('last_ts')
        print(last_ts)
        print("=" * 40)

    border = last_ts - pd.Timedelta(days=test_days)

    if verbose:
        print('border')
        print(border)
        print("=" * 40)

    train = df[df["ts"] <= border]

    if verbose:
        print('train')
        print(train)
        print("=" * 40)

    test = df[df["ts"] > border]

    if verbose:
        print('test')
        print(test)
        print("=" * 40)


    X_train = train.drop(columns=["ts", "consumption_mw"])
    X_test = test.drop(columns=["ts", "consumption_mw"])

    y_train = train["consumption_mw"]
    y_test = test["consumption_mw"]

    if verbose:
        print('X_train')
        print(X_train)
        print("=" * 40)
        print('y_train')
        print(y_train)
        print("=" * 40)


    return X_train, X_test, y_train, y_test


if __name__ == "__main__":
    data = load_data(verbose=True)
    print(data)
    print(data.shape)

    X_train, X_test, y_train, y_test = split_by_time(data, verbose=True)
    print(X_train.shape, X_test.shape)