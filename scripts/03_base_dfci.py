"""Construction et alimentation de la base DFCI (GeoPackage).

Le GeoPackage reprend le modèle PostGIS de sql/schema_dfci_postgis.sql (mêmes tables et
mêmes listes de valeurs). Les listes de valeurs sont écrites comme « domaines de champs »
GeoPackage : QGIS et QField les affichent automatiquement en listes déroulantes, ce qui
évite les saisies hors nomenclature sur le terrain.

Alimentation :
- carreau_dfci   <- carroyage DFCI 2 km (IGN)
- zonage_old     <- zonage OLD (IGN, arrêté préfectoral de Gironde)
- piste          <- tronçons BD TOPO portant l'attribut piste_dfci
- point_eau      <- OpenStreetMap (hydrants, citernes, réserves)
- point_interet  <- tours de guet (OSM), aires de retournement DFCI (BD TOPO)
- incendie       <- périmètre dNBR (01_severite_dnbr.py)
- old_obligation <- analyse OLD (02_analyse_old.py)
- controle_old, patrouille : tables vides, alimentées sur le terrain (QField)
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from osgeo import gdal, ogr, osr
from shapely.geometry import MultiLineString, MultiPolygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

gdal.UseExceptions()
ogr.UseExceptions()

# Listes de valeurs : identiques aux tables ref.* du modèle PostGIS
DOMAINES = {
    "type_point_eau": {"PI": "Poteau / bouche d'incendie (réseau)", "CIT": "Citerne / réserve artificielle",
                       "RN": "Réserve naturelle (mare, étang, lagune)", "FOR": "Forage / point d'aspiration",
                       "AUT": "Autre"},
    "etat_equipement": {"BON": "Bon état, opérationnel", "MOY": "Utilisable, entretien à prévoir",
                        "HS": "Hors service", "NC": "Non contrôlé"},
    "categorie_piste": {"1": "Catégorie 1", "2": "Catégorie 2", "3": "Catégorie 3", "NC": "Non classée"},
    "statut_old": {"CONF": "Conforme", "NCONF": "Non conforme", "PART": "Partiellement conforme",
                   "INACC": "Inaccessible / absent", "AFAIRE": "Contrôle à réaliser"},
    "suite_controle": {"AUC": "Aucune", "INFO": "Courrier d'information", "MED": "Mise en demeure (maire)",
                       "CV": "Contre-visite programmée", "PV": "Procès-verbal"},
    "type_poi": {"VIG": "Tour de guet / vigie", "RET": "Aire de retournement", "CRO": "Zone de croisement",
                 "BAR": "Barrière / accès fermé", "RDV": "Point de rendez-vous des secours", "DZ": "Hélisurface / DZ",
                 "SEN": "Site sensible (camping, ERP, industrie)"},
    "niveau_risque": {"faible": "Faible", "modéré": "Modéré", "sévère": "Sévère", "très sévère": "Très sévère",
                      "exceptionnel": "Exceptionnel"},
}

# champ -> domaine, par table
CHAMPS_DOMAINES = {
    "piste": {"categorie": "categorie_piste", "etat": "etat_equipement"},
    "point_eau": {"type": "type_point_eau", "etat": "etat_equipement"},
    "point_interet": {"type": "type_poi"},
    "old_obligation": {"statut": "statut_old"},
    "controle_old": {"statut": "statut_old", "suite": "suite_controle"},
    "patrouille": {"niveau_risque": "niveau_risque"},
}

OSM_VERS_TYPE = {"fire_hydrant": "PI", "water_tank": "CIT", "fire_water_pond": "RN", "suction_point": "FOR"}


def multi(g):
    if g.geom_type == "Polygon":
        return MultiPolygon([g])
    if g.geom_type == "LineString":
        return MultiLineString([g])
    return g


def ecrire(gdf, table):
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.apply(multi) if gdf.geom_type.isin(["Polygon", "LineString"]).any() else gdf.geometry
    gdf.to_file(C.GPKG_DFCI, layer=table, driver="GPKG", mode="w")
    print(f"   {table:16s} {len(gdf):>7,} entités")


def table_vide(ds, nom, geom_type, champs):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(2154)
    lyr = ds.CreateLayer(nom, srs, geom_type, options=["OVERWRITE=YES", "FID=fid", "GEOMETRY_NAME=geom"])
    for champ, t in champs:
        lyr.CreateField(ogr.FieldDefn(champ, t))
    print(f"   {nom:16s} (vide, saisie terrain)")


def appliquer_domaines(ds):
    for nom, valeurs in DOMAINES.items():
        dom = ogr.CreateCodedFieldDomain(nom, f"Liste de valeurs DFCI : {nom}", ogr.OFTString, ogr.OFSTNone, valeurs)
        ds.AddFieldDomain(dom)
    for table, champs in CHAMPS_DOMAINES.items():
        lyr = ds.GetLayerByName(table)
        defn = lyr.GetLayerDefn()
        for champ, dom in champs.items():
            idx = defn.GetFieldIndex(champ)
            fd = ogr.FieldDefn(champ, ogr.OFTString)
            fd.SetDomainName(dom)
            lyr.AlterFieldDefn(idx, fd, ogr.ALTER_DOMAIN_FLAG)


def main():
    if C.GPKG_DFCI.exists():
        C.GPKG_DFCI.unlink()
    print(f"Base : {C.GPKG_DFCI}")

    car = gpd.read_file(C.BRUT / "carroyage_dfci_2km.gpkg")[["nom", "geometry"]].rename(columns={"nom": "code"})
    car["code_20km"] = car.code.str[:4]
    ecrire(car, "carreau_dfci")

    z = gpd.read_file(C.BRUT / "zonage_old.gpkg")
    z = gpd.GeoDataFrame({
        "type_zone": z.zonage.map({1: "massif", 2: "zone_application"}),
        "nature": z.nature.where(z.zonage == 1),
        "arrete_date": pd.to_datetime(z.dat_ap_old, format="%d/%m/%y", errors="coerce").dt.date.astype(str),
        "source": z.source, "geometry": z.geometry}, crs=C.CRS)
    ecrire(z, "zonage_old")

    r = gpd.read_file(C.BRUT / "troncons_route.gpkg")
    r = r[r.piste_dfci.astype(str) == "True"]
    communes = gpd.read_file(C.BRUT / "communes.gpkg")[["code_insee", "geometry"]]
    piste = gpd.GeoDataFrame({
        "id_bdtopo": r.cleabs,
        "nom": r.cpx_toponyme_route_nommee,
        "categorie": "NC",
        "gabarit": r.gabarit_dfci,
        "revetement": r.nature_detaillee_dfci,
        "largeur_m": pd.to_numeric(r.largeur_de_chaussee, errors="coerce"),
        "impasse": r.impasse_dfci.astype(str) == "True",
        "debroussaillee": r.piste_dfci_debroussaillee.astype(str) == "True",
        "vitesse_kmh": r.vitesse_moyenne_dfci,
        "etat": "NC",
        "geometry": r.geometry.force_2d(),
    }, crs=C.CRS)
    piste["commune_insee"] = gpd.sjoin(gpd.GeoDataFrame(geometry=piste.geometry.interpolate(0.5, normalized=True),
                                                        crs=C.CRS), communes, predicate="within", how="left") \
        .groupby(level=0).code_insee.first()
    piste["longueur_m"] = piste.length.round(1)
    ecrire(piste, "piste")

    osm = gpd.read_file(C.BRUT / "osm_equipements_dfci.gpkg")
    e = osm[osm.emergency.isin(OSM_VERS_TYPE)]
    pe = gpd.GeoDataFrame({
        "id_source": e.osm_id, "type": e.emergency.map(OSM_VERS_TYPE),
        "capacite_m3": pd.to_numeric(e.capacity, errors="coerce"),
        "etat": "NC", "source": "OpenStreetMap", "geometry": e.geometry}, crs=C.CRS)
    pe = gpd.sjoin(pe, car[["code", "geometry"]], predicate="within", how="left") \
        .drop(columns="index_right").rename(columns={"code": "carreau_dfci"})
    ecrire(pe, "point_eau")

    vig = osm[osm.man_made == "tower"]
    poi = gpd.GeoDataFrame({"type": "VIG", "nom": vig.name, "source": "OpenStreetMap", "geometry": vig.geometry},
                           crs=C.CRS)
    ret = r[r.aire_de_retournement_dfci.notna()]
    poi = pd.concat([poi, gpd.GeoDataFrame({"type": "RET", "nom": None, "source": "BD TOPO",
                                            "geometry": ret.geometry.force_2d().interpolate(1, normalized=True)},
                                           crs=C.CRS)], ignore_index=True)
    ecrire(gpd.GeoDataFrame(poi, crs=C.CRS), "point_interet")

    feu = gpd.read_file(C.TRAITE / "perimetre_brule_2022.gpkg")
    inc = gpd.GeoDataFrame({
        "id": feu.id_feu, "nom": feu.nom, "code_bdiff": None,
        "date_debut": "2022-07-12", "date_fin": None,
        "surface_ha": feu.surface_ha, "methode": feu.source, "geometry": feu.geometry}, crs=C.CRS)
    ecrire(inc, "incendie")

    old = gpd.read_file(C.TRAITE / "old_obligations.gpkg")
    ecrire(old, "old_obligation")

    ds = gdal.OpenEx(str(C.GPKG_DFCI), gdal.OF_VECTOR | gdal.OF_UPDATE)
    table_vide(ds, "controle_old", ogr.wkbPoint, [
        ("id_obligation", ogr.OFTString), ("date_controle", ogr.OFTDate), ("agent", ogr.OFTString),
        ("statut", ogr.OFTString), ("hauteur_strate_ok", ogr.OFTInteger), ("elagage_ok", ogr.OFTInteger),
        ("dechets_verts_ok", ogr.OFTInteger), ("observation", ogr.OFTString), ("photo", ogr.OFTString),
        ("suite", ogr.OFTString)])
    table_vide(ds, "patrouille", ogr.wkbMultiLineString, [
        ("date_patrouille", ogr.OFTDate), ("equipe", ogr.OFTString), ("niveau_risque", ogr.OFTString),
        ("observation", ogr.OFTString)])
    appliquer_domaines(ds)
    ds = None
    print("Domaines de valeurs appliqués.")


if __name__ == "__main__":
    main()
