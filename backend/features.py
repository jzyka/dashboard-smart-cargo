"""Window feature extraction, shared by the training notebook and the live backend."""
import numpy as np
import pandas as pd

WINDOW_S = 2.0   # window length in seconds
STRIDE_S = 1.0   # step between windows
MIN_ROWS = 8     # ignore windows with fewer rows than this
PRESSURE_MAX = 4095.0

FEATURES = [
    "acc_mean", "acc_std", "acc_lo", "acc_hi", "acc_range", "acc_peak_dev", "jerk_mean",
    "ax_std", "ay_std", "az_std",
    "gyro_mean", "gyro_std", "gyro_max",
    "press_mean", "press_max", "press_std",
    "temp_mean",
]


def window_features(w: pd.DataFrame) -> dict:
    """w: rows of one time window with columns acc_mag, acc_min, acc_max, ax, ay, az,
    gx, gy, gz, pressure, temp. Returns one feature dict."""
    acc = w["acc_mag"].to_numpy(float)
    gyro = np.sqrt(w["gx"] ** 2 + w["gy"] ** 2 + w["gz"] ** 2).to_numpy(float)
    press = PRESSURE_MAX - w["pressure"].to_numpy(float)
    lo, hi = float(w["acc_min"].min()), float(w["acc_max"].max())
    return {
        "acc_mean": acc.mean(), "acc_std": acc.std(),
        "acc_lo": lo, "acc_hi": hi, "acc_range": hi - lo,
        "acc_peak_dev": np.abs(acc - 9.81).max(),
        "jerk_mean": np.abs(np.diff(acc)).mean() if len(acc) > 1 else 0.0,
        "ax_std": w["ax"].std(ddof=0), "ay_std": w["ay"].std(ddof=0), "az_std": w["az"].std(ddof=0),
        "gyro_mean": gyro.mean(), "gyro_std": gyro.std(), "gyro_max": gyro.max(),
        "press_mean": press.mean(), "press_max": press.max(), "press_std": press.std(),
        "temp_mean": max(33.0, float(np.nanmean(w["temp"].to_numpy(float)))),
    }


def make_windows(df: pd.DataFrame, trim_s: float = 1.0, trim_min_len_s: float = 10.0) -> pd.DataFrame:
    """Slice every recording session into overlapping windows and compute features.
    Sessions longer than trim_min_len_s lose trim_s seconds at each end (button-press moments)."""
    rows = []
    for sid, s in df.groupby("session"):
        s = s.sort_values("uptime_ms")
        t = (s["uptime_ms"].to_numpy(float) - s["uptime_ms"].iloc[0]) / 1000.0
        t0, t1 = 0.0, t[-1]
        if t1 > trim_min_len_s:
            t0, t1 = trim_s, t1 - trim_s
        start = t0
        while start + WINDOW_S <= t1 + 1e-9:
            m = (t >= start) & (t < start + WINDOW_S)
            if m.sum() >= MIN_ROWS:
                f = window_features(s[m])
                f.update(session=int(sid), label=s["label"].iloc[0],
                         rule_alert=_rule_alert(s.loc[m, "alert"]), t_start=start)
                rows.append(f)
            start += STRIDE_S
    return pd.DataFrame(rows)


def _rule_alert(alerts: pd.Series) -> str:
    """What the rule-based system said in this window (most common non-'none' alert)."""
    a = alerts[alerts != "none"]
    return a.value_counts().idxmax() if len(a) else "none"
