"""Plotly plotting functions shared by ACTRIS in-situ and NRT notebooks.

Functions from ACTRIS_insitu.py:

* `plot_datasets`
* `plot_line_datasets`
* `plot_mass_spectrometer`
* `plot_particle_size_distribution`
* `create_pnsd_year_widget`

"""

import re
import numpy as np
import pandas as pd
import xarray as xr
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import init_notebook_mode


# -----------------------------------------------------------------------------
# Plotly setup and small helpers
# -----------------------------------------------------------------------------

def enable_plotly_offline_mode():
    """Prefer local Plotly JS assets so figures render without internet access."""
    try:
        init_notebook_mode(connected=False)
        if pio.renderers.default == "notebook_connected":
            pio.renderers.default = "notebook"
    except Exception:
        # Keep plotting functional even if renderer setup fails in non-notebook contexts.
        pass


enable_plotly_offline_mode()


def _as_dataset_list(datasets):
    if isinstance(datasets, (xr.Dataset, xr.DataArray)):
        return [datasets]
    if isinstance(datasets, list):
        return datasets
    if isinstance(datasets, tuple):
        return list(datasets)
    return []


def _selected_year_to_str(selected_year):
    if selected_year in (None, "All years", "all", "ALL", ""):
        return None
    try:
        return str(int(selected_year))
    except (TypeError, ValueError):
        return None


def _filter_dataarray_to_year(data_array, selected_year):
    year_str = _selected_year_to_str(selected_year)
    if not year_str or "time" not in data_array.coords:
        return data_array, year_str
    years = pd.to_datetime(data_array["time"].values).year
    return data_array.isel(time=(years == int(year_str))), year_str


def _build_title(selected_instrument=None, selected_attr=None, selected_station=None,
                 selected_year=None, selected_model=None):
    year_str = _selected_year_to_str(selected_year)
    title_parts = [
        selected_instrument,
        selected_attr,
        selected_station,
        f"Year: {year_str}" if year_str else None,
        f"Model: {selected_model}" if selected_model else None,
    ]
    parts = [str(p) for p in title_parts if p]
    title = " - ".join(parts)
    # A one-line title of this length reaches over the y-axis label, which is itself long and
    # rotated: the two overlap in a narrow window. Breaking it after the instrument keeps both
    # readable at any width, and plotly honours <br> in titles.
    if len(title) > 60 and len(parts) > 1:
        title = parts[0] + "<br>" + " - ".join(parts[1:])
    return title


def _clean_label(name):
    if name is None:
        return ""
    label = str(name)
    for token in ("_amean", "ug_per_m3", "_per_m3", "_coefficient"):
        label = label.replace(token, "")
    return label.replace("_", " ").strip()


MAX_LINE_GAP = pd.Timedelta(hours=1)


def _insert_time_gaps(times, y_values, max_gap=MAX_LINE_GAP):
    """Insert a NaN sample at the midpoint of any gap larger than max_gap.

    Plotly draws a straight line between consecutive (x, y) points regardless of the time
    gap between them; connectgaps=False only stops it connecting across existing NaNs, not
    across missing timestamps. Inserting a NaN there breaks the line instead.
    """
    times = pd.DatetimeIndex(times)
    y_values = np.asarray(y_values, dtype=float)
    if len(times) < 2:
        return times, y_values

    gaps = np.diff(times.values)
    gap_idx = np.flatnonzero(gaps > np.timedelta64(max_gap))
    if gap_idx.size == 0:
        return times, y_values

    marker_times = times[gap_idx] + (times.values[gap_idx + 1] - times.values[gap_idx]) / 2
    all_times = times.append(pd.DatetimeIndex(marker_times))
    all_values = np.concatenate([y_values, np.full(len(marker_times), np.nan)])
    order = np.argsort(all_times.values)
    return all_times[order], all_values[order]


def _is_size_distribution(selected_attr=None, selected_instrument=None):
    text = " ".join(str(x or "").lower() for x in (selected_attr, selected_instrument))
    return (
        "particle number size distribution" in text
        or "mobility particle size spectrometer" in text
        or "size distribution" in text
    )


def _is_mass_spectrometer(selected_instrument=None):
    return selected_instrument is not None and "particle mass spectrometer" in str(selected_instrument).lower()


# -----------------------------------------------------------------------------
# PNSD year widget
# -----------------------------------------------------------------------------

def create_pnsd_year_widget(datasets):
    """Create a year selector for PNSD plots.

    If multiple years are present, the user must choose one single year.
    """
    import ipywidgets as widgets
    from IPython.display import display

    da = None
    for ds in _as_dataset_list(datasets):
        if not isinstance(ds, xr.Dataset):
            continue
        for v_name in ds.data_vars:
            if "particle_number_size_distribution" in v_name.lower():
                da = ds[v_name]
                break
        if da is not None:
            break

    if da is None or "time" not in da.coords:
        print("No PNSD time axis found for year selection.")
        return None

    years = sorted({int(y) for y in pd.to_datetime(da["time"].values).year if pd.notna(y)})
    year_options = years
    if len(year_options) <= 1:
        print("No year options available for PNSD.")
        return None

    widget = widgets.Dropdown(
        options=year_options,
        value=year_options[0],
        description="PNSD year:",
        style={"description_width": "initial"},
    )
    display(widget)
    return widget


# -----------------------------------------------------------------------------
# PNSD heatmap plotting
# -----------------------------------------------------------------------------

def plot_particle_size_distribution(datasets, selected_attr=None, selected_station=None,
                                    selected_year=None, selected_instrument=None,
                                    selected_model=None):
    """Plot particle number size distribution as a Plotly heatmap."""
    ds_list = _as_dataset_list(datasets)

    target_var = None
    ds_to_plot = None
    for ds in ds_list:
        if not isinstance(ds, xr.Dataset):
            continue
        for v_name in ds.data_vars:
            if "particle_number_size_distribution" in v_name.lower():
                target_var = v_name
                ds_to_plot = ds
                break
        if target_var:
            break

    if not target_var or ds_to_plot is None:
        print("No particle_number_size_distribution variable found in datasets.")
        return None

    print(f"Plotting variable: {target_var}")
    da = ds_to_plot[target_var]

    year_str = _selected_year_to_str(selected_year)
    if year_str is None:
        if "time" in da.coords:
            time_values = pd.to_datetime(da["time"].values)
            if len(time_values) == 0:
                print("No PNSD time values found.")
                return None
            duration = time_values.max() - time_values.min()
            if duration <= pd.Timedelta(days=366):
                print("Rendering selected PNSD time window at full resolution.")
            else:
                available_years = set()
                for ds in ds_list:
                    if not isinstance(ds, xr.Dataset):
                        continue
                    for v_name in ds.data_vars:
                        if "particle_number_size_distribution" in v_name.lower():
                            da_temp = ds[v_name]
                            if "time" in da_temp.coords:
                                years = pd.to_datetime(da_temp["time"].values).year
                                available_years.update(int(y) for y in years if pd.notna(y))
                print("PNSD plotting requires selecting exactly one year.")
                if available_years:
                    print(f"Available years: {sorted(available_years)}")
                return None
        else:
            print("PNSD plotting requires selecting exactly one year.")
            return None
    else:
        da, _ = _filter_dataarray_to_year(da, year_str)
        if da.sizes.get("time", 0) == 0:
            print(f"No PNSD data found for year {year_str}.")
            return None
        print(f"Rendering year {year_str} at full time resolution.")

    diameter_col = None
    for dim in da.dims:
        if str(dim).upper() == "D" or str(dim).lower() == "diameter":
            diameter_col = dim
            break
    if diameter_col is None:
        for coord in da.coords:
            if str(coord).upper() == "D" or str(coord).lower() == "diameter":
                diameter_col = coord
                break
    if diameter_col is None:
        print("Could not find diameter dimension (D/d/diameter) in dataset.")
        return None

    if "time" in da.dims:
        da = da.transpose(diameter_col, "time")
    else:
        da = da.transpose(diameter_col, *[dim for dim in da.dims if dim != diameter_col])

    z_values = np.asarray(da.values, dtype=np.float32)
    x_values = da["time"].values

    valid_full = (z_values > 0) & np.isfinite(z_values)
    valid_d_rows = np.any(valid_full, axis=1)
    valid_t_cols = np.any(valid_full, axis=0)

    if not np.any(valid_d_rows) or not np.any(valid_t_cols):
        print("No valid PNSD data after year filtering.")
        return None

    z_values = z_values[valid_d_rows][:, valid_t_cols]
    x_values = x_values[valid_t_cols]
    y_values = da[diameter_col].values[valid_d_rows]
    full_width = max(1600, min(5000, int(z_values.shape[1] * 0.75)))
    print(
        f"Full-resolution render: {z_values.shape[1]} time points x "
        f"{z_values.shape[0]} diameter bins (width={full_width}px)."
    )

    valid_mask = (z_values > 0) & np.isfinite(z_values)
    if not valid_mask.any():
        print("No valid data to plot.")
        return None

    z_log = np.full(z_values.shape, np.nan, dtype=np.float32)
    np.log10(z_values, out=z_log, where=valid_mask)

    zmin = np.nanmin(z_log)
    zmax = np.nanmax(z_log)
    tickvals = np.linspace(zmin, zmax, 6)
    ticktext = np.round(10 ** np.linspace(zmin, zmax, 6), 1)

    fig = go.Figure(go.Heatmap(
        x=x_values,
        y=y_values,
        z=z_log,
        zsmooth="best",
        colorscale="Jet",
        showscale=True,
        hoverongaps=False,
        colorbar=dict(
            title=dict(text="dN/dlogDp ([1/cm³])", side="right"),
            tickvals=tickvals,
            ticktext=ticktext,
        ),
    ))

    layout_args = dict(
        title=dict(text=_build_title(selected_instrument, selected_attr, selected_station,
                                     selected_year, selected_model),
                   x=0.5, xanchor="center", automargin=True),
        xaxis_title="Time",
        yaxis_title="D [nm]",
        title_font_size=20,
        template="plotly_white",
        height=650,
        width=full_width,
        autosize=False,
    )
    fig.update_layout(**layout_args)
    fig.update_yaxes(automargin=True)
    fig.update_yaxes(type="log")
    fig.show()
    return fig


# -----------------------------------------------------------------------------
# Particle mass spectrometer plotting
# -----------------------------------------------------------------------------

def plot_mass_spectrometer(datasets, selected_attr=None, selected_station=None,
                           selected_year=None, selected_instrument=None,
                           selected_model=None):
    """Plot particle mass spectrometer data with ug/m3 arithmetic-mean variables."""
    custom_colors = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    ]

    ds_list = _as_dataset_list(datasets)
    all_variables = {}

    for ds in ds_list:
        if isinstance(ds, xr.DataArray):
            ds = ds.to_dataset(name=ds.name or "variable")
        if not isinstance(ds, xr.Dataset):
            continue

        skip_dimensions = {"Fraction", "Fractionx", "Fractionxx"}
        skip_patterns = ("perc", "std", "unc", "_min", "_max", "_range", "_median", "_qc", "qc_")

        for v_name in ds.data_vars:
            if v_name in all_variables or any(p in v_name.lower() for p in skip_patterns):
                continue
            v_data = ds[v_name]
            attrs = v_data.attrs
            if attrs.get("ebas_statistics") == "arithmetic mean" and attrs.get("units") == "ug/m3":
                if not any(dim in v_data.dims for dim in skip_dimensions):
                    all_variables[v_name] = (v_data, attrs)

    if not all_variables:
        print("No valid variables found with 'arithmetic mean' statistics and 'ug/m3' units.")
        return None

    fig = go.Figure()
    year_str = _selected_year_to_str(selected_year)
    trace_count = 0

    for idx, (v_name, (v_data, attrs)) in enumerate(sorted(all_variables.items())):
        v_plot, _ = _filter_dataarray_to_year(v_data, selected_year)
        if "time" in v_plot.coords and v_plot.sizes.get("time", 0) == 0:
            continue

        is_visible = "legendonly"
        if selected_attr:
            std_name = attrs.get("standard_name", "").lower()
            var_lower = v_name.lower()
            attr_lower = selected_attr.lower()
            key_terms = [
                "ammonium", "nitrate", "sulfate", "sulphate", "organic", "chloride",
                "mass", "black carbon", "elemental carbon",
            ]
            matched_term = next((term for term in key_terms if term in attr_lower), None)
            if matched_term:
                is_visible = True if matched_term in var_lower or matched_term in std_name else "legendonly"
            else:
                attr_words = [w for w in attr_lower.replace("_", " ").split() if len(w) > 4]
                match_count = sum(1 for w in attr_words if w in var_lower or w in std_name)
                is_visible = True if match_count >= 1 else "legendonly"
        else:
            is_visible = True

        if "time" not in v_plot.coords:
            continue
        times = pd.to_datetime(v_plot["time"].values)
        y_values = np.asarray(v_plot.values)
        if y_values.ndim > 1:
            extra_dims = [d for d in v_plot.dims if d != "time"]
            v_plot = v_plot.mean(dim=extra_dims, skipna=True)
            y_values = np.asarray(v_plot.values)
        if not np.isfinite(y_values).any():
            continue
        times, y_values = _insert_time_gaps(times, y_values)

        fig.add_trace(go.Scatter(
            x=times,
            y=y_values,
            mode="lines",
            name=_clean_label(v_name),
            line=dict(color=custom_colors[idx % len(custom_colors)], width=2),
            connectgaps=False,
            visible=is_visible,
            showlegend=True,
        ))
        trace_count += 1

    if trace_count == 0:
        if year_str:
            print(f"No data available for selected year {year_str}.")
        else:
            print("No plottable data found for the current selection.")
        return None

    fig.update_layout(
        title=dict(text=_build_title(selected_instrument, selected_attr, selected_station,
                                     selected_year, selected_model),
                   x=0.5, xanchor="center", automargin=True),
        yaxis_title="Concentration (ug/m3)",
        xaxis_title="Time",
        template="plotly_white",
        hovermode="x unified",
        showlegend=True,
        legend=dict(yanchor="top", y=1, xanchor="left", x=1.02, title_text="Variables"),
        margin=dict(r=150, l=80, t=80),
    )
    fig.update_yaxes(automargin=True)
    fig.show()
    return fig


# -----------------------------------------------------------------------------
# Line / wavelength plotting for absorption, scattering and backscattering
# -----------------------------------------------------------------------------

def _variable_type_label(v_name, v_data, selected_attr=None):
    std_name = str(v_data.attrs.get("standard_name", "")).lower()
    v_lower = str(v_name).lower()
    attr_lower = str(selected_attr or "").lower()

    if "absorption" in std_name or "absorption" in v_lower:
        return "Abs"
    if "backscattering" in std_name or "backscattering" in v_lower or "backscatter" in v_lower:
        return "Backscat"
    if "scattering" in std_name or "scattering" in v_lower:
        return "Scat"

    if "absorption" in attr_lower:
        return "Abs"
    if "backscattering" in attr_lower or "backscatter" in attr_lower:
        return "Backscat"
    if "scattering" in attr_lower or "scatter" in attr_lower:
        return "Scat"

    return None


def _normalize_selected_attr_text(text):
    if text is None:
        return ""
    text = str(text).lower().strip()
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _matches_selected_attr(v_name, label_prefix, selected_attr):
    if not selected_attr:
        return True

    sel_lower = _normalize_selected_attr_text(selected_attr)
    prefix_lower = label_prefix.lower()
    v_name_lower = _normalize_selected_attr_text(v_name)

    if sel_lower in v_name_lower or v_name_lower in sel_lower:
        return True
    if prefix_lower == "abs" and "absorption" in sel_lower:
        return True
    if prefix_lower == "backscat" and ("backscattering" in sel_lower or "backscatter" in sel_lower):
        return True
    if prefix_lower == "scat":
        is_backscattering_selected = "backscattering" in sel_lower or "backscatter" in sel_lower
        is_scattering_selected = "scattering" in sel_lower or "scatter" in sel_lower
        return is_scattering_selected and not is_backscattering_selected
    return False


def _get_wavelength_values(data_array):
    for name in ("Wavelength", "wavelength"):
        if name in data_array.coords:
            vals = data_array[name].values
            if np.ndim(vals) == 0:
                vals = [vals.item()]
            return name, list(vals), name in data_array.dims
    return None, [], False


def _yaxis_title_for_attr(selected_attr):
    if not selected_attr:
        return "Coefficient [1/Mm]"
    attr = selected_attr.lower()
    if "absorption" in attr:
        return "Aerosol particle light absorption coefficient [1/Mm]"
    if "backscattering" in attr or "backscatter" in attr:
        return "Aerosol particle light backscattering coefficient [1/Mm]"
    if "scattering" in attr or "scatter" in attr:
        return "Aerosol particle light scattering coefficient [1/Mm]"
    if "equivalent_black_carbon" in attr or "black carbon" in attr:
        return "Equivalent black carbon [ng/m3]"
    return "Coefficient [1/Mm]"


def plot_line_datasets(datasets, selected_attr=None, selected_station=None,
                       selected_year=None, selected_instrument=None,
                       selected_model=None):
    """Plot ordinary ACTRIS/NRT time-series variables with Plotly.

    Handles wavelength-dependent absorption/scattering variables in the same
    style as the previous ACTRIS_insitu.plot_datasets implementation.
    """
    custom_colors = [
        "#9400D3", "#0000FF", "#00FF00", "#FFD700", "#FF0000",
        "#8B0000", "#000000", "#FF1493", "#00CED1", "#FF8C00",
    ]

    ds_list = _as_dataset_list(datasets)
    fig = go.Figure()
    trace_count = 0
    wavelength_color_map = {}
    next_color_idx = 0

    skip_patterns = ("perc", "std", "unc", "_min", "_max", "_range", "_median", "_qc", "qc_")

    for i, ds in enumerate(ds_list):
        try:
            targets = []

            if isinstance(ds, xr.DataArray):
                lbl = _variable_type_label(ds.name, ds, selected_attr) or "Var"
                targets.append((ds, lbl))
            elif isinstance(ds, xr.Dataset):
                for v_name in ds.data_vars:
                    if any(p in v_name.lower() for p in skip_patterns):
                        continue
                    v_data = ds[v_name]
                    if "time" not in v_data.dims and "time" not in v_data.coords:
                        continue
                    lbl = _variable_type_label(v_name, v_data, selected_attr)
                    if lbl is None:
                        # Generic fallback: allow selected variable datasets from NRT.
                        if selected_attr and (selected_attr.lower() in v_name.lower() or v_name.lower() in selected_attr.lower()):
                            lbl = _clean_label(v_name)
                        elif len(ds.data_vars) == 1:
                            lbl = _clean_label(v_name)
                    if lbl:
                        targets.append((v_data, lbl))
            else:
                continue

            if not targets:
                print(f"Dataset {i} has no recognizable variables.")
                continue

            seen_var_names = set()
            unique_targets = []
            for v_data, lbl in targets:
                var_name = v_data.name if hasattr(v_data, "name") else str(v_data)
                if var_name not in seen_var_names:
                    seen_var_names.add(var_name)
                    unique_targets.append((v_data, lbl))

            for main_var_data, label_prefix in unique_targets:
                main_var_data, _ = _filter_dataarray_to_year(main_var_data, selected_year)
                if "time" in main_var_data.coords and main_var_data.sizes.get("time", 0) == 0:
                    continue
                if "time" not in main_var_data.coords:
                    continue

                is_visible = True if _matches_selected_attr(main_var_data.name, label_prefix, selected_attr) else "legendonly"
                times = pd.to_datetime(main_var_data["time"].values)

                wdim, wavelengths, has_wavelength_dim = _get_wavelength_values(main_var_data)
                if wavelengths:
                    for wl in wavelengths:
                        try:
                            wl_key = float(wl)
                            wl_text = f"{wl:g}" if isinstance(wl, (int, float, np.integer, np.floating)) else str(wl)
                        except Exception:
                            wl_key = str(wl)
                            wl_text = str(wl)

                        if wl_key not in wavelength_color_map:
                            wavelength_color_map[wl_key] = custom_colors[next_color_idx % len(custom_colors)]
                            next_color_idx += 1
                        current_color = wavelength_color_map[wl_key]

                        if has_wavelength_dim:
                            y_da = main_var_data.sel({wdim: wl}, method="nearest")
                        else:
                            y_da = main_var_data
                        if y_da.ndim > 1:
                            extra_dims = [d for d in y_da.dims if d != "time"]
                            y_da = y_da.mean(dim=extra_dims, skipna=True)
                        y_values = np.asarray(y_da.values)
                        if not np.isfinite(y_values).any():
                            continue
                        x_plot, y_plot = _insert_time_gaps(times, y_values)

                        lbl_mean = f"{label_prefix}<sub>{wl_text}</sub> mean"
                        group_name = f"{label_prefix}_{wl_key}"
                        fig.add_trace(go.Scatter(
                            x=x_plot,
                            y=y_plot,
                            mode="lines",
                            name=lbl_mean,
                            line=dict(color=current_color, width=2),
                            connectgaps=False,
                            visible=is_visible,
                            showlegend=True,
                            legendgroup=group_name,
                        ))
                        trace_count += 1
                else:
                    y_da = main_var_data
                    if y_da.ndim > 1:
                        extra_dims = [d for d in y_da.dims if d != "time"]
                        y_da = y_da.mean(dim=extra_dims, skipna=True)
                    y_values = np.asarray(y_da.values)
                    if not np.isfinite(y_values).any():
                        continue
                    x_plot, y_plot = _insert_time_gaps(times, y_values)

                    wl_attr = main_var_data.attrs.get("wavelength")
                    if wl_attr:
                        lbl_mean = f"{label_prefix}<sub>{wl_attr}</sub> mean"
                        wl_key = str(wl_attr)
                    else:
                        lbl_mean = f"{label_prefix} Mean" if label_prefix in ("Abs", "Scat", "Backscat") else str(label_prefix)
                        wl_key = "unknown"
                    if wl_key not in wavelength_color_map:
                        wavelength_color_map[wl_key] = custom_colors[next_color_idx % len(custom_colors)]
                        next_color_idx += 1
                    current_color = wavelength_color_map[wl_key]
                    group_name = f"{label_prefix}_{wl_key}"

                    fig.add_trace(go.Scatter(
                        x=x_plot,
                        y=y_plot,
                        mode="lines",
                        name=lbl_mean,
                        line=dict(color=current_color, width=2),
                        connectgaps=False,
                        visible=is_visible,
                        showlegend=True,
                        legendgroup=group_name,
                    ))
                    trace_count += 1

        except Exception as exc:
            print(f"Error plotting dataset {i}: {exc}")

    year_str = _selected_year_to_str(selected_year)
    if trace_count == 0:
        if year_str:
            print(f"No data available for selected year {year_str}.")
        else:
            print("No plottable data found for the current selection.")
        return None

    fig.update_layout(
        title=dict(text=_build_title(selected_instrument, selected_attr, selected_station,
                                     selected_year, selected_model),
                   x=0.5, xanchor="center", automargin=True),
        yaxis_title=_yaxis_title_for_attr(selected_attr),
        xaxis_title="Time",
        template="plotly_white",
        hovermode="x unified",
        showlegend=True,
        legend=dict(yanchor="top", y=1, xanchor="left", x=1.02, title_text="Wavelengths"),
        margin=dict(r=150, l=80, t=80),
    )
    # The y-axis title is long ("aerosol particle light absorption coefficient [1/Mm]") and
    # plotly does not account for it when sizing the margins: in a narrow window it ends up
    # on top of the plot title. automargin makes the axis claim the room it needs.
    fig.update_yaxes(automargin=True)
    fig.show()
    return fig


# -----------------------------------------------------------------------------
# Public dispatcher
# -----------------------------------------------------------------------------

def plot_datasets(datasets, selected_attr=None, selected_station=None, selected_year=None,
                  selected_instrument=None, selected_model=None):
    """Dispatch to the appropriate ACTRIS/NRT Plotly plot.

    This function is shared by both the ACTRIS in-situ notebook and the NRT
    notebook.  It does not depend on metadata archives, THREDDS filename search,
    or any global state from ACTRIS_insitu.py.
    """
    ds_list = _as_dataset_list(datasets)
    if not ds_list:
        print("No datasets provided.")
        return None

    print(f"Plotting {len(ds_list)} dataset(s).")
    if selected_attr:
        print(f"Selected attribute: {selected_attr}, Station: {selected_station}")

    if _is_size_distribution(selected_attr, selected_instrument):
        return plot_particle_size_distribution(
            ds_list,
            selected_attr=selected_attr,
            selected_station=selected_station,
            selected_year=selected_year,
            selected_instrument=selected_instrument,
            selected_model=selected_model,
        )

    if _is_mass_spectrometer(selected_instrument):
        return plot_mass_spectrometer(
            ds_list,
            selected_attr=selected_attr,
            selected_station=selected_station,
            selected_year=selected_year,
            selected_instrument=selected_instrument,
            selected_model=selected_model,
        )

    return plot_line_datasets(
        ds_list,
        selected_attr=selected_attr,
        selected_station=selected_station,
        selected_year=selected_year,
        selected_instrument=selected_instrument,
        selected_model=selected_model,
    )
