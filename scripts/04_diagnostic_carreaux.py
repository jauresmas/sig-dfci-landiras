"""Diagnostic de défendabilité du massif par carreau DFCI (2 x 2 km).

Pour chaque carreau du carroyage DFCI national :
- surface de massif (zonage OLD : forêts et landes) ;
- linéaire de pistes DFCI et densité (m/ha de massif) ;
- points d'eau présents ;
- part du massif à plus de 500 m d'une voie carrossable (routes, pistes, chemins BD TOPO) ;
- part du massif à plus de 500 m d'une piste DFCI recensée ;
- part du massif à plus de 1 000 m d'un point d'eau recensé (ravitaillement des CCF) ;
- part de résineux (BD Forêt V2) ;
- surface brûlée en 2022 et sévérité moyenne (dNBR) ;
- obligations OLD et obligations prioritaires.

Un indice de vigilance (0-100) combine l'accessibilité, l'éloignement de l'eau, la sensibilité du
combustible et la pression bâtie, pour hiérarchiser les carreaux à équiper ou à contrôler.
Distances à vol d'oiseau, calculées par transformée de distance sur une grille de 20 m.

Attention : pistes DFCI (attribut BD TOPO) et points d'eau (OpenStreetMap) sont incomplets en
données ouvertes. Les indicateurs « recensés » mesurent donc aussi un manque de données, à combler
par l'intégration des bases des ASA DFCI et du SDIS.

Sortie : data/traite/diagnostic_carreaux.gpkg, sorties/diagnostic_carreaux.csv
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.mask import mask
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

RES = 20
SEUIL_PISTE = 500
SEUIL_EAU = 1000
# pondération de l'indice de vigilance
POIDS = {"loin_voie": 0.30, "loin_eau": 0.25, "resineux": 0.25, "densite_old": 0.20}


def main():
    base = C.GPKG_DFCI
    car = gpd.read_file(base, layer="carreau_dfci")
    massif = gpd.read_file(base, layer="zonage_old", where="type_zone = 'massif'")
    piste = gpd.read_file(base, layer="piste")
    eau = gpd.read_file(base, layer="point_eau")
    old = gpd.read_file(base, layer="old_obligation")
    feu = gpd.read_file(base, layer="incendie")
    foret = gpd.read_file(C.BRUT / "bdforet_v2.gpkg")

    xmin, ymin, xmax, ymax = car.total_bounds
    w, h = int(np.ceil((xmax - xmin) / RES)), int(np.ceil((ymax - ymin) / RES))
    tr = rasterio.transform.from_origin(xmin, ymax, RES, RES)

    def grille(geoms):
        return rasterize(((g, 1) for g in geoms), out_shape=(h, w), transform=tr, fill=0, dtype="uint8").astype(bool)

    voies = gpd.read_file(C.BRUT / "troncons_route.gpkg", columns=["nature", "etat_de_l_objet"])
    voies = voies[(voies.nature != "Sentier") & (voies.etat_de_l_objet == "En service")]

    m_massif = grille(massif.geometry)
    d_voie = ndimage.distance_transform_edt(~grille(voies.geometry)) * RES
    d_piste = ndimage.distance_transform_edt(~grille(piste.geometry)) * RES
    d_eau = ndimage.distance_transform_edt(~grille(eau.geometry.buffer(RES))) * RES
    resineux = foret[foret.tfv_g11.str.contains("conifères", na=False) | foret.essence.str.contains("Pin", na=False)]
    m_res = grille(resineux.geometry)

    lignes = []
    for code, geom in zip(car.code, car.geometry):
        m_car = grille([geom])
        mm = m_car & m_massif
        n = mm.sum()
        lignes.append({
            "code": code,
            "massif_ha": n * RES ** 2 / 10000,
            "part_loin_voie": (mm & (d_voie > SEUIL_PISTE)).sum() / n if n else np.nan,
            "part_loin_piste": (mm & (d_piste > SEUIL_PISTE)).sum() / n if n else np.nan,
            "part_loin_eau": (mm & (d_eau > SEUIL_EAU)).sum() / n if n else np.nan,
            "part_resineux": (mm & m_res).sum() / n if n else np.nan,
        })
    d = car.merge(pd.DataFrame(lignes), on="code")

    # pistes : longueur découpée par carreau
    inter = gpd.overlay(piste[["geometry"]], car[["code", "geometry"]], how="intersection", keep_geom_type=True)
    d["pistes_km"] = d.code.map(inter.assign(l=inter.length).groupby("code").l.sum() / 1000).fillna(0)
    d["densite_pistes_m_ha"] = (d.pistes_km * 1000 / d.massif_ha).where(d.massif_ha > 0)
    d["points_eau"] = d.code.map(gpd.sjoin(eau, car[["code", "geometry"]]).groupby("code").size()).fillna(0).astype(int)

    # incendie 2022
    brule = gpd.overlay(car[["code", "geometry"]], feu[["geometry"]], how="intersection")
    d["brule_2022_ha"] = d.code.map(brule.set_index("code").area / 10000).fillna(0)
    moyennes = {}
    with rasterio.open(C.RASTER / "dnbr_landiras_2022.tif") as src:
        for code, g in zip(brule.code, brule.geometry):
            arr, _ = mask(src, [g], crop=True, nodata=np.nan, filled=True)
            moyennes[code] = float(np.nanmean(arr)) if np.isfinite(arr).any() else np.nan
    d["dnbr_moyen"] = d.code.map(moyennes)

    # OLD
    centres = gpd.GeoDataFrame(old[["priorite"]], geometry=old.representative_point(), crs=C.CRS)
    j = gpd.sjoin(centres, car[["code", "geometry"]])
    d["obligations_old"] = d.code.map(j.groupby("code").size()).fillna(0).astype(int)
    d["obligations_prioritaires"] = d.code.map(
        j[j.priorite.isin(["forte", "très forte"])].groupby("code").size()).fillna(0).astype(int)

    # pression bâtie : constructions (>= 20 m²) du carreau, disponible sur toute l'emprise
    # (les obligations OLD ne sont calculées que sur les communes touchées en 2022)
    bati = gpd.read_file(C.BRUT / "batiments.gpkg", columns=["etat_de_l_objet"])
    bati = bati[(bati.etat_de_l_objet == "En service") & (bati.area >= 20)]
    pts = gpd.GeoDataFrame(geometry=bati.representative_point(), crs=C.CRS)
    d["constructions"] = d.code.map(gpd.sjoin(pts, car[["code", "geometry"]]).groupby("code").size()) \
        .fillna(0).astype(int)

    # indice de vigilance (carreaux avec au moins 25 ha de massif)
    pression = (d.constructions / 300).clip(0, 1)  # 300 constructions par carreau = saturation
    d["indice_vigilance"] = (100 * (POIDS["loin_voie"] * d.part_loin_voie
                                    + POIDS["loin_eau"] * d.part_loin_eau
                                    + POIDS["resineux"] * d.part_resineux
                                    + POIDS["densite_old"] * pression)).round(0)
    d.loc[d.massif_ha < 25, "indice_vigilance"] = np.nan

    for c in ["massif_ha", "pistes_km", "densite_pistes_m_ha", "brule_2022_ha"]:
        d[c] = d[c].round(1)
    for c in ["part_loin_voie", "part_loin_piste", "part_loin_eau", "part_resineux", "dnbr_moyen"]:
        d[c] = d[c].round(3)
    d.to_file(C.TRAITE / "diagnostic_carreaux.gpkg", driver="GPKG", mode="w")
    d.drop(columns="geometry").to_csv(C.SORTIES / "diagnostic_carreaux.csv", index=False, encoding="utf-8")

    v = d.dropna(subset=["indice_vigilance"])
    pond = lambda col: (v[col] * v.massif_ha).sum() / v.massif_ha.sum()  # noqa: E731
    print(f"{len(v)} carreaux avec massif ; {v.massif_ha.sum():,.0f} ha de massif ; "
          f"{v.pistes_km.sum():,.0f} km de pistes DFCI recensées ; {v.points_eau.sum()} points d'eau recensés")
    print(f"Massif à > {SEUIL_PISTE} m d'une voie carrossable : {pond('part_loin_voie'):.1%}")
    print(f"Massif à > {SEUIL_PISTE} m d'une piste DFCI recensée : {pond('part_loin_piste'):.0%}")
    print(f"Massif à > {SEUIL_EAU} m d'un point d'eau recensé : {pond('part_loin_eau'):.0%}")
    print("\nCarreaux les plus vigilants :")
    print(v.nlargest(10, "indice_vigilance")[["code", "massif_ha", "part_loin_voie", "points_eau",
                                               "part_loin_eau", "part_resineux", "constructions",
                                               "indice_vigilance"]].to_string(index=False))


if __name__ == "__main__":
    main()
