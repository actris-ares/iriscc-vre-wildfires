# IRISCC VRE — Wildfires in Europe

Jupyter notebooks for following a wildfire episode across three European research
infrastructures — **ACTRIS**, **IAGOS** and **ICOS** — together with **FLEXPART** model output.
The worked example is the 2025 Canadian fire season and the transport of its smoke plume to
Europe.

The notebooks were built for Demonstrator 6 of the Horizon Europe project
[IRISCC](https://www.iriscc.eu/), coordinated by ACTRIS, and run on the D4Science virtual
research environment *Wildfires in Europe*. They are written for students and researchers who
are not Python specialists: every choice — station, period, product — is made from menus, and
no code needs to be edited.

- Demonstrator 6 and its other wildfire services:
  [tools.actris-ares.eu/wildfire](https://tools.actris-ares.eu/wildfire/)
- The *Wildfires in Europe* VRE on D4Science:
  [iriscc.d4science.org/group/wildfires_in-europe](https://iriscc.d4science.org/group/wildfires_in-europe)

Maintained by the ACTRIS Data Centre unit ARES (Aerosol REmote Sensing), CNR.

## What is inside

| Notebook | What it does | Developed by |
|---|---|---|
| `IRISCC_home.ipynb` | Landing page: describes every tool, opens it, and copies the VRE into the user's home on D4Science | CNR |
| `actris/ACTRIS_explore_national_facilities.ipynb` | ACTRIS national facilities on a map and in a table | NILU, CNR |
| `actris/aerosol_remotesensing/ACTRIS_aerosol_remote_sensing.ipynb` | Aerosol lidar profiles from the ACTRIS ARES data centre, three aerosol typing algorithms (HIRAC, MAC, HETEAC-Flex) side by side, and nearby in-situ data (*beta*) | CNR |
| `actris/aerosol_remotesensing/ACTRIS_aerosol_typing.ipynb` | Aerosol typing only, without the optical profiles | CNR |
| `actris/aerosol_in_situ/in_situ_L2/ACTRIS_aerosol_insitu_L2.ipynb` | Quality-controlled (Level 2) in-situ aerosol data | NILU |
| `actris/aerosol_in_situ/in_situ_NRT/ACTRIS_aerosol_insitu_NRT.ipynb` | Near real time in-situ aerosol data | NILU |
| `iagos/IAGOS_flight_search_download.ipynb` | Search and download of IAGOS commercial-aircraft flights | CNRS |
| `icos/ICOS_ex1a_atmo_data_L2.ipynb`, `icos/ICOS_ex1a_atmo_data_NRT.ipynb` | CO and CO₂ at ICOS atmospheric stations, Level 2 and near real time | Lund University |
| `icos/*_two_species.ipynb` | Experimental variants comparing two species | Lund University |
| `flexpart/FLEXPART_historical.ipynb` | Modelled black carbon over 2008–2025, with the wildfire contribution separated | NILU |

Every notebook comes with an example case known to work — Potenza on 5 June 2025, when the
smoke reached southern Italy, for the ACTRIS lidar — and falls back to it when nothing is
selected, saying so.

The in-situ section at the end of the remote sensing notebook is marked **beta**: it works on
the cases it was tried on, but it is still being tested and its interface may change.

## Running the notebooks

### On D4Science

The VRE is at
[iriscc.d4science.org/group/wildfires_in-europe](https://iriscc.d4science.org/group/wildfires_in-europe)
and asks for a D4Science sign-in. After signing in, request access to the VRE: a VRE manager
approves the request. You can also ask to be invited directly by writing to
[services@actris-ares.eu](mailto:services@actris-ares.eu).

Members open `IRISCC_home.ipynb` from the shared folder and
press the copy button: the notebooks need to write their downloads and exports, so they run
from a copy in the user's own home. `VRE_home_folder_instructions.md` explains this step by
step.

### Anywhere else

The notebooks need a recent Python 3 and JupyterLab. Each one installs the packages it is
missing when it starts, but they can also be installed in advance:

```sh
python -m pip install -r requirements.txt
jupyter lab
```

Then open the notebooks directly. The copy button of the landing page only works on
D4Science, where the shared folder exists; elsewhere it reports that the folder was not found.

Keep the folders together: the remote sensing notebook reuses the in-situ modules of its
sibling folders, and finds them by walking up the tree.

### Accounts

Most data are open. Two tools ask for an account, at run time and never stored in the files:

- **IAGOS** downloads go through the AERIS data centre: register at
  [iagos.aeris-data.fr](https://iagos.aeris-data.fr/registration/) and sign in from the notebook.
- **ICOS** downloads need an ICOS Carbon Portal account, created at
  [cpauth.icos-cp.eu](https://cpauth.icos-cp.eu/).

## Notes for contributors

Notebooks are committed without outputs. The repository is published from the development
repository of the ACTRIS ARES unit, so changes proposed here through issues or pull requests
are carried over there and come back with the next release.

Each notebook prints its name, version and date from its first cell: when reporting a problem,
please include that line.

## License

- Code and notebooks: [GNU GPL v3.0](LICENSE).
- Documentation and images: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The modules under `actris/aerosol_in_situ/` were written by NILU, and the AERIS client under
`iagos/src/` by CNRS; they are included, with their authors' consent, so that the notebooks
run as they were tested.

Data retrieved by the notebooks remain under the licenses of the infrastructures that
distribute them: cite the datasets as each notebook indicates.

## Citation

If you use these notebooks, please cite them as described in [`CITATION.cff`](CITATION.cff)
(GitHub shows it under *Cite this repository*).

## Acknowledgements

This work was carried out within IRISCC (Integrated Research Infrastructure Services for
Climate Change risks), funded by the European Union's Horizon Europe programme under grant
agreement No 101131261. The VRE is hosted by [D4Science](https://www.d4science.org/).
