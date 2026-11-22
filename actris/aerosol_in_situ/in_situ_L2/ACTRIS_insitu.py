import ipywidgets as widgets
from IPython.display import HTML, display
import plotly.graph_objects as go
import requests
import xarray as xr
import numpy as np
import re
import json
import ebas_plot
from copy import deepcopy
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm.notebook import tqdm

# Minimal, direct-HTTP reimplementation of the dvas-api-client pieces this
# script uses (that package requires Python >=3.13, unavailable in some VREs).
# Field paths and query/pagination behavior mirror dvas.client exactly, so
# results (facility/dataset counts, etc.) match the dvas-client-based version.
PROD_API = "https://prod-actris-md.nilu.no"


class SortType:
    ASC = "asc"
    DESC = "desc"


class QueryField:
    DATASET_REPOSITORY_ID = "dataset_metadata.repository.repository_id.keyword"
    IDENTIFICATION_PID = "identification.identifier.pid.keyword"
    FACILITY_NAME = "facility.name.keyword"
    VARIABLE_NAME = "variables.variable_name.keyword"
    VARIABLE_INSTRUMENT_TYPE = "variables.instrument.instrument_type.keyword"
    DISTRIBUTION_ACCESS_RESTRICTED = "distribution_information.access_restriction.restricted"


class BoolQuery:
    """Chainable Elasticsearch bool-query builder matching dvas.client.BoolQuery's must/must_not/sort/to_dict."""

    def __init__(self, size: int = 10):
        self._must: list[dict] = []
        self._must_not: list[dict] = []
        self._sort: list[tuple[str, str]] | None = None
        self._size = size
        self._search_after = None

    def must(self, field: str, value) -> "BoolQuery":
        self._must.append({"term": {field: value}})
        return self

    def must_not(self, field: str, value) -> "BoolQuery":
        self._must_not.append({"term": {field: value}})
        return self

    def sort(self, sort: list[tuple[str, str]] | None) -> "BoolQuery":
        self._sort = sort
        return self

    def to_dict(self) -> dict:
        bool_clause = {}
        if self._must:
            bool_clause["must"] = self._must
        if self._must_not:
            bool_clause["must_not"] = self._must_not
        query = {"query": {"bool": bool_clause} if bool_clause else {"match_all": {}}}
        if self._size:
            query["size"] = self._size
        if self._sort:
            query["sort"] = [{field: {"order": order}} for field, order in self._sort]
        if self._search_after:
            query["search_after"] = self._search_after
        return query


def search(api: str, query: BoolQuery, timeout: float = 30.0):
    """Yield all matching documents, paginating via search_after exactly like dvas.client.search."""
    q = deepcopy(query)
    while True:
        response = requests.post(f"{api}/api/metadata/search", json={"search": q.to_dict()}, timeout=timeout)
        response.raise_for_status()
        hits_block = response.json().get("response", {}).get("hits", {})
        max_hits = hits_block.get("total", {}).get("value", 0)
        hits = hits_block.get("hits", [])
        for doc in hits:
            yield doc["_source"]
        if max_hits > q._size and q._sort and len(hits) > 0:
            q._search_after = hits[-1]["sort"]
        else:
            return

# Each instrument maps to {variable: constraints}; constraints add must_not exclusions where API terms overlap (e.g. subtypes, or eBC vs absorption coefficient).
_TARGETS = {
    "filter absorption photometer": {
        "aerosol particle light absorption coefficient": {
            "exclude_variables": ("aerosol particle equivalent black carbon mass concentration",),
        },
        "aerosol particle equivalent black carbon mass concentration": {},
    },
    "mobility particle size spectrometer": {
        "aerosol particle number size distribution": {},
    },
    "particle size spectrometer": {
        "aerosol particle number size distribution": {
            "exclude_instrument_types": (
                "optical particle size spectrometer",
                "mobility particle size spectrometer",
            ),
        },
    },
    "integrating nephelometer": {
        "aerosol particle light scattering coefficient": {},
        "aerosol particle light hemispheric backscatter coefficient": {},
    },
    "particle mass spectrometer": {
        "aerosol particle ammonium mass concentration": {},
        "aerosol particle chloride mass concentration": {},
        "aerosol particle nitrate mass concentration": {},
        "aerosol particle organics mass concentration": {},
        "aerosol particle sulphate mass concentration": {},
    },
    "condensation particle counter": {
        "aerosol particle number concentration": {},
    },
}

_metadata = {}
_widget_state = {"instrument": None, "variable": None, "station": None, "model": None, "doi": None}


def _model_label(instrument):
    metadata = (instrument.get("extra_metadata") or {}).get("insitu") or {}
    manufacturer = metadata.get("ebas_instrument_manufacturer", "").strip()
    model = metadata.get("ebas_instrument_model", "").strip()
    name = instrument.get("instrument_name", "").strip()
    return f"{manufacturer} {model}".strip() if model else name


def _model_key(label: str) -> str:
    """Normalize a model label for equivalence checks (e.g. 'TSI 3321' == 'TSI3321')."""
    return re.sub(r"\s+", "", label).lower()


def _matches_model(document, model):
    return not model or any(
        _model_key(_model_label(instrument)) == _model_key(model)
        for variable in document.get("variables", [])
        for instrument in variable.get("instrument", [])
    )


def _is_value_change(change):
    """True for an ipywidgets observe() callback firing on a plain value change."""
    return change["type"] == "change" and change["name"] == "value"


def _target_constraints(instrument, variable):
    """Return the must_not constraints dict configured for an instrument/variable combo."""
    return _TARGETS.get(instrument, {}).get(variable, {})


def _dataset_query(instrument, variable, station=None, size=10000):
    constraints = _target_constraints(instrument, variable)
    query = (
        BoolQuery(size=size)
        .must(QueryField.DATASET_REPOSITORY_ID, "In-Situ")
        .must(QueryField.VARIABLE_INSTRUMENT_TYPE, instrument)
        .must(QueryField.VARIABLE_NAME, variable)
        .must_not(QueryField.DISTRIBUTION_ACCESS_RESTRICTED, True)
    )
    for excluded_type in constraints.get("exclude_instrument_types", ()):
        query = query.must_not(QueryField.VARIABLE_INSTRUMENT_TYPE, excluded_type)
    for excluded_variable in constraints.get("exclude_variables", ()):
        query = query.must_not(QueryField.VARIABLE_NAME, excluded_variable)
    if station:
        query = query.must(QueryField.FACILITY_NAME, station).sort(
            [(QueryField.IDENTIFICATION_PID, SortType.ASC)]
        )
    return query


def _format_date(iso: str | None) -> str:
    """Format an ISO 8601 timestamp as DD.MM.YY, or '?' if missing/unparseable."""
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d.%m.%y")
    except ValueError:
        return iso[:10]


def _fetch_models(instrument, variable, station):
    """Return sorted (label, model) pairs, label appending each model's time coverage, e.g. 'Magee AE31 (12.05.06-01.01.26)'."""
    by_key: dict[str, dict] = {}
    for document in search(PROD_API, _dataset_query(instrument, variable, station, size=1000)):
        extent = document.get("temporal_extent") or {}
        begin = extent.get("time_period_begin")
        end = extent.get("time_period_end")
        labels = {
            label
            for variable_data in document.get("variables", [])
            for instrument_data in variable_data.get("instrument", [])
            if (label := _model_label(instrument_data))
        }
        for label in labels:
            key = _model_key(label)
            entry = by_key.setdefault(key, {"label": label, "begin": begin, "end": end})
            if " " in label and " " not in entry["label"]:
                entry["label"] = label
            if begin and (entry["begin"] is None or begin < entry["begin"]):
                entry["begin"] = begin
            if end and (entry["end"] is None or end > entry["end"]):
                entry["end"] = end

    return sorted(
        (f"{e['label']} ({_format_date(e['begin'])}-{_format_date(e['end'])})", e["label"])
        for e in by_key.values()
    )


def _fetch_stations(instrument, variable):
    def _coords(fac, doc):
        """Fall back from a facility's GeoJSON Point to the doc's MultiPoint."""
        point = (fac.get("location") or {}).get("coordinates")
        if isinstance(point, list) and len(point) >= 2:
            return point[0], point[1]
        multi = (doc.get("spatial_extent") or {}).get("coordinates")
        if isinstance(multi, list) and multi and isinstance(multi[0], list) and len(multi[0]) >= 2:
            return multi[0][0], multi[0][1]
        return None, None

    stations = {}
    for doc in search(PROD_API, _dataset_query(instrument, variable, size=1000).sort(
        [(QueryField.FACILITY_NAME, SortType.ASC)]
    )):
        fac = doc.get("facility") or {}
        name = fac.get("name")
        if not name or name in stations:
            continue
        lat = fac.get("lat") or fac.get("latitude")
        lon = fac.get("lon") or fac.get("longitude")
        if lat is None or lon is None:
            lon, lat = _coords(fac, doc)
        cc = fac.get("country_code") or ""
        try:
            stations[name] = (
                (float(lat), float(lon), cc)
                if lat is not None and lon is not None
                else (None, None, cc)
            )
        except (TypeError, ValueError):
            stations[name] = (None, None, cc)
    return stations


def load_metadata():
    global _metadata
    combos = [(instr, var) for instr, vars_ in _TARGETS.items() for var in vars_.keys()]
    print(f"Loading metadata for {len(combos)} instrument/variable combinations...")
    with ThreadPoolExecutor(max_workers=min(8, len(combos))) as executor:
        futures = {executor.submit(_fetch_stations, *combo): combo for combo in combos}
        _metadata = {
            futures[future]: future.result()
            for future in tqdm(as_completed(futures), total=len(futures), desc="Loading metadata")
        }
    n_fac = len({name for v in _metadata.values() for name in v})
    print(f"Done. {len(_metadata)} combinations, {n_fac} unique facilities.        ")


def _get_stations(instrument, variable):
    return _metadata.get((instrument, variable), {})


def _stations_with_coords(instrument, variable):
    """Return sorted list of facility names that have coordinates."""
    return sorted(
        n for n, c in _get_stations(instrument, variable).items()
        if c[0] is not None
    )


# Values the interface opens on, so that the first selection a user makes is a meaningful one
# rather than whatever comes first in the list. Each is applied only if it is actually available
# in the metadata; otherwise the first option is used, as before. Set DEFAULT_SELECTION = {} to
# open on the first option everywhere.
DEFAULT_SELECTION = {
    "instrument": "filter absorption photometer",
    "variable": "aerosol_light_absorption_coefficient",
    "facility": "Sonnblick",
    # The 2025 Canadian fire season, when the smoke reached Europe. A period is needed because
    # a facility's record can span more than a decade of hourly data: plotted whole, it sent a
    # million points to the browser (47 MB of HTML for Sonnblick) and the page crashed.
    "start": "2025-05-01",
    "end": "2025-09-30",
}

# The browser receives a Plotly figure as HTML with every value written out, and pages of a few
# tens of megabytes are where it gives up. A figure larger than this is averaged over time -
# daily, then weekly, then monthly means - until it fits. Measured on the HTML rather than on
# the number of values, because the same values cost very different sizes as a line plot and
# as a size-distribution heatmap.
MAX_FIGURE_MB = 8

def _overlaps(document, start, end):
    """True unless the dataset's declared temporal extent lies entirely outside [start, end].

    start or end may be None (open-ended). Undated documents cannot be ruled out, so they are
    kept. Filtering here, on the metadata, is what keeps a short period from downloading the
    whole record first.
    """
    extent = document.get("temporal_extent") or {}
    begin, finish = extent.get("time_period_begin"), extent.get("time_period_end")
    try:
        d0 = np.datetime64(begin[:19]) if begin else None
        d1 = np.datetime64(finish[:19]) if finish else None
    except (TypeError, ValueError):
        return True
    return ((d0 is None or end is None or d0 <= end)
            and (d1 is None or start is None or d1 >= start))


def _period(state):
    """(start, end) as datetime64 from the state, None where not set; end is inclusive of its day."""
    start, end = state.get("start"), state.get("end")
    start = np.datetime64(str(start)[:10]) if start else None
    end = np.datetime64(str(end)[:10]) + np.timedelta64(1, "D") - np.timedelta64(1, "s") if end else None
    return start, end


def _figure_within_limits(ds, path="/tmp/concatenated_datasets.nc", max_mb=MAX_FIGURE_MB):
    """Plot ds with ebas_plot, averaging it over time until the figure's HTML fits in max_mb.

    Returns (html, note): note says what averaging was applied, or is None when none was. The
    full-resolution data are written to path first, so the notebook's save cell keeps them
    whatever the figure shows.
    """
    ds.to_netcdf(path)
    html = ebas_plot.plot(path).to_html(full_html=False, include_plotlyjs="cdn")
    if len(html) <= max_mb * 1e6 or "time" not in ds.dims:
        return html, None
    full_mb = len(html) / 1e6
    reduced_path = path.replace(".nc", "_averaged.nc")
    for rule, code, label in (("1D", "1d", "daily"), ("7D", "1w", "weekly"), ("30D", "1mo", "monthly")):
        reduced = ds.resample(time=rule).mean(keep_attrs=True)
        # ebas_plot lays a size distribution on a regular grid at the resolution the file
        # declares; left at "1h", monthly means come back as eleven years of hourly columns.
        reduced.attrs["ebas_resolution_code"] = code
        reduced.to_netcdf(reduced_path)
        html = ebas_plot.plot(reduced_path).to_html(full_html=False, include_plotlyjs="cdn")
        if len(html) <= max_mb * 1e6:
            break
    return html, (f"At full resolution this figure would be {full_mb:.0f} MB, more than the "
                  f"browser can draw: it shows {label} means instead. Choose a shorter period "
                  f"to see every measurement.")


def _preferred(values, wanted, fallback=None):
    """The wanted value when it is among `values`, otherwise the fallback (the first by default).

    Matching is done on the string form and on a leading substring as well, because the facility
    entries carry the station code along with the name ("Sonnblick (AT0034G)").
    """
    values = list(values)
    if not values:
        return None
    if wanted:
        wanted_str = str(wanted)
        for value in values:
            if value == wanted or str(value) == wanted_str or str(value).startswith(wanted_str):
                return value
    return values[0] if fallback is None else fallback


def create_search_widgets():
    """Display instrument/variable/facility/model dropdowns and a station map; call load_metadata() first."""
    if not _metadata:
        print("Warning: metadata not loaded. Call load_metadata() first.")

    instruments = list(_TARGETS.keys())
    default_instr = _preferred(instruments, DEFAULT_SELECTION.get("instrument"))
    initial_vars = list(_TARGETS[default_instr].keys())
    default_var = _preferred(initial_vars, DEFAULT_SELECTION.get("variable"))
    initial_station_list = [""] + _stations_with_coords(default_instr, default_var)
    # "" is the "nothing chosen yet" entry, so the default facility is looked for after it
    default_station = _preferred(initial_station_list[1:], DEFAULT_SELECTION.get("facility"),
                                 fallback="")

    _dd_layout = widgets.Layout(width="420px")
    _label_layout = widgets.Layout(width="90px")

    instrument_dd = widgets.Dropdown(
        options=instruments, value=default_instr,
        layout=_dd_layout,
    )
    variable_dd = widgets.Dropdown(
        options=initial_vars, value=default_var,
        layout=_dd_layout,
    )
    station_dd = widgets.Dropdown(
        options=initial_station_list, value=default_station,
        layout=_dd_layout,
    )
    model_dd = widgets.Dropdown(
        options=["— select a facility first —"], value="— select a facility first —",
        layout=_dd_layout,
        disabled=True,
    )
    _dd_rows = widgets.VBox([
        widgets.HBox([widgets.Label("Instrument:", layout=_label_layout), instrument_dd]),
        widgets.HBox([widgets.Label("Variable:", layout=_label_layout), variable_dd]),
        widgets.HBox([widgets.Label("Facility:", layout=_label_layout), station_dd]),
        widgets.HBox([widgets.Label("Model:", layout=_label_layout), model_dd]),
    ])
    def _date(value):
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date() if value else None
        except ValueError:
            return None

    start_dp = widgets.DatePicker(value=_date(DEFAULT_SELECTION.get("start")),
                                  layout=widgets.Layout(width="160px"))
    end_dp = widgets.DatePicker(value=_date(DEFAULT_SELECTION.get("end")),
                                layout=widgets.Layout(width="160px"))
    _dd_rows.children = _dd_rows.children + (
        widgets.HBox([widgets.Label("Period:", layout=_label_layout), start_dp,
                      widgets.Label("to"), end_dp]),
    )
    btn = widgets.Button(
        description="Confirm Selection", button_style="success", icon="check",
    )
    map_output = widgets.Output(layout=widgets.Layout(width="700px", height="420px"))
    confirm_output = widgets.Output()
    plot_output = widgets.Output()

    def _render_figure(output_widget, fig):
        """Render a Plotly figure (or its ready-made HTML) for environments without Plotly MIME support."""
        html = fig if isinstance(fig, str) else fig.to_html(full_html=False, include_plotlyjs="cdn")
        output_widget.outputs = ({
            "output_type": "display_data",
            "data": {"text/html": html},
            "metadata": {},
        },)

    def draw_map():
        instr = instrument_dd.value
        var = variable_dd.value
        station = station_dd.value or None
        located = {n: c for n, c in _get_stations(instr, var).items()
                   if c[0] is not None}

        others = [n for n in located if n != station]
        selected = [station] if station and station in located else []

        def label(n):
            lat, lon, cc = located[n]
            return f"{n} ({cc})" if cc else n

        def _trace(names, name, **kwargs):
            """Build one Scattergeo trace for a set of facility names."""
            return go.Scattergeo(
                lon=[located[n][1] for n in names], lat=[located[n][0] for n in names],
                text=[label(n) for n in names], hoverinfo="text", name=name, **kwargs,
            )

        fig = go.Figure()
        if others:
            fig.add_trace(_trace(
                others, "Available facilities", mode="markers",
                marker=dict(size=8, color="#3B4872", line=dict(width=1, color="white")),
            ))
        if selected:
            fig.add_trace(_trace(
                selected, "Selected", mode="markers+text", textposition="top center",
                marker=dict(size=15, color="crimson", line=dict(width=1.5, color="white")),
            ))
        title = f"{len(located)} facilities"
        fig.update_geos(
            resolution=50, showcountries=True,
            countrycolor="#888", showland=True, landcolor="#f2efe9",
            showocean=True, oceancolor="#dbe9f2", showlakes=False,
            lataxis=dict(range=[-70, 85]),
            lonaxis=dict(range=[-180, 180]),
        )
        fig.update_layout(
            width=700, height=420, autosize=False, margin=dict(l=0, r=0, t=40, b=0),
            showlegend=False, title=dict(text=title, font=dict(size=13), x=0.5, xanchor="center"),
            uirevision="facility-map",  # keeps the user's pan/zoom across redraws instead of resetting each time
        )
        _render_figure(map_output, fig)

    def on_instrument_change(change):
        if not _is_value_change(change):
            return
        new_instr = change["new"]
        new_vars = list(_TARGETS[new_instr].keys())
        new_stations = [""] + _stations_with_coords(new_instr, new_vars[0])
        variable_dd.unobserve(on_variable_change, names="value")
        station_dd.unobserve(on_station_change, names="value")
        try:
            variable_dd.options = new_vars
            variable_dd.value = new_vars[0]
            station_dd.options = new_stations
            station_dd.value = ""
        finally:
            variable_dd.observe(on_variable_change, names="value")
            station_dd.observe(on_station_change, names="value")
        _update_model_dd(None)
        draw_map()

    def on_variable_change(change):
        if not _is_value_change(change):
            return
        if not change["new"]:
            return
        new_stations = [""] + _stations_with_coords(instrument_dd.value, change["new"])
        station_dd.unobserve(on_station_change, names="value")
        try:
            station_dd.options = new_stations
            station_dd.value = ""
        finally:
            station_dd.observe(on_station_change, names="value")
        _update_model_dd(None)
        draw_map()

    def _update_model_dd(station):
        """Fetch models for the current selection and update the model dropdown."""
        if not station:
            model_dd.options = ["— select a facility first —"]
            model_dd.value = "— select a facility first —"
            model_dd.disabled = True
            return
        models = _fetch_models(instrument_dd.value, variable_dd.value, station)
        model_dd.unobserve(on_model_change, names="value")
        try:
            # All options must be (label, value) tuples, else ipywidgets treats every option as an opaque value.
            model_dd.options = [("", "")] + models
            # Open on the first model, so that Confirm Selection works straight away as the
            # notebook promises; an empty value made it ask for a model first.
            model_dd.value = models[0][1] if models else ""
        finally:
            model_dd.observe(on_model_change, names="value")
        model_dd.disabled = len(models) == 0

    def on_station_change(change):
        if not _is_value_change(change):
            return
        _update_model_dd(change["new"] or None)
        draw_map()

    def on_model_change(change):
        if not _is_value_change(change):
            return

    def on_confirm_clicked(_):
        def _write(text, stream="stdout", reset=False):
            """Append (or reset) a stream message on confirm_output."""
            entry = ({"output_type": "stream", "name": stream, "text": text},)
            confirm_output.outputs = entry if reset else confirm_output.outputs + entry

        # A new selection replaces the old figure even when it fails: a plot left over from the
        # previous selection under a new error message reads as the answer to the new one.
        plot_output.outputs = ()
        station = station_dd.value or None
        model = model_dd.value if model_dd.value and "select a facility" not in model_dd.value else None
        if not station or not model:
            missing = []
            if not station:
                missing.append("Facility")
            if not model:
                missing.append("Model")
            _write(f"Please select: {', '.join(missing)}\n", "stderr", reset=True)
            return
        if start_dp.value and end_dp.value and start_dp.value > end_dp.value:
            _write("The period ends before it starts: check the two dates.\n", "stderr", reset=True)
            return
        _widget_state["instrument"] = instrument_dd.value
        _widget_state["variable"] = variable_dd.value
        _widget_state["station"] = station
        _widget_state["model"] = model
        _widget_state["start"] = start_dp.value.isoformat() if start_dp.value else None
        _widget_state["end"] = end_dp.value.isoformat() if end_dp.value else None
        period = (f"{_widget_state['start'] or 'start of record'} to "
                  f"{_widget_state['end'] or 'end of record'}")
        _write(
            f"Confirmed:\n"
            f"  Instrument : {_widget_state['instrument']}\n"
            f"  Variable   : {_widget_state['variable']}\n"
            f"  Station    : {_widget_state['station']}\n"
            f"  Model      : {_widget_state['model']}\n"
            f"  Period     : {period}\n",
            reset=True,
        )

        # The button itself is the loading indicator: it updates in the browser immediately, even though this callback then blocks on network I/O.
        btn.disabled = True
        btn.description = "Loading..."
        btn.icon = "hourglass-half"
        try:
            dois = find_datasets()
            if not dois:
                _write(f"No dataset of this model covers {period}. Try a different period or "
                       f"model: the model list shows the years each one measured.\n", "stderr")
                return
            _write(f"Found {len(dois)} dataset(s) covering the period:\n"
                   + "\n".join(f"  {doi}" for doi in dois) + "\n")
            # Always through the loader, also for a single dataset: plotting a DOI directly
            # draws its whole record, whatever the period.
            datasets = load_and_concat_datasets(model, _widget_state["variable"], show_progress=False)
            if datasets is None:
                _write("Datasets could not be loaded/concatenated.\n", "stderr")
                return
            start, end = _period(_widget_state)
            if "time" in datasets.dims:
                covered = (str(datasets["time"].values[0])[:10], str(datasets["time"].values[-1])[:10])
                datasets = datasets.sel(time=slice(start, end))
                if datasets.sizes["time"] == 0:
                    _write(f"The datasets cover {covered[0]} to {covered[1]} but hold no "
                           f"measurement in {period}.\n", "stderr")
                    return
            fig, note = _figure_within_limits(datasets)
            if note:
                _write(note + "\n")

            # Atomic assignment avoids display() duplication; doing it before the finally keeps "Loading..." visible through figure delivery.
            _render_figure(plot_output, fig)
        except Exception as exc:
            _write(f"Error: {exc}\n", "stderr")
        finally:
            btn.disabled = False
            btn.description = "Confirm Selection"
            btn.icon = "check"

    display(_dd_rows, map_output, btn, confirm_output, plot_output)
    draw_map()

    instrument_dd.observe(on_instrument_change, names="value")
    variable_dd.observe(on_variable_change, names="value")
    station_dd.observe(on_station_change, names="value")
    model_dd.observe(on_model_change, names="value")
    btn.on_click(on_confirm_clicked)

    # The default facility is set while the dropdown is being built, before the observers above
    # exist, so no change event is fired and the model list would stay on "select a facility
    # first". Run the handler once by hand to complete the cascade.
    if station_dd.value:
        on_station_change({"type": "change", "name": "value",
                           "old": "", "new": station_dd.value})

    return instrument_dd, variable_dd, station_dd, model_dd, btn


def find_datasets(state=None):
    """Return sorted DOI URLs matching the confirmed widget selection (or the given state override)."""
    return sorted(
        pid for document in _search_docs(state)
        if (pid := (document.get("identification") or {}).get("identifier", {}).get("pid"))
    )


def _search_docs(state=None):
    """Return raw API docs matching the confirmed selection, with model filtering."""
    s = state if state is not None else _widget_state
    instrument = s.get("instrument")
    variable = s.get("variable")
    station = s.get("station")
    model = s.get("model")
    start, end = _period(s)

    if not all([instrument, variable, station]):
        raise ValueError("instrument, variable and station must be set. Call confirm first.")

    return [
        document for document in search(PROD_API, _dataset_query(instrument, variable, station))
        if _matches_model(document, model) and _overlaps(document, start, end)
    ]


def find_and_choose_datasets(state=None):
    """Search for matching datasets and present a dropdown to pick one, storing the choice in _widget_state['doi']."""
    docs = _search_docs(state)

    if not docs:
        print("No datasets found for the current selection.")
        return None

    # Build dropdown options: label → doi
    def _fmt_date(iso):
        return iso[:10] if iso else "?"

    options = []
    for doc in docs:
        pid = (doc.get("identification") or {}).get("identifier", {}).get("pid", "")
        te = doc.get("temporal_extent") or {}
        start = _fmt_date(te.get("time_period_begin"))
        end = _fmt_date(te.get("time_period_end"))
        short = pid.split("/")[-1]
        label = f"{short}  ({start} – {end})"
        options.append((label, pid))

    _dd_layout = widgets.Layout(width="520px")
    _dd_style = {"description_width": "100px"}

    doi_dd = widgets.Dropdown(
        options=[(lbl, pid) for lbl, pid in options],
        description="Dataset:",
        layout=_dd_layout,
        style=_dd_style,
    )

    confirm_output = widgets.Output()
    btn = widgets.Button(
        description="Select Dataset",
        button_style="success",
        icon="check",
        layout=widgets.Layout(width="180px"),
    )

    def on_confirm(_):
        _widget_state["doi"] = doi_dd.value
        confirm_output.outputs = ({
            "output_type": "stream",
            "name": "stdout",
            "text": f"Selected: {doi_dd.value}\n",
        },)

    btn.on_click(on_confirm)
    display(doi_dd, btn, confirm_output)

    print(f"Found {len(docs)} dataset(s). Pick one and click 'Select Dataset'.")
    return doi_dd


_AUXILIARY = ("v_pressure", "v_temperature", "v_relative_humidity", "v_pH", "v_conductivity")


def _clean_dataset(ds):
    """Drop QC metadata, statistics, and auxiliary variables, keeping only main v_ measurement variables and their needed coords/dims."""
    ds = ds.copy()
    
    # Step 1 & 2: Identify main v_ variables (without statistics suffixes)
    statistics_suffixes = ('_std', '_stddev', '_unc', '_uncertainty', 
                          '_min', '_max', '_median', '_det_lim', '_precision',
                          '_range', '_ExpUnc2s')
    # Percentile/precision variables (e.g. _perc8413, _prec1587) get their own
    # duplicate d_D_dN dimension per file; excluding them avoids fake dimension
    # mismatches across datasets when concatenating.
    statistics_number_suffix_re = re.compile(r"_(perc|prec)\d+$")
    
    vars_to_keep = [
        name for name in ds.data_vars
        if name.startswith("v_") and name not in _AUXILIARY
        and not name.endswith(statistics_suffixes)
        and not statistics_number_suffix_re.search(name)
    ]
    
    if not vars_to_keep:
        print("Warning: No main v_ variables found after filtering.")
        return ds
    
    # Step 4: Identify all dimensions and coordinates needed by the main variables
    needed_coords = {"time"} | {
        dim for name in vars_to_keep for dim in ds[name].dims if dim in ds.coords
    }
    ds_clean = ds[vars_to_keep]
    ds_clean = ds_clean.drop_vars(set(ds_clean.coords) - needed_coords, errors="ignore")
    return _rename_duplicate_dims(ds_clean)


_DUPLICATE_DIM_RE = re.compile(r"^(d_[A-Za-z_]+?)_d\d+$")


def _rename_duplicate_dims(ds):
    """Rename OPeNDAP-disambiguated dims like d_D_d0 back to d_D so identical dims across datasets can concatenate."""
    rename_map = {}
    for dim in ds.dims:
        match = _DUPLICATE_DIM_RE.match(dim)
        if match and match.group(1) not in ds.dims:
            rename_map[dim] = match.group(1)
    return ds.rename(rename_map) if rename_map else ds


def _doi_to_opendap_url(doi: str) -> str:
    """Convert a DOI identifier like '8HWP-FRNN' to the THREDDS OPeNDAP URL."""
    if doi.startswith("https://"):
        doi = doi.split("/")[-1]
    
    s = doi.replace("-", "")
    return f"https://thredds.nilu.no/thredds/dodsC/ebas_doi/{s[0:2]}/{s[2:4]}/{s[4:6]}/{doi}.nc"


def _normalize_variable_names(ds):
    """Rename variables like v_foo_amean/v_foo_mean to a common v_foo base name."""
    rename_map = {}
    
    # Suffixes that should be stripped (in order of preference)
    normalize_suffixes = ('_amean', '_mean', '_std', '_stddev', '_uncertainty')
    
    for var_name in ds.data_vars:
        # Skip coordinates and dimensions
        if var_name in ds.coords or var_name in ds.dims:
            continue
        
        # Check if this variable has a normalizable suffix
        base_name = var_name
        for suffix in normalize_suffixes:
            if var_name.endswith(suffix):
                base_name = var_name[:-len(suffix)]
                break
        
        # Only rename if the base name differs and won't collide with an existing variable
        if base_name != var_name and base_name not in ds.data_vars:
            rename_map[var_name] = base_name
    
    if rename_map:
        ds = ds.rename(rename_map)
    
    return ds


def _diagnose_concatenation_issues(datasets_list):
    """Print diagnostics on concatenated datasets: duplicate times, time gaps, and suspicious (all-zero/all-NaN) data."""
    if not datasets_list:
        return
    
    print("\n Concatenation Diagnostics:")
    
    # Check time ranges for each dataset
    for i, ds in enumerate(datasets_list):
        if 'time' in ds.coords:
            t_min = ds['time'].values[0]
            t_max = ds['time'].values[-1]
            n_times = len(ds['time'])
            print(f"  Dataset {i+1}: {n_times} time steps, {t_min} to {t_max}")
            
            # Check for duplicate times
            unique_times = len(np.unique(ds['time'].values))
            if unique_times < n_times:
                print(f"    ⚠ WARNING: {n_times - unique_times} duplicate time values detected!")
            
            # Check for suspicious data patterns in v_ variables
            for var in ds.data_vars:
                if var.startswith('v_'):
                    data = ds[var].values
                    if not np.issubdtype(data.dtype, np.number):
                        continue

                    valid_data = data[~np.isnan(data)]
                    if valid_data.size == 0:
                        print(f"    ⚠ {var}: all values are NaN")
                        continue

                    n_zero = np.count_nonzero(valid_data == 0)
                    pct_zero = (n_zero / valid_data.size) * 100
                    if pct_zero > 30:
                        print(f"    ⚠ {var}: {pct_zero:.1f}% zero values")


def _regrid_particle_size_dim(datasets):
    """Interpolate all datasets' d_D bins onto one shared log-spaced grid, since differing instrument bin grids would otherwise fragment on concat."""
    grids = [ds["d_D"].values for ds in datasets if "d_D" in ds.dims]
    if len(grids) < 2 or all(np.array_equal(grids[0], g) for g in grids[1:]):
        return datasets  # nothing to regrid, or already on a shared grid

    n_bins = max(len(g) for g in grids)
    d_min = min(g.min() for g in grids)
    d_max = max(g.max() for g in grids)
    canonical_d_d = np.logspace(np.log10(d_min), np.log10(d_max), n_bins)
    print(f"  Regridding d_D onto a shared {n_bins}-bin log grid "
          f"({d_min:.3g}-{d_max:.3g} nm) -- instrument bin grids differed.")

    def _regrid(ds):
        if "d_D" not in ds.dims:
            return ds
        ds = ds.assign_coords(d_D=("d_D", np.log10(ds["d_D"].values)))
        ds = ds.interp(d_D=np.log10(canonical_d_d), method="linear")
        return ds.assign_coords(d_D=("d_D", canonical_d_d))

    return [_regrid(ds) for ds in datasets]


def load_and_concat_datasets(selected_model, selected_attr=None, state=None, diagnose=False, show_progress=True):
    """Find, load, clean, and concatenate all datasets for a model/variable, returning one xr.Dataset or None."""
    global _widget_state
    
    s = state if state is not None else _widget_state
    
    # Verify required state is set
    if not all([s.get("instrument"), s.get("variable"), s.get("station")]):
        raise ValueError("instrument, variable and station must be set in widget state. Call create_search_widgets and confirm first.")
    
    # Set the model in state
    s["model"] = selected_model

    # Find all DOIs matching the model
    dois = find_datasets(state=s)
    
    if not dois:
        print("No datasets found for the current selection.")
        return None
    
    # Load each dataset
    def _load_one(doi):
        try:
            url = _doi_to_opendap_url(doi)
            ds = xr.open_dataset(url)
            # Step 1: Clean dataset (remove flags, QC metadata, statistics)
            ds = _clean_dataset(ds)
            # Step 2: Normalize variable names
            ds = _normalize_variable_names(ds)
            return ('ok', ds, doi)
        except Exception as e:
            return ('error', str(e), doi)
    
    loaded_datasets = []
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_load_one, doi): doi for doi in dois}
        completed = as_completed(futures)
        # tqdm.notebook's own display() escapes the surrounding Output widget and can't be cleared on repeated calls, so skip it when embedded in a widget callback.
        if show_progress:
            completed = tqdm(completed, total=len(futures), desc="Loading datasets")
        for future in completed:
            result = future.result()
            if result[0] == 'error':
                print(f"  Error loading {result[2]}: {result[1]}")
            else:
                _, ds, doi = result
                loaded_datasets.append(ds)
    
    if not loaded_datasets:
        print("No datasets could be loaded successfully.")
        return None
    
    if diagnose:
        _diagnose_concatenation_issues(loaded_datasets)
    
    # Concatenate along time dimension
    try:
        loaded_datasets = _regrid_particle_size_dim(loaded_datasets)

        # join='outer' aligns dims like d_D by real coordinate value, growing to the union and NaN-filling missing bins instead of requiring identical sizes.
        concatenated = xr.concat(loaded_datasets, dim='time', join='outer')
        
        # Sort by time to remove discontinuities/artifacts from concatenation
        if 'time' in concatenated.coords:
            concatenated = concatenated.sortby('time')

            # Drop exact-duplicate timestamps (e.g. overlapping source files), which otherwise leave a zero median time step and break gap-fill calculations.
            n_before = concatenated.sizes['time']
            _, unique_idx = np.unique(concatenated['time'].values, return_index=True)
            if len(unique_idx) < n_before:
                concatenated = concatenated.isel(time=np.sort(unique_idx))
                print(f"⚠ WARNING: Dropped {n_before - len(unique_idx)} duplicate timestamp(s) from overlapping datasets.")

            time_start = concatenated['time'].values[0]
            time_end = concatenated['time'].values[-1]
            print(f"Loaded {len(loaded_datasets)} dataset(s): {time_start} to {time_end} ({concatenated.sizes['time']} time steps)")
            
            # Check if time is now properly sorted
            time_diffs = np.diff(concatenated['time'].values.astype('datetime64[ns]').astype(np.int64))
            if not np.all(time_diffs >= 0):
                n_unsorted = np.sum(time_diffs < 0)
                print(f"⚠ WARNING: Found {n_unsorted} time steps out of order!")
        else:
            print(f"Loaded {len(loaded_datasets)} dataset(s). Result shape: {concatenated.sizes}")
        
        return concatenated
    except Exception as e:
        print(f"Error concatenating datasets: {e}")
        return None
