"""Projet QField de contrôle des OLD sur le terrain.

Crée qfield/controle_old.qgz et qfield/dfci_terrain.gpkg, à copier sur la tablette :
- obligations OLD de priorité forte et très forte (lecture seule, fiche d'information) ;
- couche « controle_old » éditable : formulaire en onglets, listes de valeurs issues des domaines
  du GeoPackage, date et agent préremplis, rattachement automatique à l'obligation la plus proche,
  photo en pièce jointe, contrainte « non conforme => suite obligatoire » ;
- points d'eau, pistes DFCI et carroyage pour se repérer, fond Plan IGN (en ligne).

Lancement : python-qgis-ltr.bat scripts/06_projet_qfield.py
"""
import shutil
import sys
from pathlib import Path

import geopandas as gpd
from osgeo import gdal
from qgis.core import (
    QgsApplication,
    QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsDefaultValue,
    QgsEditFormConfig,
    QgsEditorWidgetSetup,
    QgsFieldConstraints,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

DOSSIER = C.RACINE / "qfield"
GPKG = DOSSIER / "dfci_terrain.gpkg"
PLAN_IGN = ("type=xyz&zmin=0&zmax=19&url=https://data.geopf.fr/wmts?SERVICE%3DWMTS%26REQUEST%3DGetTile"
            "%26VERSION%3D1.0.0%26LAYER%3DGEOGRAPHICALGRIDSYSTEMS.PLANIGNV2%26STYLE%3Dnormal"
            "%26TILEMATRIXSET%3DPM%26TILEMATRIX%3D%7Bz%7D%26TILEROW%3D%7By%7D%26TILECOL%3D%7Bx%7D"
            "%26FORMAT%3Dimage%2Fpng")
PRIORITES = {"forte": "#e0672b", "très forte": "#9e1b1b"}


def preparer_gpkg():
    """Copie la base DFCI puis ne garde que ce qui sert sur le terrain."""
    DOSSIER.mkdir(exist_ok=True)
    if GPKG.exists():
        GPKG.unlink()
    shutil.copy(C.GPKG_DFCI, GPKG)
    ds = gdal.OpenEx(str(GPKG), gdal.OF_VECTOR | gdal.OF_UPDATE)
    for table in ("zonage_old", "incendie", "patrouille", "point_interet"):
        ds.ExecuteSQL(f"DROP TABLE {table}")
    ds.ExecuteSQL("DELETE FROM old_obligation WHERE priorite NOT IN ('forte', 'très forte')")
    ds.ExecuteSQL("VACUUM")  # compacte le fichier avant copie sur la tablette
    ds = None
    n = len(gpd.read_file(GPKG, layer="old_obligation", ignore_geometry=True))
    print(f"   {n:,} obligations prioritaires embarquées")


def couche(table, nom):
    lyr = QgsVectorLayer(f"{GPKG}|layername={table}", nom, "ogr")
    if not lyr.isValid():
        raise RuntimeError(table)
    return lyr


def widget(lyr, champ, type_, config=None):
    lyr.setEditorWidgetSetup(lyr.fields().indexOf(champ), QgsEditorWidgetSetup(type_, config or {}))


def formulaire_controle(ctrl):
    idx = ctrl.fields().indexOf
    # valeurs par défaut
    ctrl.setDefaultValueDefinition(idx("date_controle"), QgsDefaultValue("now()"))
    ctrl.setDefaultValueDefinition(idx("agent"), QgsDefaultValue("@user_full_name"))
    ctrl.setDefaultValueDefinition(idx("statut"), QgsDefaultValue("'AFAIRE'"))
    ctrl.setDefaultValueDefinition(idx("suite"), QgsDefaultValue("'AUC'"))
    # obligation la plus proche du point saisi (dans 60 m)
    ctrl.setDefaultValueDefinition(idx("id_obligation"), QgsDefaultValue(
        "array_first(overlay_nearest('Obligations OLD prioritaires', \"id_obligation\", max_distance:=60))", True))

    widget(ctrl, "date_controle", "DateTime", {"display_format": "dd/MM/yyyy", "calendar_popup": True,
                                               "field_format": "yyyy-MM-dd", "allow_null": False})
    for champ in ("hauteur_strate_ok", "elagage_ok", "dechets_verts_ok"):
        widget(ctrl, champ, "CheckBox", {"CheckedState": "1", "UncheckedState": "0"})
    widget(ctrl, "observation", "TextEdit", {"IsMultiline": True})
    widget(ctrl, "photo", "ExternalResource", {"DocumentViewer": 1, "RelativeStorage": 1,
                                               "StorageMode": 0, "FileWidget": True})
    widget(ctrl, "id_obligation", "TextEdit", {})

    # contraintes
    ctrl.setConstraintExpression(idx("agent"), "\"agent\" IS NOT NULL AND length(\"agent\") > 1", "Agent obligatoire")
    ctrl.setFieldConstraint(idx("statut"), QgsFieldConstraints.ConstraintNotNull,
                            QgsFieldConstraints.ConstraintStrengthHard)
    ctrl.setConstraintExpression(idx("suite"), "\"statut\" <> 'NCONF' OR \"suite\" <> 'AUC'",
                                 "Une non-conformité doit avoir une suite")

    # formulaire en onglets
    cfg = ctrl.editFormConfig()
    cfg.setLayout(QgsEditFormConfig.TabLayout)
    racine = cfg.invisibleRootContainer()
    racine.clear()
    onglets = {
        "Contrôle": ["date_controle", "agent", "id_obligation", "statut"],
        "Constat": ["hauteur_strate_ok", "elagage_ok", "dechets_verts_ok", "observation", "photo"],
        "Suite": ["suite"],
    }
    for titre, champs in onglets.items():
        tab = QgsAttributeEditorContainer(titre, racine)
        for c in champs:
            tab.addChildElement(QgsAttributeEditorField(c, idx(c), tab))
        racine.addChildElement(tab)
    ctrl.setEditFormConfig(cfg)
    alias = {"date_controle": "Date du contrôle", "agent": "Agent", "id_obligation": "Obligation (parcelle)",
             "statut": "Résultat", "hauteur_strate_ok": "Strate basse rabattue", "elagage_ok": "Élagage conforme",
             "dechets_verts_ok": "Rémanents évacués", "observation": "Observations", "photo": "Photo",
             "suite": "Suite donnée"}
    for c, a in alias.items():
        ctrl.setFieldAlias(idx(c), a)


def main():
    preparer_gpkg()
    qgs = QgsApplication([], False)
    qgs.initQgis()
    projet = QgsProject.instance()
    projet.setCrs(QgsCoordinateReferenceSystem(C.CRS))
    projet.setTitle("Contrôle OLD — DFCI Landiras")

    fond = QgsRasterLayer(PLAN_IGN, "Plan IGN", "wms")

    car = couche("carreau_dfci", "Carroyage DFCI")
    car.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "#2b5c8a", "outline_width": "0.3"})))
    s = QgsPalLayerSettings()
    s.fieldName = "code"
    fmt = QgsTextFormat()
    fmt.setSize(9)
    fmt.setColor(QColor("#2b5c8a"))
    s.setFormat(fmt)
    car.setLabeling(QgsVectorLayerSimpleLabeling(s))
    car.setLabelsEnabled(True)

    piste = couche("piste", "Pistes DFCI")
    piste.setRenderer(QgsSingleSymbolRenderer(
        QgsLineSymbol.createSimple({"line_color": "#8a4b20", "line_width": "0.7"})))
    piste.setReadOnly(True)

    eau = couche("point_eau", "Points d'eau")
    eau.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "#1c6fb8", "outline_color": "#ffffff", "size": "3"})))

    old = couche("old_obligation", "Obligations OLD prioritaires")
    cats = [QgsRendererCategory(v, QgsFillSymbol.createSimple(
        {"color": f"{QColor(c).red()},{QColor(c).green()},{QColor(c).blue()},90", "outline_color": c,
         "outline_width": "0.5"}), v) for v, c in PRIORITES.items()]
    old.setRenderer(QgsCategorizedSymbolRenderer("priorite", cats))
    old.setReadOnly(True)
    old.setDisplayExpression("\"id_obligation\" || ' — priorité ' || \"priorite\" || ' (' || \"score_priorite\" || ')'")

    ctrl = couche("controle_old", "Contrôles OLD")
    ctrl.setRenderer(QgsCategorizedSymbolRenderer("statut", [
        QgsRendererCategory(v, QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": c, "outline_color": "#ffffff", "size": "4"}), lib)
        for v, c, lib in [("CONF", "#2e8b57", "Conforme"), ("PART", "#e0a100", "Partiellement conforme"),
                          ("NCONF", "#c0392b", "Non conforme"), ("INACC", "#7f8c8d", "Inaccessible"),
                          ("AFAIRE", "#34495e", "À réaliser")]]))
    formulaire_controle(ctrl)

    racine = projet.layerTreeRoot()
    for lyr in (ctrl, eau, piste, old, car, fond):  # de haut en bas : contrôles au-dessus, fond en dessous
        projet.addMapLayer(lyr, False)
        racine.addLayer(lyr)

    chemin = DOSSIER / "controle_old.qgz"
    projet.write(str(chemin))
    print(f"Projet QField : {chemin}")
    qgs.exitQgis()


if __name__ == "__main__":
    main()
