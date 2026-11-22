"""ACTRIS-style widgets for NRT data, backed by df_files instead of metadata_archive."""

import re
import traceback
from pathlib import Path

import pandas as pd
import xarray as xr
import ipywidgets as widgets
from IPython.display import display

import nrt_utils as nu
import actris_plotting_utilities as ap
from nrt_config import get_variable_rule

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
    _PLOTLY_ERROR = None
except Exception as _exc:  # noqa: BLE001 - import can fail for reasons other than ImportError
    _HAS_PLOTLY = False
    _PLOTLY_ERROR = _exc

_STATIONS_CSV = Path(__file__).with_name("nrt_stations.csv")

# Coordinates in nrt_stations.csv are sexagesimal strings such as 40°23'04"N / 044°15'38"E
_DMS_RE = re.compile(r"""^\s*(\d+)\s*°\s*(\d+)\s*'\s*([\d.]+)\s*"?\s*([NSEW])\s*$""", re.X)


def _dms_to_decimal(value):
    """Convert a 40°23'04\"N style string to decimal degrees; return None if not parseable."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _DMS_RE.match(text)
    if match:
        degrees, minutes, seconds, hemisphere = match.groups()
        decimal = int(degrees) + int(minutes) / 60 + float(seconds) / 3600
        return -decimal if hemisphere in ("S", "W") else decimal
    try:
        return float(text)  # already decimal degrees
    except ValueError:
        return None


# The station-name lookup is cosmetic: if the CSV is missing or unreadable, fall back to bare
# station codes instead of making the whole module fail to import.
_STATION_NAMES = {}
_STATION_COORDS = {}
_STATIONS_ERROR = None
try:
    _stations_df = pd.read_csv(_STATIONS_CSV)
    _STATION_NAMES = _stations_df.set_index("Code")["Station name"].to_dict()
    for _row in _stations_df.itertuples(index=False):
        _lat = _dms_to_decimal(getattr(_row, "latitude", None))
        _lon = _dms_to_decimal(getattr(_row, "longitude", None))
        if _lat is not None and _lon is not None:
            _STATION_COORDS[_row.Code] = (_lat, _lon)
except Exception as exc:  # noqa: BLE001 - any read problem must stay non-fatal
    _STATIONS_ERROR = f"{type(exc).__name__}: {exc}"
    print(f"WARNING: could not read {_STATIONS_CSV} ({_STATIONS_ERROR}); "
          f"facilities will be shown by station code only, without a map.")


def station_table_status():
    """Report whether nrt_stations.csv loaded, for troubleshooting the facility map."""
    if _STATIONS_ERROR:
        return f"NOT LOADED from {_STATIONS_CSV} -> {_STATIONS_ERROR}"
    return (f"loaded from {_STATIONS_CSV}: {len(_STATION_NAMES)} names, "
            f"{len(_STATION_COORDS)} with coordinates")


def _station_label(code):
    name = _STATION_NAMES.get(code)
    return f"{name} ({code})" if name else code


# Values the interface opens on, so that the first plot a user sees is a meaningful one rather
# than whatever happens to come first alphabetically. Each is applied only if it is actually
# available in the data; otherwise the first option is used, as before. They apply to the initial
# build only - once the user starts changing things, the cascade behaves normally.
DEFAULT_SELECTION = {
    "instrument": "filter absorption photometer",
    "variable": "aerosol_light_absorption_coefficient",
    "facility": "AT0034G",          # Sonnblick
    "start_date": "2025-06-07",
    "end_date": "2025-06-10",
    "start_hour": 0,
    "end_hour": 23,
}


def _preferred(values, wanted, fallback=None):
    """The wanted value when it is among `values`, otherwise the fallback (first by default).

    Matching is done on the string form as well as the value itself: the day dropdowns are
    filled with whatever `available_days()` returns - dates, timestamps or strings depending on
    the file - so comparing "2025-06-07" directly would silently miss. The element actually
    present in `values` is returned, so the widget keeps its own type.
    """
    values = list(values)
    if not values:
        return None
    if wanted is not None:
        wanted_str = str(wanted)
        for value in values:
            if value == wanted or str(value) == wanted_str or str(value).startswith(wanted_str):
                return value
    return values[0] if fallback is None else fallback


# The dataset currently loaded in the interface. build_app keeps its state in a local dict, which
# the notebook cannot reach; this module-level reference is what lets the last cell save it.
_LAST_STATE = None


def current_dataset():
    """The dataset shown by the interface, variables already selected, or None if nothing is loaded."""
    if not _LAST_STATE:
        return None
    return _LAST_STATE.get("ds_selected") or _LAST_STATE.get("ds")


def build_app(df_files, defaults=None):
    """Build the NRT explorer.

    `defaults` overrides DEFAULT_SELECTION; pass {} to open on the first option everywhere.
    """
    prefs = DEFAULT_SELECTION if defaults is None else {**DEFAULT_SELECTION, **defaults}
    first_build = {"pending": True}          # cleared once the initial cascade has run

    df_files = nu.prepare_nrt_file_index(df_files)
    if df_files.empty:
        return widgets.HTML("<b>No NRT files found.</b>")

    instrument_options = nu.get_nrt_instruments(df_files)
    if not instrument_options:
        return widgets.HTML("<b>No supported NRT instruments found.</b>")

    instrument_dd = widgets.Dropdown(
        options=instrument_options,
        value=_preferred(instrument_options, prefs.get("instrument")),
        description="Instrument:", style={"description_width": "initial"})
    variable_dd = widgets.Dropdown(options=[], description="Variable:", style={"description_width": "initial"})
    station_dd = widgets.Dropdown(options=[], description="Facility:", style={"description_width": "initial"})
    confirm_btn = widgets.Button(description="Refresh file list", button_style="success", icon="refresh")

    file_dd = widgets.Dropdown(options=[], description="File:", layout=widgets.Layout(width="980px"))
    load_btn = widgets.Button(description="Load selected file", button_style="primary")
    plot_btn = widgets.Button(description="Plot", button_style="success")

    start_day_dd = widgets.Dropdown(description="Start date:", options=[])
    end_day_dd = widgets.Dropdown(description="End date:", options=[])
    start_hour = widgets.Dropdown(description="Start hour:", options=list(range(24)), value=0)
    end_hour = widgets.Dropdown(description="End hour:", options=list(range(24)), value=23)
    start_box = widgets.HBox([start_day_dd, start_hour])
    end_box = widgets.HBox([end_day_dd, end_hour])
    time_selection_box = widgets.VBox([start_box, end_box])
    year_widget_box = widgets.VBox([])
    year_widget_out = widgets.Output()

    out = widgets.Output()
    time_out = widgets.Output()
    map_out = widgets.Output()
    inspect_out = widgets.Output(layout=widgets.Layout(border="1px solid #ddd"))
    citation_out = widgets.Output(layout=widgets.Layout(border="1px solid #ddd"))

    state = {"ds": None, "ds_selected": None, "url": None, "year_widget": None}
    global _LAST_STATE
    _LAST_STATE = state

    # Set while widgets are updated programmatically, so the cascade of observers doesn't
    # re-run the (expensive) filtering several times for a single user action.
    updating = {"busy": False}

    def format_file_option(row):
        parts = []
        if "source_catalog" in row and pd.notna(row["source_catalog"]):
            parts.append(str(row["source_catalog"]))
        if "start_dt" in row and pd.notna(row["start_dt"]):
            parts.append(f"start={row['start_dt']:%Y-%m-%d}")
        if "created_dt" in row and pd.notna(row["created_dt"]):
            parts.append(f"created={row['created_dt']:%Y-%m-%d %H:%M}")
        meta = " | ".join(parts)
        return f"{meta} | {row['filename']}" if meta else row["filename"]

    def reset_loaded_dataset():
        # Close the previous remote dataset: every "Load" opens an OPeNDAP connection, and
        # dropping the reference without closing leaks file handles/sockets over a session.
        for key in ("ds_selected", "ds"):
            obj = state.get(key)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        state.update({"ds": None, "ds_selected": None, "url": None, "year_widget": None})
        start_day_dd.options = []
        end_day_dd.options = []
        year_widget_box.children = []
        time_selection_box.children = [start_box, end_box]
        with year_widget_out:
            year_widget_out.clear_output()
        with time_out:
            time_out.clear_output()
        with inspect_out:
            inspect_out.clear_output()

    def update_variables(*args):
        if updating["busy"]:
            return
        updating["busy"] = True
        try:
            attrs = nu.get_nrt_attributes_for_instrument(instrument_dd.value)
            variable_dd.options = attrs
            variable_dd.value = (_preferred(attrs, prefs.get("variable"))
                                 if first_build["pending"] else (attrs[0] if attrs else None))
        finally:
            updating["busy"] = False
        update_stations()

    def update_stations(*args):
        if updating["busy"]:
            return
        updating["busy"] = True
        try:
            stations = nu.get_nrt_stations_for_selection(df_files, instrument_dd.value, variable_dd.value)
            station_dd.options = [(_station_label(code), code) for code in stations]
            station_dd.value = (_preferred(stations, prefs.get("facility"))
                                if first_build["pending"] else (stations[0] if stations else None))
        finally:
            updating["busy"] = False
        reset_loaded_dataset()
        update_file_options()
        draw_station_map()

    def update_file_options(*args):
        reset_loaded_dataset()
        df_sel = nu.filter_nrt_files(df_files, instrument_dd.value, variable_dd.value, station_dd.value)
        options = [(format_file_option(row), row["opendap_url"]) for _, row in df_sel.iterrows()]
        updating["busy"] = True
        try:
            file_dd.options = options
            file_dd.value = options[0][1] if options else None
        finally:
            updating["busy"] = False
        with out:
            out.clear_output()
            print(f"Found {len(options)} file(s) for {instrument_dd.value} | {variable_dd.value} | {station_dd.value}.")
            if options:
                print("The most recent file is preselected - press 'Load selected file' to open it.")

    def draw_station_map(*_):
        """Map the facilities available for the current instrument/variable selection."""
        with map_out:
            map_out.clear_output(wait=True)
            if not _HAS_PLOTLY:
                print(f"Map unavailable: plotly could not be imported ({_PLOTLY_ERROR}). "
                      f"Install it with: pip install plotly")
                return
            codes = [code for _, code in station_dd.options] if station_dd.options else []
            if not codes:
                print("No facility to show on the map for this selection.")
                return

            located = [c for c in codes if c in _STATION_COORDS]
            missing = [c for c in codes if c not in _STATION_COORDS]
            if not located:
                if not _STATION_COORDS:
                    print("Map unavailable: the station coordinate table was not loaded.")
                    print(f"  {station_table_status()}")
                else:
                    print("None of the facilities for this selection has coordinates in "
                          f"{_STATIONS_CSV.name}: {', '.join(missing[:10])}")
                return

            current = station_dd.value
            others = [c for c in located if c != current]
            selected = [c for c in located if c == current]

            figure = go.Figure()
            if others:
                figure.add_trace(go.Scattergeo(
                    lon=[_STATION_COORDS[c][1] for c in others],
                    lat=[_STATION_COORDS[c][0] for c in others],
                    text=[_station_label(c) for c in others],
                    hoverinfo="text", mode="markers", name="Available facilities",
                    marker=dict(size=8, color="#3B4872", line=dict(width=1, color="white")),
                ))
            if selected:
                figure.add_trace(go.Scattergeo(
                    lon=[_STATION_COORDS[c][1] for c in selected],
                    lat=[_STATION_COORDS[c][0] for c in selected],
                    text=[_station_label(c) for c in selected],
                    hoverinfo="text", mode="markers+text", name="Selected",
                    textposition="top center",
                    marker=dict(size=15, color="crimson", line=dict(width=1.5, color="white")),
                ))
            # Scattergeo uses Plotly's built-in geometry, so no external map tiles are needed
            figure.update_geos(fitbounds="locations", resolution=50, showcountries=True,
                               countrycolor="#888", showland=True, landcolor="#f2efe9",
                               showocean=True, oceancolor="#dbe9f2", showlakes=False)
            title = f"{len(located)} facility(ies) with NRT data for {instrument_dd.value}"
            if missing:
                title += f" - {len(missing)} without coordinates: {', '.join(missing[:5])}"
                if len(missing) > 5:
                    title += ", ..."
            figure.update_layout(height=420, margin=dict(l=0, r=0, t=40, b=0),
                                 showlegend=False, title=dict(text=title, font=dict(size=13)))
            # Always print a one-line summary: if the map area below stays blank, this line still
            # shows whether the code ran and what it tried to draw.
            print(f"Map: {len(located)} facility(ies), selected {current}")
            # display() emits the Plotly mimebundle, which renders inside an ipywidgets Output.
            # fig.show() relies on the global renderer and often stays blank in this context.
            display(figure)

    def update_end_day_options(*args):
        days = list(start_day_dd.options) if start_day_dd.options else []
        if not days or start_day_dd.value is None:
            end_day_dd.options = []
            end_day_dd.value = None
            return
        i0 = days.index(start_day_dd.value) if start_day_dd.value in days else 0
        end_opts = days[i0:]
        end_day_dd.options = end_opts
        end_day_dd.value = end_opts[-1]

    def on_load_clicked(_):
        # The whole handler runs inside `out`: exceptions raised in a widget callback are not
        # displayed by ipywidgets, so without this an OPeNDAP failure would look like the
        # button simply doing nothing.
        with out:
            out.clear_output()
            selected_url = file_dd.value
            if not selected_url:
                print("No file selected.")
                return

            load_btn.disabled = True
            load_btn.description = "Loading..."
            try:
                print("Loading selected URL:\n", selected_url)
                print("(streaming over OPeNDAP - this can take a while for large files)")

                try:
                    ds = xr.open_dataset(selected_url).sortby("time")
                except Exception as exc:  # noqa: BLE001 - report any remote/read failure to the user
                    print(f"\nERROR: could not open the dataset ({type(exc).__name__}: {exc})")
                    print("The THREDDS server may be unreachable or the file temporarily unavailable. "
                          "Try another file, or re-run the catalog cells to refresh the index.")
                    return

                reset_loaded_dataset()
                citation = ds.attrs.get("citation")
                state["ds"] = ds
                state["url"] = selected_url
                ds_selected = nu.select_nrt_variables(ds, instrument_dd.value, variable_dd.value)
                state["ds_selected"] = ds_selected

                if not ds_selected.data_vars:
                    print("\nWARNING: no variable matching "
                          f"'{variable_dd.value}' was found in this file. "
                          "The dataset is still loaded, but there may be nothing to plot.")

                tindex = nu.get_time_index(ds_selected if "time" in ds_selected.coords else ds)

                days = nu.available_days(tindex)
                start_day_dd.options = days
                start_day_dd.value = (_preferred(days, prefs.get("start_date"))
                                      if first_build["pending"] else (days[0] if days else None))
                update_end_day_options()
                if first_build["pending"]:
                    end_day_dd.value = _preferred(list(end_day_dd.options), prefs.get("end_date"),
                                                  fallback=end_day_dd.value)
                    for label, dd, key in (("start", start_day_dd, "start_date"),
                                           ("end", end_day_dd, "end_date")):
                        wanted = prefs.get(key)
                        if wanted and not str(dd.value).startswith(str(wanted)):
                            print(f"Note: the example {label} date {wanted} is not in this file; "
                                  f"using {dd.value} instead.")
                    for dd, key in ((start_hour, "start_hour"), (end_hour, "end_hour")):
                        if prefs.get(key) in list(dd.options):
                            dd.value = prefs[key]
                    first_build["pending"] = False

                with time_out:
                    time_out.clear_output()
                    if len(tindex) > 0:
                        print("Dataset time coverage:")
                        print("  Start:", tindex.min())
                        print("  End  :", tindex.max())
                        print("  N:", len(tindex))
                    else:
                        print("This dataset contains no time steps.")
                with citation_out:
                    citation_out.clear_output()
                    print(citation if citation else "No citation information found.")
                with inspect_out:
                    inspect_out.clear_output()
                    display(ds_selected)
                    display(nu.dataset_variable_overview(ds_selected))

                # Optional year selector for multi-year PNSD datasets.
                year_widget_box.children = []
                state["year_widget"] = None
                time_selection_box.children = [start_box, end_box]
                with year_widget_out:
                    year_widget_out.clear_output()
                    if instrument_dd.value == "mobility particle size spectrometer":
                        yw = ap.create_pnsd_year_widget(ds_selected)
                        if yw is not None:
                            state["year_widget"] = yw
                            year_widget_box.children = [year_widget_out]
                            time_selection_box.children = []
                if state["year_widget"] is None:
                    year_widget_box.children = []

                print("\nDataset loaded. Choose a time window and press 'Plot'.")

            except Exception:  # noqa: BLE001 - never let a callback fail silently
                print("Unexpected error while loading the dataset:")
                traceback.print_exc()
            finally:
                load_btn.disabled = False
                load_btn.description = "Load selected file"

    def _current_ds_plot():
        ds = state.get("ds_selected")
        if ds is None:
            return None

        if state.get("year_widget") is not None:
            return ds

        if not start_day_dd.value or not end_day_dd.value:
            print("Choose start/end date.")
            return None

        if (start_day_dd.value == end_day_dd.value
                and int(start_hour.value) > int(end_hour.value)):
            print(f"The start hour ({start_hour.value}:00) is after the end hour "
                  f"({end_hour.value}:59) on the same day - nothing to plot.")
            return None

        return nu.slice_datetime_range(
            ds,
            start_day_dd.value,
            start_hour.value,
            end_day_dd.value,
            end_hour.value,
        )

    def on_plot_clicked(_):
        with out:
            out.clear_output()
            plot_btn.disabled = True
            plot_btn.description = "Plotting..."
            try:
                ds_plot = _current_ds_plot()
                if ds_plot is None:
                    if state.get("ds_selected") is None:
                        print("Load a dataset first.")
                    return
                if ds_plot.sizes.get("time", 0) == 0:
                    print("No data in selected window.")
                    return
                rule = get_variable_rule(instrument_dd.value, variable_dd.value)
                selected_attr = rule.get("selected_attr", variable_dd.value) if rule else variable_dd.value
                selected_year = state["year_widget"].value if state.get("year_widget") is not None else None
                selected_model = state["ds"].attrs.get("ebas_instrument_model") or state["ds"].attrs.get("instrument_model") if state.get("ds") is not None else None
                ap.plot_datasets(
                    ds_plot,
                    selected_attr=selected_attr,
                    selected_station=station_dd.value,
                    selected_year=selected_year,
                    selected_instrument=instrument_dd.value,
                    selected_model=selected_model,
                )
            except Exception:  # noqa: BLE001 - show plotting errors instead of swallowing them
                print("Unexpected error while plotting:")
                traceback.print_exc()
            finally:
                plot_btn.disabled = False
                plot_btn.description = "Plot"

    def _on_selection_change(*_):
        if not updating["busy"]:
            reset_loaded_dataset()

    def on_station_change(*_):
        # Ignore the change if it was made programmatically by update_stations(), which
        # refreshes the file list itself right afterwards.
        if updating["busy"]:
            return
        update_file_options()
        draw_station_map()

    instrument_dd.observe(update_variables, names="value")
    variable_dd.observe(update_stations, names="value")
    station_dd.observe(on_station_change, names="value")
    confirm_btn.on_click(update_file_options)
    file_dd.observe(_on_selection_change, names="value")
    load_btn.on_click(on_load_clicked)
    plot_btn.on_click(on_plot_clicked)
    start_day_dd.observe(update_end_day_options, names="value")

    # Populate everything once at startup, so the file list is already usable without the user
    # having to press a button first.
    update_variables()

    return widgets.VBox([
        widgets.HTML("<b>Choose instrument, variable, and facility:</b>"),
        instrument_dd,
        variable_dd,
        station_dd,
        map_out,
        confirm_btn,
        widgets.HTML("<b>Choose NRT file:</b>"),
        file_dd,
        load_btn,
        inspect_out,
        time_out,
        widgets.HTML("<b>Citation for use of this dataset:</b>"),
        citation_out,
        widgets.HTML("<b>Select time window:</b>"),
        time_selection_box,
        year_widget_box,
        plot_btn,
        out,
    ])
