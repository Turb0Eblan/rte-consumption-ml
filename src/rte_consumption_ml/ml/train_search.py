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
        print(df)
        print("=" * 40)

    return df.dropna().reset_index(drop=True)

if __name__ == "__main__":
    data = load_data(verbose=True)
    print(data)
    print(data.shape)