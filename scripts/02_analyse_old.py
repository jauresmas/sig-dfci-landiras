"""Analyse des Obligations Légales de Débroussaillement (OLD) sur les communes touchées en 2022.

Règle appliquée (Code forestier art. L134-6, arrêté préfectoral OLD de la Gironde) :
- sont soumis les terrains situés à moins de 200 m des bois, forêts et landes du zonage OLD ;
- autour de chaque construction située dans cette zone, débroussaillement sur 50 m,
  y compris lorsque ce rayon déborde sur les parcelles voisines.

Étapes :
1. Zone d'application officielle (zonage OLD IGN, massifs + 200 m), contrôlée par recalcul.
2. Constructions soumises = bâtiments BD TOPO en service, >= 20 m², dans la zone d'application.
3. Une obligation par parcelle bâtie (maison + annexes = un même obligé) ; son périmètre est
   le tampon de 50 m autour des constructions ∩ zone d'application − emprises bâties.
4. Débordement cadastral (Etalab) : surface à débroussailler chez des tiers, parcelles voisines touchées.
5. Score de priorité de contrôle (0–10) : exposition au massif, combustible résineux,
   isolement de la construction, éloignement du premier point d'eau.

Sorties : data/traite/old_obligations.gpkg, sorties/old_synthese_communes.csv
Limites : les zones U des PLU (débroussaillement de toute la parcelle) et les rayons portés
à 100 m par arrêté municipal ne sont pas intégrés ; faute d'accès aux Fichiers fonciers, une
parcelle voisine appartenant au même propriétaire est comptée comme « tiers ».
"""
import gzip
import io
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

CADASTRE = "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/communes/{dep}/{insee}/cadastre-{insee}-parcelles.json.gz"
SURFACE_BATI_MIN = 20       # m², écarte abris et annexes légères
MARGE_COMMUNES = 1000       # m autour du périmètre brûlé pour sélectionner les communes
SURFACE_TIERS_MIN = 25      # m², ignore les micro-débords dus aux décalages cadastre / BD TOPO

# poids du score de priorité (somme = 10)
POIDS = {"exposition": 4, "resineux": 2, "isolement": 2, "eau": 2}


def communes_etudiees():
    communes = gpd.read_file(C.BRUT / "communes.gpkg")
    feu = gpd.read_file(C.TRAITE / "perimetre_brule_2022.gpkg")
    sel = communes[communes.intersects(feu.buffer(MARGE_COMMUNES).union_all())]
    return sel[["code_insee", "nom_officiel", "geometry"]].rename(columns={"nom_officiel": "commune"})


def parcelles(codes):
    dossier = C.BRUT / "cadastre"
    dossier.mkdir(exist_ok=True)
    frames = []
    for insee in codes:
        f = dossier / f"parcelles_{insee}.gpkg"
        if not f.exists():
            r = requests.get(CADASTRE.format(dep=insee[:2], insee=insee), timeout=120)
            r.raise_for_status()
            data = gzip.decompress(r.content) if r.content[:2] == b"\x1f\x8b" else r.content
            colonnes = ["id", "commune", "section", "numero", "contenance", "geometry"]
            gpd.read_file(io.BytesIO(data)).to_crs(C.CRS)[colonnes].to_file(f, driver="GPKG")
            print(f"   cadastre {insee} téléchargé")
        frames.append(gpd.read_file(f))
    p = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=C.CRS)
    return p.rename(columns={"id": "idu"})


def normaliser(s, bas, haut, inverse=False):
    """Ramène une variable sur [0, 1] entre deux bornes métier."""
    v = ((s - bas) / (haut - bas)).clip(0, 1)
    return 1 - v if inverse else v


def main():
    communes = communes_etudiees()
    emprise = communes.union_all()
    print(f"Communes étudiées ({len(communes)}) : {', '.join(sorted(communes.commune))}")

    # 1. zone d'application : la couche IGN porte les massifs (zonage = 1) et la zone officielle
    #    d'application, bande de 200 m et ajustements locaux compris (zonage = 2)
    z = gpd.read_file(C.BRUT / "zonage_old.gpkg", mask=emprise.buffer(C.OLD_BANDE_FORET))
    massifs = z[z.zonage == 1].reset_index(drop=True)
    zone = z[z.zonage == 2].union_all().intersection(emprise)
    # contrôle qualité : comparaison avec la bande de 200 m recalculée
    recalc = massifs.union_all().buffer(C.OLD_BANDE_FORET).intersection(emprise)
    ecart = recalc.symmetric_difference(zone).area / zone.area
    print(f"Zone d'application officielle : {zone.area / 1e4:,.0f} ha "
          f"(écart avec massifs + 200 m recalculés : {ecart:.1%})")
    gpd.GeoDataFrame({"nom": ["zone_application_old"]}, geometry=[zone], crs=C.CRS) \
        .to_file(C.TRAITE / "old_zone_application.gpkg", driver="GPKG", mode="w")

    # 2. constructions soumises
    bati = gpd.read_file(C.BRUT / "batiments.gpkg", mask=emprise)
    bati = bati[(bati.etat_de_l_objet == "En service") & (bati.area >= SURFACE_BATI_MIN)]
    bati = bati[bati.intersects(zone)].copy()
    print(f"Constructions soumises aux OLD : {len(bati):,}")

    # 3. une obligation par parcelle bâtie : maison + annexes = un même obligé
    parc = parcelles(sorted(communes.code_insee))
    centres = gpd.GeoDataFrame(bati[["cleabs"]], geometry=bati.representative_point(), crs=C.CRS)
    rattach = gpd.sjoin(centres, parc[["idu", "geometry"]], predicate="within", how="left") \
        .drop_duplicates("cleabs").set_index("cleabs")["idu"]
    bati["id_obligation"] = bati.cleabs.map(rattach).fillna(bati.cleabs)  # hors cadastre : le bâtiment seul
    groupes = bati.dissolve("id_obligation", aggfunc={"cleabs": "count"}).rename(columns={"cleabs": "nb_batiments"})
    groupes = gpd.sjoin(groupes, communes[["code_insee", "commune", "geometry"]], predicate="intersects", how="left") \
        .drop(columns="index_right")
    groupes = groupes[~groupes.index.duplicated()]
    print(f"Obligations (parcelles bâties) : {len(groupes):,}")

    emprises_bati = gpd.read_file(C.BRUT / "batiments.gpkg", mask=emprise.buffer(C.OLD_RAYON_BATI))
    sindex = emprises_bati.sindex
    geoms = []
    for g in groupes.geometry:
        disque = g.buffer(C.OLD_RAYON_BATI).intersection(zone)
        voisins = emprises_bati.geometry.iloc[sindex.query(disque, predicate="intersects")]
        geoms.append(disque.difference(voisins.union_all()) if len(voisins) else disque)
    old = groupes[["nb_batiments", "code_insee", "commune"]].reset_index()
    old["geom_bati"] = groupes.geometry.values
    old = gpd.GeoDataFrame(old, geometry=gpd.GeoSeries(geoms, crs=C.CRS), crs=C.CRS)
    old["idu_parcelle"] = old.id_obligation.where(old.id_obligation.isin(parc.idu))
    old["surface_old_m2"] = old.area.round()

    # 4. débordement sur les parcelles voisines (débroussaillement chez des tiers)
    #    (voirie et domaine non cadastré exclus : seules comptent les autres parcelles)
    inter = gpd.overlay(old[["id_obligation", "idu_parcelle", "geometry"]], parc[["idu", "geometry"]],
                        how="intersection", keep_geom_type=True)
    inter["m2"] = inter.area
    inter = inter[(inter.idu != inter.idu_parcelle) & (inter.m2 >= SURFACE_TIERS_MIN)]
    parcelles_baties = set(old.idu_parcelle.dropna())
    inter["non_batie"] = ~inter.idu.isin(parcelles_baties)
    par_obl = inter.groupby("id_obligation")
    old["nb_parcelles_tierces"] = old.id_obligation.map(par_obl.idu.nunique()).fillna(0).astype(int)
    old["surface_chez_tiers_m2"] = old.id_obligation.map(par_obl.m2.sum()).fillna(0).round()
    old["surface_tiers_non_batis_m2"] = old.id_obligation.map(
        inter[inter.non_batie].groupby("id_obligation").m2.sum()).fillna(0).round()
    # « en lisière » : au moins la moitié du débroussaillement se fait chez un voisin non bâti
    # (typiquement une parcelle forestière) -> information du propriétaire, conventions d'accès
    old["part_tiers_non_batis"] = (old.surface_tiers_non_batis_m2 / old.surface_old_m2).round(2)
    old["en_lisiere"] = old.part_tiers_non_batis >= 0.5
    non_batis_net = inter[inter.non_batie].union_all().area / 10000

    # 5. score de priorité
    #    exposition : distance du bâti au massif (0 m -> 1, >= 200 m -> 0)
    massifs_idx = massifs.sindex
    old["dist_massif_m"] = [
        round(massifs.geometry.iloc[massifs_idx.nearest(g)[1]].distance(g).min(), 1) for g in old.geom_bati]
    #    résineux : part de conifères (BD Forêt V2) dans le rayon de 200 m
    foret = gpd.read_file(C.BRUT / "bdforet_v2.gpkg", mask=emprise.buffer(C.OLD_BANDE_FORET))
    resineux = foret[foret.tfv_g11.str.contains("conifères", na=False) | foret.essence.str.contains("Pin", na=False)]
    res_u = resineux.union_all()
    anneau = old.geom_bati.buffer(C.OLD_BANDE_FORET)
    old["part_resineux"] = (anneau.intersection(res_u).area / anneau.area).round(3)
    #    isolement : nombre d'autres constructions à moins de 100 m (habitat diffus = plus exposé)
    tampons = gpd.GeoDataFrame(geometry=old.geom_bati.buffer(100), crs=C.CRS)
    voisins = gpd.sjoin(tampons, gpd.GeoDataFrame(geometry=emprises_bati.geometry, crs=C.CRS), predicate="intersects")
    old["nb_bati_100m"] = (voisins.groupby(level=0).size().reindex(old.index).fillna(0)
                           - old.nb_batiments).clip(lower=0).astype(int)
    #    eau : distance au point d'eau DFCI le plus proche (OSM)
    eau = gpd.read_file(C.BRUT / "osm_equipements_dfci.gpkg")
    eau = eau[eau.emergency.notna()]
    old["dist_point_eau_m"] = gpd.sjoin_nearest(
        gpd.GeoDataFrame(geometry=old.geom_bati.centroid, crs=C.CRS), eau[["geometry"]], distance_col="d"
    ).groupby(level=0).d.min().reindex(old.index).round()

    score = (POIDS["exposition"] * normaliser(old.dist_massif_m, 0, C.OLD_BANDE_FORET, inverse=True)
             + POIDS["resineux"] * old.part_resineux
             + POIDS["isolement"] * normaliser(old.nb_bati_100m, 0, 10, inverse=True)
             + POIDS["eau"] * normaliser(old.dist_point_eau_m.fillna(2000), 150, 1000))
    old["score_priorite"] = score.round(1)
    old["priorite"] = pd.cut(old.score_priorite, [-1, 4, 6, 7.5, 11],
                             labels=["faible", "moyenne", "forte", "très forte"]).astype(str)
    old["statut"] = "AFAIRE"

    old = old.drop(columns="geom_bati")
    old.to_file(C.TRAITE / "old_obligations.gpkg", driver="GPKG", mode="w")

    # synthèse communale ; surfaces nettes = sans double compte des recouvrements entre obligations
    nette = old.dissolve("code_insee").area / 10000
    non_batis = gpd.GeoDataFrame(geometry=[inter[inter.non_batie].union_all()], crs=C.CRS)
    nette_nb = gpd.overlay(communes[["code_insee", "geometry"]], non_batis, how="intersection") \
        .set_index("code_insee").area / 10000
    synth = old.groupby(["code_insee", "commune"]).agg(
        obligations=("id_obligation", "size"),
        constructions=("nb_batiments", "sum"),
        en_lisiere=("en_lisiere", "sum"),
        priorite_forte_ou_plus=("priorite", lambda s: int(s.isin(["forte", "très forte"]).sum())),
    ).reset_index()
    synth.insert(4, "surface_nette_ha", synth.code_insee.map(nette).round(1))
    synth.insert(5, "dont_chez_voisins_non_batis_ha", synth.code_insee.map(nette_nb).round(1))
    synth = synth.sort_values("obligations", ascending=False)
    synth.to_csv(C.SORTIES / "old_synthese_communes.csv", index=False, encoding="utf-8")
    print(synth.to_string(index=False))
    print(f"\nTotal : {len(old):,} obligations ({old.nb_batiments.sum():,} constructions), "
          f"{synth.surface_nette_ha.sum():,.0f} ha nets à débroussailler, "
          f"dont {non_batis_net:,.0f} ha sur des parcelles voisines non bâties ; "
          f"{old.en_lisiere.sum():,} obligations en lisière ({old.en_lisiere.mean():.0%})")
    print(old.priorite.value_counts().to_string())


if __name__ == "__main__":
    main()
