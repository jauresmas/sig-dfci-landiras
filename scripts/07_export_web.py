"""Export des résultats pour la carte web (docs/, publiée par GitHub Pages).

- GeoJSON en WGS84, géométries simplifiées et coordonnées arrondies (poids réduit) ;
- sévérité de l'incendie en PNG reprojeté en Web Mercator, avec ses coins pour MapLibre ;
- chiffres clés dans docs/data/synthese.json.
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
from shapely import set_precision
from shapely.geometry import MultiPolygon, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

W = C.WEB_DATA
BASE = C.GPKG_DFCI
COULEURS_SEVERITE = {1: (201, 211, 168, 150), 2: (254, 217, 118, 215), 3: (253, 141, 60, 225),
                     4: (227, 26, 28, 230), 5: (103, 0, 13, 235)}


def geojson(gdf, nom, simplifier=0, colonnes=None, precision=5):
    g = gdf.copy()
    if simplifier:
        g["geometry"] = g.simplify(simplifier, preserve_topology=True)
    g = g.to_crs(4326)
    g["geometry"] = set_precision(g.geometry.values, 10 ** -precision)
    g = g[~g.geometry.is_empty]
    if colonnes:
        g = g[colonnes + ["geometry"]]
    chemin = W / f"{nom}.geojson"
    chemin.write_text(g.to_json(drop_id=True, ensure_ascii=False), encoding="utf-8")
    print(f"   {nom}.geojson  {chemin.stat().st_size / 1e6:.2f} Mo  ({len(g):,} entités)")


def sans_trous(g):
    if g.geom_type == "Polygon":
        return Polygon(g.exterior)
    return MultiPolygon([Polygon(p.exterior) for p in g.geoms])


def image_severite():
    with rasterio.open(C.RASTER / "severite_incendie.tif") as src:
        dst_crs = "EPSG:3857"
        tr, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
        dst = np.zeros((h, w), dtype="uint8")
        reproject(src.read(1), dst, src_transform=src.transform, src_crs=src.crs, dst_transform=tr,
                  dst_crs=dst_crs, resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
    rgba = np.zeros((h, w, 4), dtype="uint8")
    for code, couleur in COULEURS_SEVERITE.items():
        rgba[dst == code] = couleur
    Image.fromarray(rgba, "RGBA").save(W / "severite.png", optimize=True)
    o, s, e, n = transform_bounds(dst_crs, "EPSG:4326", tr.c, tr.f + tr.e * h, tr.c + tr.a * w, tr.f)
    print(f"   severite.png  {(W / 'severite.png').stat().st_size / 1e6:.2f} Mo")
    return [[o, n], [e, n], [e, s], [o, s]]


def main():
    feu = gpd.read_file(BASE, layer="incendie")
    geojson(feu, "incendie", 15, ["id", "nom", "surface_ha", "date_debut"])

    diag = gpd.read_file(C.TRAITE / "diagnostic_carreaux.gpkg")
    geojson(diag, "carreaux", 0, ["code", "massif_ha", "pistes_km", "densite_pistes_m_ha", "points_eau",
                                  "part_loin_voie", "part_loin_piste", "part_loin_eau", "part_resineux",
                                  "brule_2022_ha", "dnbr_moyen", "constructions", "indice_vigilance"])

    old = gpd.read_file(BASE, layer="old_obligation")
    pts = old.copy()
    pts["geometry"] = old.representative_point()
    pts["en_lisiere"] = pts.en_lisiere.astype(bool)
    pts["surface_old_m2"] = pts.surface_old_m2.round().astype(int)
    geojson(pts, "old_points", 0, ["id_obligation", "commune", "nb_batiments", "surface_old_m2",
                                   "surface_tiers_non_batis_m2", "en_lisiere", "dist_massif_m",
                                   "part_resineux", "dist_point_eau_m", "score_priorite", "priorite"])

    # rayons OLD de 50 m, un fichier par commune, chargés par la carte seulement aux grands zooms
    (W / "old").mkdir(exist_ok=True)
    poly = old[["id_obligation", "code_insee", "priorite", "geometry"]].copy()
    # contour extérieur seul (les bâtiments sont dessinés par le Plan IGN) et simplification à 3 m,
    # invisibles à l'échelle d'un bourg mais qui divisent le poids des fichiers
    poly["geometry"] = poly.geometry.apply(sans_trous).simplify(3.0, preserve_topology=True)
    for insee, g in poly.groupby("code_insee"):
        g = g.to_crs(4326)
        g["geometry"] = set_precision(g.geometry.values, 1e-5)
        (W / "old" / f"{insee}.geojson").write_text(
            g.drop(columns="code_insee").to_json(drop_id=True, ensure_ascii=False), encoding="utf-8")
    taille = sum(f.stat().st_size for f in (W / "old").glob("*.geojson")) / 1e6
    print(f"   old/<commune>.geojson  {taille:.2f} Mo au total ({poly.code_insee.nunique()} communes)")

    piste = gpd.read_file(BASE, layer="piste")
    geojson(piste, "pistes", 4, ["gabarit", "revetement", "debroussaillee", "longueur_m"])
    eau = gpd.read_file(BASE, layer="point_eau")
    geojson(eau, "points_eau", 0, ["type", "carreau_dfci", "source"])

    communes = gpd.read_file(C.BRUT / "communes.gpkg")
    synth = pd.read_csv(C.SORTIES / "old_synthese_communes.csv", dtype={"code_insee": str})
    etud = communes.merge(synth, on="code_insee")
    geojson(etud, "communes", 20, ["code_insee", "commune", "obligations", "constructions", "surface_nette_ha",
                                   "dont_chez_voisins_non_batis_ha", "en_lisiere", "priorite_forte_ou_plus"])

    coins = image_severite()

    sev = pd.read_csv(C.SORTIES / "severite_par_classe.csv")
    v = diag.dropna(subset=["indice_vigilance"])
    pond = lambda col: float((v[col] * v.massif_ha).sum() / v.massif_ha.sum())  # noqa: E731
    synthese = {
        "images": {"avant": C.S2_AVANT, "apres": C.S2_APRES, "orbite": C.S2_ORBITE},
        "severite_coins": coins,
        "incendie": {"surface_ha": round(float(feu.area.sum() / 10000)),
                     "classes": sev.to_dict(orient="records")},
        "old": {
            "communes": int(len(synth)),
            "obligations": int(synth.obligations.sum()),
            "constructions": int(synth.constructions.sum()),
            "surface_nette_ha": round(float(synth.surface_nette_ha.sum())),
            "chez_voisins_non_batis_ha": round(float(synth.dont_chez_voisins_non_batis_ha.sum())),
            "en_lisiere": int(synth.en_lisiere.sum()),
            "priorites": old.priorite.value_counts().to_dict(),
            "par_commune": synth.drop(columns=["code_insee"]).to_dict(orient="records"),
        },
        "carreaux": {
            "nombre": int(len(v)), "massif_ha": round(float(v.massif_ha.sum())),
            "pistes_dfci_km": round(float(v.pistes_km.sum())), "points_eau": int(v.points_eau.sum()),
            "part_loin_voie": round(pond("part_loin_voie"), 3),
            "part_loin_piste": round(pond("part_loin_piste"), 3),
            "part_loin_eau": round(pond("part_loin_eau"), 3),
            "plus_vigilants": v.nlargest(8, "indice_vigilance")[["code", "indice_vigilance", "constructions",
                                                                 "part_resineux"]].to_dict(orient="records"),
        },
    }
    (W / "synthese.json").write_text(json.dumps(synthese, ensure_ascii=False, indent=1), encoding="utf-8")
    print("   synthese.json")


if __name__ == "__main__":
    main()
