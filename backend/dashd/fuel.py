"""Fuel flow from mass airflow."""
STOICH_AFR = 14.7
GASOLINE_G_PER_L = 745.0


def fuel_lph(maf_gps: float) -> float:
    """Fuel flow in L/h from mass airflow in g/s, assuming a stoichiometric mix."""
    return maf_gps / STOICH_AFR / GASOLINE_G_PER_L * 3600
