import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from aep_pipeline.contracts import load_layer_contracts
from aep_pipeline.dates import apply_placeholder_normalization, normalize_placeholder_dates, to_date
from aep_pipeline.layers import read_layer
from aep_pipeline.writers import write_json, write_parquet


# ---------------------------------------------------------------- placeholder dates (4.2)
def test_zero_date_text_becomes_missing_and_flagged():
    cleaned, flagged = normalize_placeholder_dates(pd.Series(["0000/00/00", "1995/01/01"]))
    assert pd.isna(cleaned.iloc[0]) and flagged.tolist() == [True, False]
    assert cleaned.iloc[1] == pd.Timestamp("1995-01-01")


def test_default_century_date_becomes_missing_and_flagged_in_both_forms():
    as_text, text_flag = normalize_placeholder_dates(pd.Series(["1900/01/01", "2010/05/05"]))
    as_dt, dt_flag = normalize_placeholder_dates(pd.Series(pd.to_datetime(["1900-01-01", "2010-05-05"])))
    for cleaned, flagged in ((as_text, text_flag), (as_dt, dt_flag)):
        assert pd.isna(cleaned.iloc[0]) and flagged.tolist() == [True, False]
        assert cleaned.iloc[1] == pd.Timestamp("2010-05-05")


def test_genuine_dates_pass_through_unchanged():
    cleaned, flagged = normalize_placeholder_dates(pd.Series(["2001/06/15", "1969-01-01"]))
    assert cleaned.tolist() == [pd.Timestamp("2001-06-15"), pd.Timestamp("1969-01-01")]
    assert not flagged.any()


def test_null_dates_stay_missing_without_a_flag():
    cleaned, flagged = normalize_placeholder_dates(pd.Series([None, np.nan, ""]))
    assert cleaned.isna().all()
    assert not flagged.any()


def test_unreadable_text_is_missing_and_flagged():
    cleaned, flagged = normalize_placeholder_dates(pd.Series(["not a date", "2020/02/30"]))
    assert cleaned.isna().all() and flagged.all()


def test_output_dtype_is_consistent():
    cleaned, _ = normalize_placeholder_dates(pd.Series(["2001/06/15"]))
    assert str(cleaned.dtype) == "datetime64[ms]"
    assert str(to_date(pd.Series(["2025-02-13"])).dtype) == "datetime64[ms]"


def test_contract_marked_fields_get_flag_columns_on_fixture_layers(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    pole = apply_placeholder_normalization(read_layer(fixture_raw.raw_dir, contracts["Pole_Clip"]), contracts["Pole_Clip"])
    assert {"date_insta_is_placeholder", "date_manuf_is_placeholder"} <= set(pole.columns)
    primary = apply_placeholder_normalization(
        read_layer(fixture_raw.raw_dir, contracts["Primary_ExportFeatures"]), contracts["Primary_ExportFeatures"]
    )
    # fixture primary lines include a 1900-01-01 stand-in and a genuine 2010 date
    assert primary["aep_date_i_is_placeholder"].tolist() == [True, False, False]
    assert pd.isna(primary["aep_date_i"].iloc[0])
    assert primary["aep_date_i"].iloc[1] == pd.Timestamp("2010-05-05")


@pytest.mark.real_data
def test_real_pole_install_dates_have_placeholders_flagged(real_data_dir):
    contract = load_layer_contracts()["Pole_Clip"]
    frame = read_layer(real_data_dir, contract, with_geometry=False)
    out = apply_placeholder_normalization(frame, contract)
    assert out["date_insta_is_placeholder"].sum() >= 50000  # profiling found 53,249 zero dates
    assert out.loc[out["date_insta_is_placeholder"], "date_insta"].isna().all()


# ---------------------------------------------------------------- deterministic writers (4.3)
def _table():
    return pd.DataFrame(
        {"token": ["c", "a", "b", "a"], "n": [3, 1, 2, 5], "when": pd.to_datetime(["2025-01-01"] * 4).astype("datetime64[ms]")}
    )


def test_tables_written_twice_have_identical_bytes(tmp_path):
    a = write_parquet(_table(), tmp_path / "one" / "t.parquet", tmp_path / "one", sort_by=["token", "n"])
    b = write_parquet(_table(), tmp_path / "two" / "t.parquet", tmp_path / "two", sort_by=["token", "n"])
    assert a.sha256 == b.sha256 and a.bytes == b.bytes and a.records == 4
    assert (tmp_path / "one" / "t.parquet").read_bytes() == (tmp_path / "two" / "t.parquet").read_bytes()


def test_row_order_of_the_input_does_not_change_the_output(tmp_path):
    shuffled = _table().sample(frac=1.0, random_state=7)
    a = write_parquet(_table(), tmp_path / "a" / "t.parquet", tmp_path / "a", sort_by=["token", "n"])
    b = write_parquet(shuffled, tmp_path / "b" / "t.parquet", tmp_path / "b", sort_by=["token", "n"])
    assert a.sha256 == b.sha256


def test_geoparquet_written_twice_has_identical_bytes_and_keeps_the_reference_system(tmp_path):
    frame = gpd.GeoDataFrame(
        {"asset_token": ["b", "a"]}, geometry=[Point(-80.1, 37.1), Point(-80.2, 37.2)], crs=4326
    )
    a = write_parquet(frame, tmp_path / "a" / "g.parquet", tmp_path / "a", sort_by=["asset_token"])
    b = write_parquet(frame.iloc[::-1], tmp_path / "b" / "g.parquet", tmp_path / "b", sort_by=["asset_token"])
    assert a.sha256 == b.sha256
    back = gpd.read_parquet(tmp_path / "a" / "g.parquet")
    assert back.crs.to_epsg() == 4326 and back["asset_token"].tolist() == ["a", "b"]


def test_json_is_sorted_and_stable(tmp_path):
    a = write_json({"b": 1, "a": [3, 2]}, tmp_path / "a" / "x.json", tmp_path / "a")
    b = write_json({"a": [3, 2], "b": 1}, tmp_path / "b" / "x.json", tmp_path / "b")
    assert a.sha256 == b.sha256
    assert (tmp_path / "a" / "x.json").read_text().startswith('{\n  "a"')


def test_written_file_records_relative_path_and_counts(tmp_path):
    written = write_parquet(_table(), tmp_path / "server" / "t.parquet", tmp_path)
    assert written.path == "server/t.parquet" and written.records == 4 and len(written.sha256) == 64
