"""Téléchargement des données sources sur l'emprise d'étude.

Sources (toutes en données ouvertes) :
- Géoplateforme IGN (WFS) : BD TOPO, BD Forêt V2, zonage OLD, carroyage DFCI, RPG 2022
- OpenStreetMap (Overpass) : hydrants, citernes, réserves d'eau, tours de guet
- Cadastre Etalab : parcelles (téléchargées plus tard, commune par commune, par 02_analyse_old.py)

Usage : python scripts/00_telechargement.py [--force]
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from pyproj import Transformer
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

COUCHES_IGN = {
    "communes": "BDTOPO_V3:commune",
    "batiments": "BDTOPO_V3:batiment",
    "troncons_route": "BDTOPO_V3:troncon_de_route",
    "foret_publique": "BDTOPO_V3:foret_publique",
    "bdforet_v2": "LANDCOVER.FORESTINVENTORY.V2:formation_vegetale",
    "zonage_old": "DEBROUSSAILLEMENT:debroussaillement",
    "carroyage_dfci_2km": "GEOGRAPHICALGRIDSYSTEM.DFCI:carro_dfci_2x2_l93",
    "rpg_2022": "RPG.2022:parcelles_graphiques",
}

OVERPASS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

session = requests.Session()
session.headers["User-Agent"] = "sig-dfci-landiras (projet portfolio)"


def bbox_l93():
    t = Transformer.from_crs("EPSG:4326", C.CRS, always_xy=True)
    o, s, e, n = C.BBOX_WGS84
    xs, ys = zip(*(t.transform(x, y) for x, y in [(o, s), (o, n), (e, s), (e, n)]))
    return min(xs), min(ys), max(xs), max(ys)


def wfs(typename, bbox, page=5000):
    """Récupère toutes les entités d'une couche WFS sur la bbox, par pages."""
    frames, debut = [], 0
    while True:
        r = session.get(C.WFS_IGN, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": typename, "outputFormat": "application/json",
            "srsName": C.CRS, "bbox": ",".join(map(str, bbox)) + ",urn:ogc:def:crs:" + C.CRS.replace(":", "::"),
            "count": page, "startIndex": debut,
        }, timeout=300)
        r.raise_for_status()
        d = r.json()
        if not d["features"]:
            break
        frames.append(gpd.GeoDataFrame.from_features(d["features"], crs=C.CRS))
        print(f"   {typename} : {debut + len(d['features'])} entités")
        if len(d["features"]) < page:
            break
        debut += page
    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs=C.CRS)
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=C.CRS)
    # la pagination WFS sans tri peut renvoyer des doublons : on les retire sur l'identifiant
    cle = next((c for c in ("cleabs", "id", "nom") if c in gdf.columns), None)
    return gdf.drop_duplicates(cle) if cle else gdf


def overpass():
    o, s, e, n = C.BBOX_WGS84
    b = f"({s},{o},{n},{e})"
    q = f"""[out:json][timeout:120];
    (nwr["emergency"~"fire_hydrant|water_tank|fire_water_pond|suction_point"]{b};
     nwr["man_made"="tower"]["tower:type"~"observation|fire_lookout"]{b};);
    out center tags;"""
    for url in OVERPASS:
        try:
            r = session.post(url, data={"data": q}, timeout=180)
            r.raise_for_status()
            els = r.json()["elements"]
            break
        except Exception as exc:  # miroir suivant
            print(f"   Overpass {url} indisponible ({exc})")
    else:
        raise RuntimeError("Aucun serveur Overpass disponible")
    lignes = []
    for el in els:
        lon = el.get("lon", el.get("center", {}).get("lon"))
        lat = el.get("lat", el.get("center", {}).get("lat"))
        t = el.get("tags", {})
        lignes.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "emergency": t.get("emergency"),
            "man_made": t.get("man_made"),
            "tower_type": t.get("tower:type"),
            "name": t.get("name"),
            "capacity": t.get("water_tank:volume") or t.get("capacity"),
            "fire_hydrant_type": t.get("fire_hydrant:type"),
            "geometry": Point(lon, lat),
        })
    return gpd.GeoDataFrame(lignes, crs="EPSG:4326").to_crs(C.CRS)


def main():
    bbox = bbox_l93()
    print(f"Emprise Lambert-93 : {[round(v) for v in bbox]}")
    fichier_emprise = C.BRUT / "emprise.gpkg"
    forcer = "--force" in sys.argv
    if fichier_emprise.exists():
        ancienne = gpd.read_file(fichier_emprise).total_bounds
        if max(abs(a - b) for a, b in zip(ancienne, bbox)) > 1:
            print("L'emprise a changé depuis le dernier téléchargement : tout est retéléchargé.")
            forcer = True

    for nom, typename in COUCHES_IGN.items():
        sortie = C.BRUT / f"{nom}.gpkg"
        if sortie.exists() and not forcer:
            print(f"-- {nom} déjà présent")
            continue
        print(f"-- {nom} ({typename})")
        gdf = wfs(typename, bbox)
        gdf.to_file(sortie, driver="GPKG", mode="w")

    sortie = C.BRUT / "osm_equipements_dfci.gpkg"
    if forcer or not sortie.exists():
        print("-- équipements OSM (Overpass)")
        overpass().to_file(sortie, driver="GPKG", mode="w")

    emprise = gpd.GeoDataFrame({"nom": ["emprise_etude"]}, geometry=[Polygon.from_bounds(*bbox)], crs=C.CRS)
    emprise.to_file(fichier_emprise, driver="GPKG", mode="w")
    print("Terminé.")


if __name__ == "__main__":
    main()
