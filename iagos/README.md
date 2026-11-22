# IRISCC — IAGOS CO fire contributions

## What is IAGOS?

[IAGOS](https://www.iagos.org) (In-service Aircraft for a Global Observing System) is a European
Research Infrastructure that equips commercial aircraft with scientific instruments to measure
atmospheric composition during regular passenger flights. Sensors on board continuously record
ozone (O₃), carbon monoxide (CO), nitrogen oxides (NOₓ), water vapour, and aerosols along
flight routes across the globe, building a long-term database of the upper troposphere and lower
stratosphere.

Data are collected in two modes:

- **Cruise data** — horizontal measurements at cruising altitude (~10–12 km).
- **Profile data** — vertical measurements during aircraft ascent (take-off) and descent
  (landing), capturing the full tropospheric column above major airports.

This notebook uses **profile data only**, which is the relevant mode for studying the vertical
distribution of CO from biomass burning.

---

Notebook for searching and downloading IAGOS flight data focused on **CO (carbon monoxide) from
fire sources**, using profile data — the vertical measurements made during ascent and descent.

## IRISCC parameters

Fixed for this project:

- `originCO = "fire"` (CO from biomass burning)
- `type = "profiles"` (vertical measurements during ascent and descent)

Chosen from the widgets in the notebook: `start`, `end`, `bbox`, `region`, and the data level.

## Data levels

IAGOS data are distributed at several processing levels. Three of them are used by this notebook:

| Level | Description | Access |
|-------|-------------|--------|
| **L1** | The Level 1 data are preliminary observational based on raw data that are calibrated and quality assured with automated or human quality control. It includes NRT data that is a special type of Level 1 data that are developed for fast distribution using automated or fast human quality control within a certain short time interval (typically 72 hours) of the measurement. | Available to authenticated user with additional rights. |
| **L2** | Validated and quality-controlled data. This is the standard level for most scientific analyses. | Available to any authenticated user. |
| **L4** | Model-enriched data. IAGOS observations are combined with ECMWF reanalysis fields and a CO source-attribution model, providing additional variables such as potential vorticity (PV), tropopause height, and CO contributions split by origin (fire, fossil fuels, biogenic, …). | Available to any authenticated user. |

## Notebook

| Notebook | Description |
|----------|-------------|
| `IAGOS_flight_search_download.ipynb` | Search, download L1/L2/L4, rank flights by CO, plot profiles and regional contributions |

It sits at the root of this folder, next to `src/`.

## Prerequisites

- An **AERIS account** ([register here](https://iagos.aeris-data.fr/registration/)). A shared
  account for the IRISCC school also exists — ask the IAGOS team.
- Nothing else: the notebook installs the Python packages it needs, and only those that are
  missing.

## Authentication

Everything happens **inside the notebook**: run the authentication cell, type your AERIS e-mail and
password, and press the button.

Credentials are never written into the source code of this package. By default they are kept in
memory for the session only. If you tick **Remember on this machine**, they are encrypted with a
master password of your choice into `~/.iagos/credentials.json` in your own home directory,
readable by you alone. From then on the cell asks only for the master password.

The `ENCRYPTED_USER_*` constants in `src/fr/aeris/iagos/auth/authentication.py` belong to an
earlier workflow and are no longer read. **Do not put credentials there**: in a shared environment
such as the IRISCC VRE that file is readable by every other user.

### Access to L1 data

L1 requires an additional role on the AERIS account. Without it the server answers:

```
403 - "Access to L1 data requires additional rights. You have insufficient permissions."
```

L2 and L4 are available to any authenticated user, and the CO ranking falls back from L1 to L2
automatically, so the notebook remains usable without the role.

Note that the *same request without a valid token* returns
`401 Authorization token is required for L1 data access`, which looks like a permissions problem
but is not: it means the sign-in cell has not been run, or was re-run and the session cleared.

## Where the data go

Downloads are written to a `data/` folder next to the notebook, as in the other tools of this VRE.
**It is emptied at the start of each run**, so that repeated searches do not fill up the home
directory: the L1/L2/L4 archives are tens of megabytes each. Set `CLEAN_DATA_FOLDER = False` in the
first cell to keep them.

The archives produced by the export cell go to `exports/`, which is emptied at each run as well.

The last cell of the notebook packs the figures, the ranked-flights table and, if asked, the
downloaded archives into a single zip with a download link. Use it before starting a new run on
anything you want to keep.

## Project structure

```
iagos/
├── IAGOS_flight_search_download.ipynb
├── data/                           # downloaded archives, emptied at each run
├── exports/                        # zips made by the export cell, emptied at each run
├── environment.yml
├── pyproject.toml
├── README.md
├── images/
│   └── GFED-regions.png
└── src/fr/aeris/iagos/
    ├── auth/                       # SSO authentication and encryption helpers
    └── iriscc.py                   # search, download and analysis functions
```

## API reference

**Production server**: `https://api.sedoo.fr/iagos-backend/v2.0/downloads`

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/search` | GET | Required | Search for flights (returns count and total size) |
| `/download-flights` | GET | Required | Download flight data as ZIP (max 1 GB) |

### Parameters (IRISCC)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| start | string | Yes | Start date (ISO 8601) |
| end | string | Yes | End date (ISO 8601) |
| bbox | string | Yes | Bounding box: minLon,minLat,maxLon,maxLat |
| level | string | Yes | `L1`, `L2`, or `L4` |
| region | list | No | GFED region codes (e.g., `["BONA", "EURO"]`) |
| l4Type | list | No | `ECMWF`, `CO_CONTRIBUTIONS`, `PV` (L4 only) |

**Fixed**: `type = "profiles"`, `originCO = "fire"`

### GFED regions

| Code | Region | Code | Region |
|------|--------|------|--------|
| AUST | Australia | MIDE | Middle East |
| BOAS | Boreal Asia | NHAF | N. Hemisphere Africa |
| BONA | Boreal North America | NHSA | N. Hemisphere South America |
| CEAM | Central America | SEAS | Southeast Asia |
| CEAS | Central Asia | SHAF | S. Hemisphere Africa |
| EQAS | Equatorial Asia | SHSA | S. Hemisphere South America |
| EURO | Europe | TENA | Temperate North America |

## Troubleshooting

**"No module named 'fr.aeris.iagos'"** — the notebook adds `src/` to `sys.path` itself, checking
the current folder first and then the parent, so it works whether it sits at the root of this
folder or in a subfolder. If the error persists, run `pip install -e .` from this folder.

**"403 — Access to L1 data requires IAGOS_DATA_PROVIDER role"** — see *Access to L1 data* above.

**"401 — Authorization token is required"** — the sign-in cell has not been run, or was re-run and
the session cleared. Run it and wait for the confirmation message.

**The sign-in is rejected although the credentials are right** — if you are unlocking stored
credentials, the master password may be wrong. A wrong master password does not fail outright: it
decrypts to rubbish, which AERIS then rejects. The cell checks for this and says so.

**A download returns data for the wrong query** — should no longer happen. A download is skipped
when a file of the same name already exists, and the name now includes the region as well as the
level and the dates.

**TripleDES deprecation warning** — expected, and harmless: it comes from the Jasypt-compatible
encryption used for the stored credentials.
