"""Cartographie de la sévérité des incendies de Landiras 2022 par télédétection.

Méthode :
1. Sentinel-2 L2A (Microsoft Planetary Computer, STAC), même orbite relative avant/après feu
   pour limiter les effets de géométrie d'acquisition.
2. NBR = (B8A - B12) / (B8A + B12) sur les réflectances de surface à 20 m,
   masque nuages / ombres / eau à partir de la couche SCL.
3. dNBR = NBR_avant - NBR_après, classé selon Key & Benson (2006, FIREMON).
4. Périmètre brûlé : dNBR >= 0.27 hors cultures annuelles du RPG 2022 (les moissons et les
   pivots de maïs récoltés entre les deux dates imitent un brûlis), nettoyage morphologique ;
   seul le complexe principal
   (>= 2 000 ha) est retenu comme incendie. Les petites surfaces à dNBR élevé et aux contours
   géométriques sont des coupes rases ou des récoltes, fréquentes dans le massif landais :
   elles sont exportées à part (changements_non_feu.gpkg) pour contrôle visuel.

Sorties : data/raster/dnbr_landiras_2022.tif (L93, 20 m), data/raster/severite_classes.tif,
          data/traite/perimetre_brule_2022.gpkg, data/raster/s2_apres_fausses_couleurs.tif
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.features import geometry_mask, shapes
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject, transform_bounds
from scipy import ndimage
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a"
RESOLUTION = 20

# Classes de sévérité (dNBR, Key & Benson 2006)
CLASSES = [
    (1, "Non brûlé / repousse", -np.inf, 0.10),
    (2, "Sévérité faible", 0.10, 0.27),
    (3, "Sévérité modérée-faible", 0.27, 0.44),
    (4, "Sévérité modérée-forte", 0.44, 0.66),
    (5, "Sévérité forte", 0.66, np.inf),
]
SEUIL_BRULE = 0.27
SURFACE_MIN_HA = 50        # taille mini d'une tache de changement
SURFACE_FEU_HA = 2000      # taille mini du complexe retenu comme incendie
# groupes de cultures RPG récoltés en été : céréales, maïs, oléo-protéagineux, fibres, gel,
# riz, légumineuses, fourrages, cultures industrielles, légumes (landes et prairies conservées)
RPG_CULTURES_ANNUELLES = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "11", "14", "15", "16", "24", "25"}
# SCL : 3 ombre de nuage, 6 eau, 8/9 nuages, 10 cirrus, 11 neige
SCL_INVALIDES = [0, 1, 3, 6, 8, 9, 10, 11]


def scenes(date):
    """Scènes L2A du jour sur l'emprise, en gardant le retraitement le plus récent par tuile."""
    r = requests.post(STAC, json={
        "collections": ["sentinel-2-l2a"], "bbox": list(C.BBOX_WGS84),
        "datetime": f"{date}T00:00:00Z/{date}T23:59:59Z", "limit": 50,
    }, timeout=60)
    r.raise_for_status()
    par_tuile = {}
    for f in r.json()["features"]:
        if C.S2_ORBITE not in f["id"]:
            continue
        tuile = f["properties"]["s2:mgrs_tile"]
        if tuile not in par_tuile or f["id"] > par_tuile[tuile]["id"]:
            par_tuile[tuile] = f
    if not par_tuile:
        raise RuntimeError(f"Aucune scène Sentinel-2 le {date}")
    print(f"   {date} : {', '.join(f['id'] for f in par_tuile.values())}")
    return list(par_tuile.values())


def lire_bande(items, bande, jeton, bounds_utm):
    """Mosaïque d'une bande sur l'emprise (lecture fenêtrée des COG, sans télécharger les tuiles entières)."""
    sources = []
    for it in items:
        src = rasterio.open(f"{it['assets'][bande]['href']}?{jeton}")
        sources.append(src)
    arr, transform = merge(sources, bounds=bounds_utm, res=RESOLUTION, resampling=Resampling.bilinear
                           if bande != "SCL" else Resampling.nearest, nodata=0)
    crs = sources[0].crs
    for s in sources:
        s.close()
    return arr[0], transform, crs


def reflectance(dn, item):
    """Réflectance de surface ; les produits de ligne de base >= 04.00 portent un décalage de -1000."""
    decalage = -1000 if float(item["properties"].get("s2:processing_baseline", "0")) >= 4 else 0
    r = (dn.astype("float32") + decalage) / 10000
    r[dn == 0] = np.nan
    return r


def nbr_date(date, jeton):
    items = scenes(date)
    utm = rasterio.crs.CRS.from_string(items[0]["properties"]["proj:code"]
                                       if "proj:code" in items[0]["properties"]
                                       else f"EPSG:{items[0]['properties']['proj:epsg']}")
    bounds = transform_bounds("EPSG:4326", utm, *C.BBOX_WGS84)
    b8a, tr, crs = lire_bande(items, "B8A", jeton, bounds)
    b12, _, _ = lire_bande(items, "B12", jeton, bounds)
    scl, _, _ = lire_bande(items, "SCL", jeton, bounds)
    b4, _, _ = lire_bande(items, "B04", jeton, bounds)  # B04 natif 10 m, rééchantillonné à 20 m
    it = items[0]
    nir, swir, red = reflectance(b8a, it), reflectance(b12, it), reflectance(b4, it)
    with np.errstate(invalid="ignore", divide="ignore"):
        nbr = (nir - swir) / (nir + swir)
    nbr[np.isin(scl, SCL_INVALIDES)] = np.nan
    return nbr, (swir, nir, red), tr, crs


def vers_l93(arr, tr, crs, dst_shape, dst_tr, resampling=Resampling.bilinear):
    out = np.full(dst_shape, np.nan, dtype="float32")
    reproject(arr, out, src_transform=tr, src_crs=crs, dst_transform=dst_tr, dst_crs=C.CRS,
              src_nodata=np.nan, dst_nodata=np.nan, resampling=resampling)
    return out


def ecrire(chemin, arr, transform, dtype="float32", nodata=np.nan, count=1):
    with rasterio.open(chemin, "w", driver="GTiff", height=arr.shape[-2], width=arr.shape[-1],
                       count=count, dtype=dtype, crs=C.CRS, transform=transform, nodata=nodata,
                       compress="deflate", tiled=True) as dst:
        dst.write(arr if count > 1 else arr[np.newaxis, ...])


def main():
    jeton = requests.get(SAS, timeout=30).json()["token"]
    print("-- NBR avant feu")
    nbr_av, _, tr_av, crs = nbr_date(C.S2_AVANT, jeton)
    print("-- NBR après feu")
    nbr_ap, fc, tr_ap, _ = nbr_date(C.S2_APRES, jeton)

    # grille L93 commune à 20 m
    xmin, ymin, xmax, ymax = transform_bounds("EPSG:4326", C.CRS, *C.BBOX_WGS84)
    xmin, ymin = np.floor(xmin / RESOLUTION) * RESOLUTION, np.floor(ymin / RESOLUTION) * RESOLUTION
    w, h = int(np.ceil((xmax - xmin) / RESOLUTION)), int(np.ceil((ymax - ymin) / RESOLUTION))
    dst_tr = rasterio.transform.from_origin(xmin, ymin + h * RESOLUTION, RESOLUTION, RESOLUTION)

    av = vers_l93(nbr_av, tr_av, crs, (h, w), dst_tr)
    ap = vers_l93(nbr_ap, tr_ap, crs, (h, w), dst_tr)
    dnbr = av - ap
    ecrire(C.RASTER / "dnbr_landiras_2022.tif", dnbr, dst_tr)

    # composition fausses couleurs post-feu (SWIR2 / NIR / Rouge) : brûlis en brun-rouge
    rgb = np.stack([vers_l93(b, tr_ap, crs, (h, w), dst_tr) for b in fc])
    rgb8 = np.clip(rgb / 0.4 * 255, 1, 255)
    rgb8[np.isnan(rgb8)] = 0
    ecrire(C.RASTER / "s2_apres_fausses_couleurs.tif", rgb8.astype("uint8"), dst_tr, "uint8", 0, 3)

    # cultures annuelles déclarées au RPG : non évaluées (récolte entre les deux dates)
    rpg = gpd.read_file(C.BRUT / "rpg_2022.gpkg")
    cultures = rpg[rpg.code_group.astype(str).isin(RPG_CULTURES_ANNUELLES)]
    m_cult = (geometry_mask(cultures.geometry, (h, w), dst_tr, invert=True) if len(cultures)
              else np.zeros((h, w), dtype=bool))

    # classes de sévérité (0 = non évalué)
    classes = np.zeros((h, w), dtype="uint8")
    for code, _, bas, haut in CLASSES:
        classes[(dnbr >= bas) & (dnbr < haut)] = code
    classes[m_cult] = 0
    ecrire(C.RASTER / "severite_classes.tif", classes, dst_tr, "uint8", 0)

    # périmètre brûlé, cultures annuelles exclues
    brule = np.nan_to_num(dnbr) >= SEUIL_BRULE
    print(f"   cultures annuelles RPG exclues : {m_cult.sum() * RESOLUTION ** 2 / 10000:,.0f} ha "
          f"(dont {(m_cult & brule).sum() * RESOLUTION ** 2 / 10000:,.0f} ha à dNBR élevé)")
    brule &= ~m_cult
    brule = ndimage.binary_opening(brule, iterations=1)
    brule = ndimage.binary_closing(brule, iterations=3)
    brule = ndimage.binary_fill_holes(brule)
    etiq, n = ndimage.label(brule)
    tailles = ndimage.sum(brule, etiq, range(1, n + 1)) * RESOLUTION ** 2 / 10000
    garder = np.isin(etiq, np.where(tailles >= SURFACE_MIN_HA)[0] + 1)
    geoms = [shape(g) for g, v in shapes(garder.astype("uint8"), mask=garder, transform=dst_tr) if v == 1]
    per = gpd.GeoDataFrame(geometry=geoms, crs=C.CRS)
    per["geometry"] = per.buffer(20).buffer(-20).simplify(10)
    per = per.explode(index_parts=False).reset_index(drop=True)
    per["surface_ha"] = (per.area / 10000).round(1)
    per = per[per.surface_ha >= SURFACE_MIN_HA].sort_values("surface_ha", ascending=False).reset_index(drop=True)
    per["source"] = f"dNBR Sentinel-2 {C.S2_AVANT} / {C.S2_APRES}"
    autres = per[per.surface_ha < SURFACE_FEU_HA].copy()
    autres["nature_probable"] = "coupe rase / récolte (à vérifier)"
    autres.to_file(C.TRAITE / "changements_non_feu.gpkg", driver="GPKG")
    per = per[per.surface_ha >= SURFACE_FEU_HA].copy()
    per["id_feu"] = [f"LANDIRAS_2022_{i + 1}" for i in range(len(per))]
    per["nom"] = "Complexe de Landiras (départ du 12 juillet et reprise du 9 août 2022)"
    per.to_file(C.TRAITE / "perimetre_brule_2022.gpkg", driver="GPKG")

    # statistiques par classe dans le périmètre retenu
    dans = geometry_mask(per.geometry, (h, w), dst_tr, invert=True)
    print(f"\nIncendie retenu : {per.surface_ha.sum():,.0f} ha ; {len(autres)} autres changements écartés "
          f"({autres.surface_ha.sum():,.0f} ha)")
    lignes = []
    for code, lib, _, _ in CLASSES:
        ha = ((classes == code) & dans).sum() * RESOLUTION ** 2 / 10000
        lignes.append({"code": code, "classe": lib, "surface_ha": round(ha)})
        print(f"   {lib:28s} {ha:10,.0f} ha")
    ha = (m_cult & dans).sum() * RESOLUTION ** 2 / 10000
    lignes.append({"code": 0, "classe": "Cultures annuelles (non évaluées)", "surface_ha": round(ha)})
    print(f"   {'Cultures (non évaluées)':28s} {ha:10,.0f} ha")
    pd.DataFrame(lignes).to_csv(C.SORTIES / "severite_par_classe.csv", index=False, encoding="utf-8")


if __name__ == "__main__":
    main()
