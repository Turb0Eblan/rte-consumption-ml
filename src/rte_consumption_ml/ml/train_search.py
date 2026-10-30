import holidays
import numpy as np
import pandas as pd

SLOTS_PER_DAY = 48 #recording every 30 min, so 24h * 2

def load_data() -> pd.DataFrame:
    #temporary gold table for test
    rng = np.random.default_rng(42)

    print(rng)
    print('========================================')

    time_step = pd.date_range("2020-01-01", "2026-10-29 23:30", freq="30min")
    df_test = pd.DataFrame({"time_step": time_step})

    print(df_test)
    print('========================================')

    #====================================================================
    
    # calendar example (for future table)

    df_test["half_hour"] = df_test["time_step"].dt.hour * 2 + df_test["time_step"].dt.minute // 30
    df_test["day_of_week"] = df_test["time_step"].dt.day_of_week
    df_test["month"] = df_test["time_step"].dt.month

    fr_holidays = holidays.France(years=range(2020, 2027))

    df_test['holiday'] = df_test["time_step"].dt.date.isin(list((fr_holidays.keys())))

    print(df_test)
    print('========================================')

    #====================================================================

    # fake target: base + seasons + daily cycle + weekend/holiday dip + noise

    day_of_year = df_test["time_step"].dt.dayofyear
    df_test["consumption_mw"] = (
        50_000
        + 12_000 * np.cos(2 * np.pi * day_of_year / 365.25)  # winter high, summer low
        + 6_000 * np.sin(2 * np.pi * (df_test["half_hour"] - 14) / SLOTS_PER_DAY)  # day vs night
        - 5_000 * (df_test["day_of_week"] >= 5)  # weekend
        - 4_000 * df_test["holiday"]
        + rng.normal(0, 1_500, len(df_test))  # noise
    )

    print(df_test)
    print('========================================')

    #====================================================================

    #lag example (for future table)

    df_test["lag_d1"] = df_test["consumption_mw"].shift(SLOTS_PER_DAY)
    df_test["lag_d7"] = df_test["consumption_mw"].shift(7 * SLOTS_PER_DAY)

    df_test["rolling_mean_7d"] = df_test.groupby("half_hour")["consumption_mw"].transform(
        lambda s: s.shift(1).rolling(7).mean()
    )

    print(df_test)
    print('========================================')

    return df_test.dropna().reset_index(drop=True)

if __name__ == "__main__":
    data = load_data()
    print(data)
    print(data.shape)