"""Paramètres communs à tous les scripts du projet SIG DFCI Landiras."""
from pathlib import Path

RACINE = Path(__file__).resolve().parent
DATA = RACINE / "data"
BRUT = DATA / "brut"
TRAITE = DATA / "traite"
RASTER = DATA / "raster"
SORTIES = RACINE / "sorties"
WEB_DATA = RACINE / "docs" / "data"

# Base DFCI (GeoPackage, modèle décrit dans sql/)
GPKG_DFCI = TRAITE / "dfci_landiras.gpkg"

# Projection de travail : Lambert-93
CRS = "EPSG:2154"

# Emprise d'étude : massif des Landes de Gascogne autour de Landiras (Gironde),
# englobant les deux feux de 2022 (juillet puis reprise d'août).
BBOX_WGS84 = (-0.87, 44.38, -0.36, 44.64)  # ouest, sud, est, nord (feu + 5 km)

# Sentinel-2 L2A (Planetary Computer), même orbite relative avant et après le feu.
# R094 couvre tout le complexe (l'orbite R051 coupe le lobe ouest d'août).
S2_AVANT = "2022-07-02"   # 10 jours avant le départ de feu du 12 juillet
S2_APRES = "2022-09-20"   # après extinction de la reprise d'août
S2_ORBITE = "R094"

# Obligations légales de débroussaillement (Code forestier L134-6, arrêté préfectoral Gironde)
OLD_RAYON_BATI = 50      # m autour des constructions situées à moins de 200 m des bois
OLD_BANDE_FORET = 200    # m autour des massifs soumis

WFS_IGN = "https://data.geopf.fr/wfs/ows"

for d in (BRUT, TRAITE, RASTER, SORTIES, WEB_DATA):
    d.mkdir(parents=True, exist_ok=True)
