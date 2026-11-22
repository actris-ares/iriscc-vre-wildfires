"""NRT filename indexing, ACTRIS-style selection helpers, and dataset subsetting."""

from urllib.parse import urlparse
import re
import pandas as pd
import numpy as np
import xarray as xr
import os

from nrt_config import TARGET_INSTRUMENTS, NRT_INSTRUMENT_TOKENS, NRT_VARIABLE_RULES, get_variable_rule

EMPTY = "— select —"
ALLVARS = "— all variables —"


def get_filename(url: str) -> str:
    return urlparse(str(url)).path.split("/")[-1]


def _contains_any(text, tokens):
    text = str(text).lower()
    return any(str(t).lower() in text for t in tokens)


def _contains_all(text, tokens):
    text = str(text).lower()
    return all(str(t).lower() in text for t in tokens)


def _matches_rule(filename, rule):
    tokens = rule.get("filename_required", [])
    mode = rule.get("match_mode", "all")
    if mode == "any":
        return _contains_any(filename, tokens)
    return _contains_all(filename, tokens)


def infer_nrt_instrument_type(filename_or_token):
    text = str(filename_or_token).lower()
    for instrument_type, tokens in NRT_INSTRUMENT_TOKENS.items():
        if _contains_any(text, tokens):
            return instrument_type
    return None

def is_nrt_file(url):
    """Return True for NRT files based on the filename only."""
    filename = os.path.basename(urlparse(url).path)
    tags = ["lev1.5", "lev3b", "NRT", "nrt"]
    return any(tag in filename for tag in tags)
    

def build_file_index(all_urls, exclude_instruments=None):
    """Build NRT file index from OPeNDAP URLs.

    Set exclude_instruments=None by default so SMPS/DMPS/APS are included.
    Use the canonical instrument_type column for ACTRIS-style widgets while keeping
    the filename token in instrument_token.
    """
    exclude = {x.lower() for x in (exclude_instruments or [])}
    rows = []
    for u in all_urls:
        fname = get_filename(u)
        if not fname.endswith(".nc"):
            continue
        parts = fname.split(".")
        if len(parts) < 4:
            continue
        station = parts[0]
        instrument_token = parts[3]
        if instrument_token.lower() in exclude:
            continue
        instrument_type = infer_nrt_instrument_type(fname) or instrument_token
        start_str = parts[1] if re.fullmatch(r"\d{14}", parts[1]) else None
        created_str = parts[2] if re.fullmatch(r"\d{14}", parts[2]) else None
        rows.append({
            "filename": fname,
            "opendap_url": u,
            "station": station,
            "instrument": instrument_type,       # canonical display name
            "instrument_type": instrument_type,  # explicit canonical name
            "instrument_token": instrument_token,
            "start_str": start_str,
            "created_str": created_str,
            "start_dt": pd.to_datetime(start_str, format="%Y%m%d%H%M%S", errors="coerce"),
            "created_dt": pd.to_datetime(created_str, format="%Y%m%d%H%M%S", errors="coerce"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["station", "instrument_type", "created_dt", "filename"]).reset_index(drop=True)
    return df


def prepare_nrt_file_index(df_files):
    df = df_files.copy()
    if "filename" not in df.columns and "opendap_url" in df.columns:
        df["filename"] = df["opendap_url"].map(get_filename)
    if "instrument_type" not in df.columns:
        if "instrument" in df.columns:
            df["instrument_type"] = df.apply(
                lambda r: infer_nrt_instrument_type(r.get("filename", "")) or infer_nrt_instrument_type(r.get("instrument", "")) or r.get("instrument"),
                axis=1,
            )
        else:
            df["instrument_type"] = df["filename"].map(infer_nrt_instrument_type)
    return df[df["instrument_type"].notna()].copy()


def get_nrt_instruments(df_files):
    available = set(prepare_nrt_file_index(df_files)["instrument_type"].dropna().unique())
    return [i for i in TARGET_INSTRUMENTS if i in available]


def get_nrt_attributes_for_instrument(selected_instrument):
    return [r["label"] for r in NRT_VARIABLE_RULES.get(selected_instrument, [])]


def filter_nrt_files(df_files, selected_instrument, selected_variable=None, selected_station=None):
    df = prepare_nrt_file_index(df_files)
    if selected_instrument:
        df = df[df["instrument_type"] == selected_instrument]
    if selected_station:
        df = df[df["station"] == selected_station]
    if selected_variable:
        rule = get_variable_rule(selected_instrument, selected_variable)
        if rule:
            df = df[df["filename"].map(lambda f: _matches_rule(f, rule))]
    sort_cols = [c for c in ["created_dt", "start_dt", "filename"] if c in df.columns]
    if sort_cols:
        ascending = [False if c in ["created_dt", "start_dt"] else True for c in sort_cols]
        df = df.sort_values(sort_cols, ascending=ascending)
    return df.reset_index(drop=True)


def get_nrt_stations_for_selection(df_files, selected_instrument, selected_variable):
    df = filter_nrt_files(df_files, selected_instrument, selected_variable)
    return sorted(df["station"].dropna().unique())


def get_time_index(ds):
    ds = ds.sortby("time")
    return pd.DatetimeIndex(pd.to_datetime(ds["time"].values))


def available_months(tindex):
    return sorted(pd.Index(tindex.to_period("M").astype(str)).unique().tolist())


def available_days(tindex):
    return sorted(pd.Index(tindex.normalize().strftime("%Y-%m-%d")).unique().tolist())


def slice_full(ds):
    return ds.sortby("time")


def slice_month(ds, month_str):
    p = pd.Period(month_str, freq="M")
    return ds.sortby("time").sel(time=slice(p.start_time, p.end_time))


def slice_datetime_range(ds, start_day_str, start_hour, end_day_str, end_hour):
    start = pd.Timestamp(start_day_str) + pd.Timedelta(hours=int(start_hour))
    end = pd.Timestamp(end_day_str) + pd.Timedelta(hours=int(end_hour), minutes=59, seconds=59)
    return ds.sortby("time").sel(time=slice(start, end))


def dataset_variable_overview(ds, max_rows=200):
    rows = []
    for v in ds.data_vars:
        da = ds[v]
        rows.append({
            "variable": v,
            "dims": str(da.dims),
            "shape": str(tuple(da.shape)),
            "units": da.attrs.get("units", ""),
            "ebas_statistics": da.attrs.get("ebas_statistics", ""),
            "standard_name": da.attrs.get("standard_name", ""),
            "long_name": da.attrs.get("long_name", ""),
        })
    if not rows:
        return pd.DataFrame(
            columns=[
                "variable",
                "dims",
                "shape",
                "units",
                "ebas_statistics",
                "standard_name",
                "long_name",
            ]
        )

    dfv = pd.DataFrame(rows).sort_values("variable")
    return dfv.head(max_rows) if len(dfv) > max_rows else dfv


def select_nrt_variables(ds, selected_instrument, selected_variable):
    """Return a dataset containing variables suitable for the selected ACTRIS-style variable."""
    rule = get_variable_rule(selected_instrument, selected_variable)
    if not rule:
        return ds

    # PNSD: discover variable by name and require time + D/diameter-like dimension.
    if selected_instrument == "mobility particle size spectrometer":
        skip_pats = ("_qc", "qc_", "flag", "unc", "std", "_min", "_max")
        for v_name in ds.data_vars:
            lower = v_name.lower()
            if any(p in lower for p in skip_pats):
                continue
            if "particle_number_size_distribution" in lower:
                da = ds[v_name]
                dims_lower = [str(d).lower() for d in da.dims]
                if "time" in dims_lower and ("d" in dims_lower or "diameter" in dims_lower):
                    dims_to_slice = {
                        dim: 0 for dim in da.dims
                        if str(dim).lower() not in {"time", "d", "diameter"}
                    }
                    clean = da.isel(dims_to_slice) if dims_to_slice else da
                    clean = clean.copy()
                    clean.attrs = da.attrs
                    return xr.Dataset({v_name: clean})
        return xr.Dataset(attrs=ds.attrs)

    # Particle mass spectrometer: discover mass concentration variables.
    if selected_instrument == "particle mass spectrometer":
        skip_dims = {"Fraction", "Fractionx", "Fractionxx"}
        skip_pats = ("perc", "std", "unc", "_min", "_max", "_range", "_median", "_qc")
        vars_out = []
        for v_name in ds.data_vars:
            da = ds[v_name]
            if any(p in v_name.lower() for p in skip_pats):
                continue
            if da.attrs.get("ebas_statistics") == "arithmetic mean" and da.attrs.get("units") == "ug/m3":
                if not any(dim in da.dims for dim in skip_dims):
                    vars_out.append(v_name)
        return ds[vars_out] if vars_out else xr.Dataset(attrs=ds.attrs)

    # Direct variable-name rules, with fallback to standard_name matching.
    wanted = []
    for target in rule.get("target_vars", []):
        if target in ds:
            wanted.append(target)
        else:
            for v_name in ds.data_vars:
                std_name = str(ds[v_name].attrs.get("standard_name", ""))
                if target in v_name or target in std_name:
                    wanted.append(v_name)
    wanted = list(dict.fromkeys(wanted))
    return ds[wanted] if wanted else xr.Dataset(attrs=ds.attrs)

def build_combined_file_index(
    actris_nrt_opendap_urls,
    ebas_nrt_opendap_urls,
    show_tables=False,
    duplicate_preview_rows=20,
    file_preview_rows=20,
):
    """
    Build combined NRT file index from ACTRIS-NRT and EBAS catalogs.

    Keeps SMPS/DMPS/APS available by using exclude_instruments=None.
    De-duplicates by filename, preferring ACTRIS-NRT over EBAS.
    """

    df_actris = build_file_index(actris_nrt_opendap_urls, exclude_instruments=None)
    df_actris["source_catalog"] = "actris_nrt"

    df_ebas = build_file_index(ebas_nrt_opendap_urls, exclude_instruments=None)
    df_ebas["source_catalog"] = "ebas"

    df_files_all = pd.concat([df_actris, df_ebas], ignore_index=True)

    source_priority = {"actris_nrt": 0, "ebas": 1}
    df_files_all["source_priority"] = (
        df_files_all["source_catalog"].map(source_priority).fillna(99)
    )

    duplicate_files = (
        df_files_all[df_files_all.duplicated("filename", keep=False)]
        .sort_values(["filename", "source_priority"])
    )

    df_files = (
        df_files_all
        .sort_values(["filename", "source_priority"])
        .drop_duplicates("filename", keep="first")
        .drop(columns="source_priority")
        .sort_values(["station", "instrument_type", "created_dt", "filename"])
        .reset_index(drop=True)
    )

    df_pairs = (
        df_files[["station", "instrument_type"]]
        .drop_duplicates()
        .sort_values(["station", "instrument_type"])
        .reset_index(drop=True)
    )

    pivot = (
        df_pairs.assign(present=1)
        .pivot_table(
            index="station",
            columns="instrument_type",
            values="present",
            fill_value=0,
        )
        .sort_index()
    )

    summary = {
        "files_before_duplicate_removal": len(df_files_all),
        "duplicate_filename_rows": len(duplicate_files),
        "files_after_duplicate_removal": len(df_files),
        "supported_instruments": get_nrt_instruments(df_files),
    }

    if show_tables:
        from IPython.display import display

        print(f"Files before duplicate removal: {summary['files_before_duplicate_removal']}")
        print(f"Duplicate filename rows: {summary['duplicate_filename_rows']}")

        if not duplicate_files.empty:
            display(
                duplicate_files[
                    ["filename", "source_catalog", "opendap_url"]
                ].head(duplicate_preview_rows)
            )

        print(f"Files after duplicate removal: {summary['files_after_duplicate_removal']}")
        print("Supported ACTRIS-style instruments found in NRT files:")
        print(summary["supported_instruments"])

        display(pivot)

        preview_cols = [
            "station",
            "instrument_type",
            "instrument_token",
            "source_catalog",
            "filename",
            "opendap_url",
        ]
        display(df_files[preview_cols].head(file_preview_rows))

    return df_files, df_files_all, duplicate_files, pivot, summary
