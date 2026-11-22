"""Configuration/mapping rules for ACTRIS NRT filename-driven search.

"""

TARGET_INSTRUMENTS = [
    "filter absorption photometer",
    "mobility particle size spectrometer",
    "integrating nephelometer",
    "particle mass spectrometer",
    "condensation particle counter",
]

# Canonical ACTRIS-style instrument names -> NRT filename instrument tokens.
NRT_INSTRUMENT_TOKENS = {
    "filter absorption photometer": ["filter_absorption_photometer"],
    "mobility particle size spectrometer": ["dmps", "smps", "v-smps","t-smps"],
    "integrating nephelometer": ["nephelometer"],
    "particle mass spectrometer": ["PTR-MS"],
    "condensation particle counter": ["cpc"],
}

# User-facing variable choices -> filename filters and variable selectors.
# match_mode = "any" means any filename_required token is enough; "all" means all required tokens must occur.
NRT_VARIABLE_RULES = {
    "filter absorption photometer": [
        {
            "label": "aerosol_light_absorption_coefficient",
            "selected_attr": "aerosol particle light absorption coefficient",
            "filename_required": ["filter_absorption_photometer", "lev1.5"],
            "match_mode": "all",
            "target_vars": [ "aerosol_absorption_coefficient_amean"],
        },
        {
            "label": "equivalent_black_carbon",
            "selected_attr": "equivalent black carbon",
            "filename_required": ["filter_absorption_photometer", "lev3"],
            "match_mode": "all",
            "target_vars": ["equivalent_black_carbon_amean"],
        },
    ],
    "mobility particle size spectrometer": [
        {
            "label": "aerosol particle number size distribution",
            "selected_attr": "aerosol particle number size distribution",
            "filename_required": ["dmps", "smps", "v-smps", "t-smps"],
            "match_mode": "any",
            "target_vars": ["particle_number_size_distribution"],
        },
    ],
    "integrating nephelometer": [
        {
            "label": "aerosol_light_scattering_coefficient",
            "selected_attr": "aerosol particle light scattering coefficient",
            "filename_required": ["nephelometer"],
            "match_mode": "all",
            "target_vars": ["aerosol_light_scattering_coefficient_amean"],
        },
        {
            "label": "aerosol_light_backscattering_coefficient",
            "selected_attr": "aerosol particle light backscattering coefficient",
            "filename_required": ["nephelometer"],
            "match_mode": "all",
            "target_vars": ["aerosol_light_backscattering_coefficient_amean"],
        },
    ],
    "particle mass spectrometer": [
        {
            "label": "aerosol particle mass concentration",
            "selected_attr": "aerosol particle mass concentration",
            "filename_required": ["PTR-MS"],
            "match_mode": "all",
            "target_vars": [],  # variables are discovered after opening dataset
        },
    ],
    "condensation particle counter": [
            {
                "label": "particle_number_concentration",
                "selected_attr": "particle number concentration",
                "filename_required": ["cpc"],
                "match_mode": "all",
                "target_vars": ["particle_number_concentration_amean"],
            },
    ],
}


def get_variable_rule(instrument_type, variable_label):
    for rule in NRT_VARIABLE_RULES.get(instrument_type, []):
        if rule["label"] == variable_label:
            return rule
    return None
