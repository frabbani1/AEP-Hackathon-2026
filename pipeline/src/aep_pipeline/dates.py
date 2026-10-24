"""Normalization of placeholder dates in asset data.

The source uses ``0000/00/00`` and 1900-01-01 as stand-ins for "unknown". Left as they are,
they look like real, very old installation dates and would mislead any age feature.
They become missing values with a flag. Genuinely missing (null) dates stay missing and
are not flagged, because nothing was recorded.
"""

from __future__ import annotations

import pandas as pd

from .contracts import LayerContract

PLACEHOLDER_TEXT = frozenset({"0000/00/00", "0000-00-00", "00000000", "0000/00/00 00:00:00"})
PLACEHOLDER_DATE = pd.Timestamp("1900-01-01")
FLAG_SUFFIX = "_is_placeholder"
DATE_DTYPE = "datetime64[ms]"


def normalize_placeholder_dates(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (dates, placeholder_flag). Placeholders and unreadable values become NaT and flagged."""
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series)
        flagged = pd.Series(False, index=series.index)
    else:
        text = series.astype("string").str.strip()
        is_null = text.isna() | (text == "")
        is_placeholder_text = text.isin(PLACEHOLDER_TEXT).fillna(False)
        normalized = text.str.replace("/", "-", regex=False)
        parsed = pd.to_datetime(normalized.where(~is_placeholder_text & ~is_null), errors="coerce", format="mixed")
        unreadable = parsed.isna() & ~is_null & ~is_placeholder_text
        flagged = (is_placeholder_text | unreadable).astype(bool)
    is_default_century = parsed == PLACEHOLDER_DATE
    flagged = flagged | is_default_century.fillna(False)
    cleaned = parsed.mask(is_default_century)
    return cleaned.astype(DATE_DTYPE), flagged.astype(bool)


def apply_placeholder_normalization(frame: pd.DataFrame, contract: LayerContract) -> pd.DataFrame:
    """Normalize every date field the contract marks ``placeholder_dates`` and add its flag column."""
    result = frame.copy()
    for field in contract.fields.values():
        if field.placeholder_dates and field.name in result.columns:
            cleaned, flagged = normalize_placeholder_dates(result[field.name])
            result[field.name] = cleaned
            result[field.name + FLAG_SUFFIX] = flagged
    return result


def to_date(series: pd.Series) -> pd.Series:
    """Plain conversion of a real date field (no placeholder handling), at date resolution."""
    return pd.to_datetime(series, errors="coerce").astype(DATE_DTYPE)
