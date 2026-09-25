"""Lance toute la chaîne de traitement, dans l'ordre.

Usage (environnement Python de QGIS, nécessaire aux étapes 05 et 06) :
    "C:\\Program Files\\QGIS 3.34.12\\bin\\python-qgis-ltr.bat" lancer_chaine.py
    python-qgis-ltr.bat lancer_chaine.py 02 04     # uniquement certaines étapes
"""
import subprocess
import sys
import time
from pathlib import Path

ETAPES = sorted((Path(__file__).parent / "scripts").glob("[0-9][0-9]_*.py"))

choix = sys.argv[1:]
for script in ETAPES:
    if choix and script.name[:2] not in choix:
        continue
    print(f"\n=== {script.stem}")
    debut = time.time()
    subprocess.run([sys.executable, "-W", "ignore", str(script)], check=True)
    print(f"    ({time.time() - debut:.0f} s)")
