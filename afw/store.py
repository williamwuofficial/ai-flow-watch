"""Tidy CSV storage under data/raw, upserted on a key so reruns are idempotent."""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
RAW = DATA / "raw"
DERIVED = DATA / "derived"


def read(name: str) -> pd.DataFrame:
    path = RAW / f"{name}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["date"])


def upsert(name: str, new: pd.DataFrame, key: list[str]) -> int:
    """Merge `new` into data/raw/{name}.csv; new rows win on key collisions. Returns row count."""
    if new.empty:
        return 0
    RAW.mkdir(parents=True, exist_ok=True)
    new = new.copy()
    new["date"] = pd.to_datetime(new["date"]).dt.normalize()
    old = read(name)
    merged = pd.concat([old, new], ignore_index=True) if not old.empty else new
    merged = merged.drop_duplicates(subset=key, keep="last").sort_values(key)
    merged.to_csv(RAW / f"{name}.csv", index=False, date_format="%Y-%m-%d")
    return len(new)
