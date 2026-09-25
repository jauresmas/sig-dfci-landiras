"""Production cartographique avec PyQGIS (sans interface) : projet QGIS, cartes et atlas DFCI.

Produit :
- projet/dfci_landiras.qgz : projet QGIS stylé, prêt à l'emploi ;
- sorties/cartes/carte_1_severite_2022.pdf/.png : sévérité de l'incendie (dNBR) ;
- sorties/cartes/carte_2_priorites_old.pdf/.png : obligations OLD par priorité de contrôle ;
- sorties/cartes/carte_3_diagnostic_carreaux.pdf/.png : indice de vigilance par carreau DFCI ;
- sorties/cartes/atlas_dfci_20km.pdf : atlas opérationnel, une page par carreau DFCI de 20 km.

Lancement : python-qgis-ltr.bat scripts/05_cartographie.py (environnement Python de QGIS)
"""
import sys
from pathlib import Path

import geopandas as gpd
import rasterio
from PIL import Image
from qgis.core import (
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsClassificationRange,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsGraduatedSymbolRenderer,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLegendStyle,
    QgsLineSymbol,
    QgsMapLayerLegendUtils,
    QgsMarkerSymbol,
    QgsPalettedRasterRenderer,
    QgsPalLayerSettings,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsRendererCategory,
    QgsRendererRange,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor, QFont
from rasterio.mask import mask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C

CARTES = C.SORTIES / "cartes"
CARTES.mkdir(parents=True, exist_ok=True)
CARTES_WEB = C.RACINE / "docs" / "cartes"
CARTES_WEB.mkdir(parents=True, exist_ok=True)
PROJET = C.RACINE / "projet"
PROJET.mkdir(exist_ok=True)
BASE = str(C.GPKG_DFCI)

POLICE_TITRE = "Bahnschrift"
POLICE_TEXTE = "Segoe UI"
ENCRE = QColor("#1f2a24")
ENCRE_DOUCE = QColor("#5b665f")

PLAN_IGN = ("type=xyz&zmin=0&zmax=18&url=https://data.geopf.fr/wmts?SERVICE%3DWMTS%26REQUEST%3DGetTile"
            "%26VERSION%3D1.0.0%26LAYER%3DGEOGRAPHICALGRIDSYSTEMS.PLANIGNV2%26STYLE%3Dnormal"
            "%26TILEMATRIXSET%3DPM%26TILEMATRIX%3D%7Bz%7D%26TILEROW%3D%7By%7D%26TILECOL%3D%7Bx%7D"
            "%26FORMAT%3Dimage%2Fpng")

SEVERITE = [  # code, libellé, couleur
    (1, "Non brûlé / repousse", "#c9d3a8"),
    (2, "Faible", "#fed976"),
    (3, "Modérée-faible", "#fd8d3c"),
    (4, "Modérée-forte", "#e31a1c"),
    (5, "Forte", "#67000d"),
]
PRIORITES = [("faible", "Faible", "#d6dcc9"), ("moyenne", "Moyenne", "#f2c14e"),
             ("forte", "Forte", "#e0672b"), ("très forte", "Très forte", "#9e1b1b")]


# --------------------------------------------------------------------------------------
# Couches
# --------------------------------------------------------------------------------------
def couche(table, nom, fichier=BASE, filtre=None):
    uri = f"{fichier}|layername={table}" + (f"|subset={filtre}" if filtre else "")
    lyr = QgsVectorLayer(uri, nom, "ogr")
    if not lyr.isValid():
        raise RuntimeError(f"Couche invalide : {uri}")
    return lyr


def fond_plan():
    lyr = QgsRasterLayer(PLAN_IGN, "Plan IGN (Géoplateforme)", "wms")
    lyr.renderer().setOpacity(0.55)
    lyr.hueSaturationFilter().setSaturation(-80)
    return lyr


def remplissage(couleur="#00000000", contour="#333333", largeur=0.3, style="solid"):
    return QgsFillSymbol.createSimple({"color": couleur, "outline_color": contour,
                                       "outline_width": str(largeur), "outline_style": style})


def etiquettes(lyr, champ, taille=7, couleur="#1f2a24", tampon=True, gras=False, expression=False):
    s = QgsPalLayerSettings()
    s.fieldName = champ
    s.isExpression = expression
    fmt = QgsTextFormat()
    f = QFont(POLICE_TEXTE)
    f.setBold(gras)
    fmt.setFont(f)
    fmt.setSize(taille)
    fmt.setColor(QColor(couleur))
    if tampon:
        b = QgsTextBufferSettings()
        b.setEnabled(True)
        b.setSize(0.8)
        b.setColor(QColor("#ffffff"))
        fmt.setBuffer(b)
    s.setFormat(fmt)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(s))
    lyr.setLabelsEnabled(True)


def raster_severite_incendie():
    """Classes de sévérité limitées au périmètre de l'incendie (pour l'affichage)."""
    sortie = C.RASTER / "severite_incendie.tif"
    feu = gpd.read_file(BASE, layer="incendie")
    with rasterio.open(C.RASTER / "severite_classes.tif") as src:
        arr, tr = mask(src, feu.geometry, crop=True, nodata=0)
        prof = src.profile | {"height": arr.shape[1], "width": arr.shape[2], "transform": tr, "nodata": 0}
    with rasterio.open(sortie, "w", **prof) as dst:
        dst.write(arr)
    lyr = QgsRasterLayer(str(sortie), "Sévérité de l'incendie (dNBR)")
    classes = [QgsPalettedRasterRenderer.Class(c, QColor(col), lib) for c, lib, col in SEVERITE]
    lyr.setRenderer(QgsPalettedRasterRenderer(lyr.dataProvider(), 1, classes))
    return lyr


def styles():
    L = {}
    L["fond"] = fond_plan()

    L["communes"] = QgsVectorLayer(f"{C.BRUT / 'communes.gpkg'}", "Communes", "ogr")
    L["communes"].setRenderer(QgsSingleSymbolRenderer(remplissage(contour="#555555", largeur=0.3, style="dash")))
    etiquettes(L["communes"], "upper(nom_officiel)", 7, "#3d3d3d", expression=True, gras=True)

    L["massif"] = couche("zonage_old", "Massifs soumis aux OLD", filtre="type_zone = 'massif'")
    L["massif"].setRenderer(QgsSingleSymbolRenderer(remplissage("127,164,106,90", "#00000000", 0)))

    L["zone_old"] = couche("zonage_old", "Zone d'application OLD (massifs + 200 m)",
                           filtre="type_zone = 'zone_application'")
    L["zone_old"].setRenderer(QgsSingleSymbolRenderer(remplissage("232,217,168,80", "184,154,74,160", 0.2)))

    L["incendie"] = couche("incendie", "Périmètre de l'incendie 2022 (dNBR)")
    L["incendie"].setRenderer(QgsSingleSymbolRenderer(remplissage("#00000000", "#111111", 0.7)))

    L["severite"] = raster_severite_incendie()

    L["carreaux"] = couche("carreau_dfci", "Carroyage DFCI 2 km")
    L["carreaux"].setRenderer(QgsSingleSymbolRenderer(remplissage("#00000000", "#2b5c8a", 0.25)))
    etiquettes(L["carreaux"], "code", 6.5, "#2b5c8a")

    L["pistes"] = couche("piste", "Pistes DFCI (BD TOPO)")
    cats = []
    for val, lib, col, larg in [("Super poids lourd", "Gabarit super poids lourd", "#6b3a1e", 0.9),
                                ("Poids lourd", "Gabarit poids lourd", "#b0662c", 0.6)]:
        cats.append(QgsRendererCategory(val, QgsLineSymbol.createSimple(
            {"line_color": col, "line_width": str(larg)}), lib))
    L["pistes"].setRenderer(QgsCategorizedSymbolRenderer("gabarit", cats))

    L["eau"] = couche("point_eau", "Points d'eau (OSM)")
    cats = []
    for val, lib, forme, col, taille in [("PI", "Poteau d'incendie", "circle", "#1c6fb8", 1.8),
                                         ("CIT", "Citerne / réserve", "square", "#0b3d6b", 2.6),
                                         ("RN", "Réserve naturelle", "diamond", "#2a9fd6", 2.6)]:
        cats.append(QgsRendererCategory(val, QgsMarkerSymbol.createSimple(
            {"name": forme, "color": col, "outline_color": "#ffffff", "outline_width": "0.3",
             "size": str(taille)}), lib))
    L["eau"].setRenderer(QgsCategorizedSymbolRenderer("type", cats))

    L["old"] = couche("old_obligation", "Obligations OLD (priorité de contrôle)")
    cats = [QgsRendererCategory(v, remplissage(col, "#00000000", 0), lib) for v, lib, col in PRIORITES]
    L["old"].setRenderer(QgsCategorizedSymbolRenderer("priorite", cats))

    L["diag"] = QgsVectorLayer(str(C.TRAITE / "diagnostic_carreaux.gpkg"), "Indice de vigilance DFCI", "ogr")
    bornes = [(0, 35, "#f1eef6"), (35, 40, "#d4b9da"), (40, 45, "#c994c7"), (45, 50, "#df65b0"), (50, 100, "#980043")]
    rngs = [QgsRendererRange(QgsClassificationRange(f"{a} – {b}", a, b), remplissage(c, "#ffffff", 0.2))
            for a, b, c in bornes]
    L["diag"].setRenderer(QgsGraduatedSymbolRenderer("indice_vigilance", rngs))
    etiquettes(L["diag"], "format_number(\"indice_vigilance\", 0)", 6, "#222222", expression=True)
    return L


# --------------------------------------------------------------------------------------
# Mise en page
# --------------------------------------------------------------------------------------
def texte(layout, contenu, x, y, w, h, taille=9, police=POLICE_TEXTE, gras=False, couleur=ENCRE):
    lab = QgsLayoutItemLabel(layout)
    lab.setText(contenu)
    fmt = QgsTextFormat()
    f = QFont(police)
    f.setBold(gras)
    fmt.setFont(f)
    fmt.setSize(taille)
    fmt.setColor(couleur)
    lab.setTextFormat(fmt)
    lab.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    lab.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(lab)
    return lab


def fr(n, dec=0):
    """Nombre au format français (espace fine insécable pour les milliers)."""
    return f"{n:,.{dec}f}".replace(",", " ").replace(".", ",")


def mise_en_page(nom, titre, sous_titre, couches, etendue, legende_couches, notes, source, segment_km=None):
    """Gabarit A3 paysage commun : carte à gauche, bandeau d'information à droite."""
    projet = QgsProject.instance()
    layout = QgsPrintLayout(projet)
    layout.initializeDefaults()
    layout.setName(nom)
    page = layout.pageCollection().page(0)
    page.setPageSize("A3", 1)  # paysage

    carte = QgsLayoutItemMap(layout)
    carte.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
    carte.attemptResize(QgsLayoutSize(300, 277, QgsUnitTypes.LayoutMillimeters))
    carte.setCrs(QgsCoordinateReferenceSystem(C.CRS))
    carte.setLayers(couches)
    carte.setKeepLayerSet(True)  # sinon la carte affiche toutes les couches visibles à la réouverture
    carte.setExtent(etendue)
    carte.setFrameEnabled(True)
    carte.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))
    grille = QgsLayoutItemMapGrid("Lambert-93", carte)
    grille.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)
    grille.setIntervalX(10000)
    grille.setIntervalY(10000)
    grille.setAnnotationEnabled(True)
    grille.setAnnotationPrecision(0)
    fg = QgsTextFormat()
    fg.setFont(QFont(POLICE_TEXTE))
    fg.setSize(6)
    fg.setColor(ENCRE_DOUCE)
    grille.setAnnotationTextFormat(fg)
    grille.setFrameStyle(QgsLayoutItemMapGrid.LineBorder)
    grille.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)
    carte.grids().addGrid(grille)
    layout.addLayoutItem(carte)

    x0 = 318
    texte(layout, "SIG DFCI · MASSIF DES LANDES DE GASCOGNE", x0, 12, 95, 6, 7.5, POLICE_TITRE, couleur=ENCRE_DOUCE)
    texte(layout, titre, x0, 19, 95, 26, 17, POLICE_TITRE, gras=True)
    texte(layout, sous_titre, x0, 46, 95, 20, 9, couleur=ENCRE_DOUCE)

    leg = QgsLayoutItemLegend(layout)
    leg.setTitle("")
    leg.setLinkedMap(carte)
    leg.setAutoUpdateModel(False)
    racine = leg.model().rootGroup()
    racine.clear()
    for lyr in legende_couches:
        noeud = racine.addLayer(lyr)
        if isinstance(lyr, QgsRasterLayer) and lyr.renderer().type() == "paletted":
            # masque l'en-tête « Band 1 » : ne garder que les classes
            n = len(lyr.renderer().classes())
            QgsMapLayerLegendUtils.setLegendNodeOrder(noeud, list(range(1, n + 1)))
            leg.model().refreshLayerLegend(noeud)
    for style, taille, gras in [(QgsLegendStyle.Group, 9, True), (QgsLegendStyle.Subgroup, 8.5, True),
                                (QgsLegendStyle.SymbolLabel, 8, False)]:
        f = QFont(POLICE_TEXTE)
        f.setBold(gras)
        tf = QgsTextFormat()
        tf.setFont(f)
        tf.setSize(taille)
        tf.setColor(ENCRE)
        leg.rstyle(style).setTextFormat(tf)
    leg.setSymbolHeight(3.5)
    leg.setSymbolWidth(6)
    leg.attemptMove(QgsLayoutPoint(x0, 68, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(leg)

    texte(layout, notes, x0, 170, 95, 80, 8)

    echelle = QgsLayoutItemScaleBar(layout)
    echelle.setStyle("Single Box")
    echelle.setLinkedMap(carte)
    echelle.setUnits(QgsUnitTypes.DistanceKilometers)
    echelle.setUnitLabel("km")
    echelle.setNumberOfSegments(4)
    echelle.setNumberOfSegmentsLeft(0)
    if segment_km is None:  # 4 segments occupant environ 80 mm
        brut = carte.scale() * 0.02 / 1000
        segment_km = min((0.1, 0.25, 0.5, 1, 2, 2.5, 5), key=lambda s: abs(s - brut))
    echelle.setUnitsPerSegment(segment_km)
    tf = QgsTextFormat()
    tf.setFont(QFont(POLICE_TEXTE))
    tf.setSize(7)
    echelle.setTextFormat(tf)
    echelle.attemptMove(QgsLayoutPoint(x0, 256, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(echelle)

    nord = QgsLayoutItemPicture(layout)
    nord.setPicturePath(QgsApplication.defaultThemePath() + "/../../svg/arrows/NorthArrow_02.svg")
    nord.attemptMove(QgsLayoutPoint(x0 + 80, 250, QgsUnitTypes.LayoutMillimeters))
    nord.attemptResize(QgsLayoutSize(12, 14, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(nord)

    texte(layout, source, x0, 270, 95, 18, 6.5, couleur=ENCRE_DOUCE)
    return layout, carte


def exporter(layout, nom):
    ex = QgsLayoutExporter(layout)
    pdf = QgsLayoutExporter.PdfExportSettings()
    pdf.dpi = 200
    ex.exportToPdf(str(CARTES / f"{nom}.pdf"), pdf)
    img = QgsLayoutExporter.ImageExportSettings()
    img.dpi = 110
    ex.exportToImage(str(CARTES / f"{nom}.png"), img)
    apercu_web(CARTES / f"{nom}.png")
    QgsProject.instance().layoutManager().addLayout(layout)
    print(f"   {nom}")


def apercu_web(png):
    """Copie JPEG allégée dans docs/cartes (README et site) ; les PDF restent hors dépôt."""
    Image.open(png).convert("RGB").save(CARTES_WEB / f"{png.stem}.jpg", quality=82, optimize=True)


def rgba(hexa, alpha):
    c = QColor(hexa)
    return f"{c.red()},{c.green()},{c.blue()},{alpha}"


def etendue(lyr, marge=0.04):
    r = QgsRectangle(lyr.extent())
    r.grow(max(r.width(), r.height()) * marge)
    return cadrer(r)


def cadrer(r):
    """Ajuste l'emprise au ratio du cadre carte (300 x 277 mm)."""
    ratio = 300 / 277
    if r.width() / r.height() < ratio:
        r.setXMinimum(r.center().x() - r.height() * ratio / 2)
        r.setXMaximum(r.center().x() + r.height() * ratio / 2)
    else:
        r.setYMinimum(r.center().y() - r.width() / ratio / 2)
        r.setYMaximum(r.center().y() + r.width() / ratio / 2)
    return r


SOURCE = ("Sources : IGN (BD TOPO, BD Forêt V2, zonage OLD, carroyage DFCI, Plan IGN) · "
          "Copernicus Sentinel-2 L2A via Microsoft Planetary Computer · OpenStreetMap · Cadastre Etalab. "
          "Réalisation : J. Daa-Hingbanon, projet sig-dfci-landiras (données ouvertes, 2026).")


def main():
    qgs = QgsApplication([], False)
    qgs.initQgis()
    projet = QgsProject.instance()
    projet.setCrs(QgsCoordinateReferenceSystem(C.CRS))
    projet.setTitle("SIG DFCI Landiras")
    L = styles()
    ordre = ["eau", "pistes", "incendie", "carreaux", "communes", "old", "diag", "severite", "zone_old", "massif",
             "fond"]
    racine = projet.layerTreeRoot()
    for k in ordre:  # de haut en bas : points d'eau au-dessus, fond Plan IGN en dessous
        projet.addMapLayer(L[k], False)
        racine.addLayer(L[k])
    groupe_mep = racine.addGroup("Couches des mises en page (zoom Landiras, atlas)")
    groupe_mep.setItemVisibilityChecked(False)
    for k in ["diag", "old", "carreaux"]:
        projet.layerTreeRoot().findLayer(L[k].id()).setItemVisibilityChecked(False)

    import pandas as pd
    sev = pd.read_csv(C.SORTIES / "severite_par_classe.csv")
    feu_ha = gpd.read_file(BASE, layer="incendie").area.sum() / 10000
    old_s = pd.read_csv(C.SORTIES / "old_synthese_communes.csv")
    diag = pd.read_csv(C.SORTIES / "diagnostic_carreaux.csv")

    # --- carte 1 : sévérité
    lignes = "\n".join(f"{r.classe} : {fr(r.surface_ha)} ha ({r.surface_ha / feu_ha:.0%})"
                       for r in sev.itertuples() if r.code > 1)
    layout, _ = mise_en_page(
        "carte_1_severite_2022", "Sévérité de l'incendie\nde Landiras (2022)",
        f"Différence de Normalized Burn Ratio entre le {C.S2_AVANT} et le {C.S2_APRES} (Sentinel-2, 20 m)",
        [L["incendie"], L["communes"], L["severite"], L["fond"]], etendue(L["incendie"], 0.08),
        [L["severite"], L["incendie"]],
        f"Surface parcourue estimée : {fr(feu_ha)} ha\n\n" + lignes +
        "\n\nMéthode : dNBR = NBR avant − NBR après, NBR = (B8A − B12) / (B8A + B12), "
        "masque nuages/eau par la couche SCL. Seuils de Key & Benson (2006). Le périmètre retient "
        "le complexe de plus de 2 000 ha ; cultures annuelles du RPG 2022 (récoltes) et coupes rases exclues.",
        SOURCE)
    exporter(layout, "carte_1_severite_2022")

    # --- carte 2 : priorités OLD
    tot = old_s.obligations.sum()
    layout, _ = mise_en_page(
        "carte_2_priorites_old", "Obligations légales\nde débroussaillement",
        "Priorité de contrôle des 15 communes touchées par l'incendie de 2022",
        [L["incendie"], L["communes"], L["old"], L["zone_old"], L["fond"]], etendue(L["old"], 0.03),
        [L["old"], L["zone_old"], L["incendie"]],
        f"{fr(tot)} obligations (parcelles bâties), {fr(old_s.constructions.sum())} constructions.\n"
        f"{fr(old_s.surface_nette_ha.sum())} ha nets à débroussailler, dont "
        f"{fr(old_s.dont_chez_voisins_non_batis_ha.sum())} ha chez des voisins non bâtis.\n"
        f"{fr(old_s.en_lisiere.sum())} obligations « en lisière » (≥ 50 % du rayon chez un tiers non bâti).\n\n"
        "Rayon de 50 m autour des constructions situées dans la zone d'application (massifs + 200 m). "
        "Score de priorité (0–10) : exposition au massif (40 %), part de résineux dans 200 m (20 %), "
        "isolement de la construction (20 %), éloignement du point d'eau recensé (20 %).",
        SOURCE)
    exporter(layout, "carte_2_priorites_old")

    # --- carte 2b : zoom à la parcelle sur le bourg de Landiras
    parcelles = QgsVectorLayer(str(C.BRUT / "cadastre" / "parcelles_33225.gpkg"), "Parcelles cadastrales", "ogr")
    parcelles.setRenderer(QgsSingleSymbolRenderer(remplissage("#00000000", "120,120,120,170", 0.15)))
    bati = QgsVectorLayer(str(C.BRUT / "batiments.gpkg"), "Bâtiments (BD TOPO)", "ogr")
    bati.setRenderer(QgsSingleSymbolRenderer(remplissage("#3a3a3a", "#3a3a3a", 0.05)))
    old_zoom = couche("old_obligation", "Rayon OLD de 50 m (priorité)", filtre="code_insee = '33225'")
    cats = [QgsRendererCategory(v, remplissage(rgba(col, 110), rgba(col, 255), 0.3), lib) for v, lib, col in PRIORITES]
    old_zoom.setRenderer(QgsCategorizedSymbolRenderer("priorite", cats))
    for lyr in (old_zoom, bati, parcelles):
        projet.addMapLayer(lyr, False)
        groupe_mep.addLayer(lyr)
    centre = gpd.read_file(BASE, layer="old_obligation", where="code_insee = '33225'")
    cx, cy = centre.geometry.centroid.x.median(), centre.geometry.centroid.y.median()
    zoom = cadrer(QgsRectangle(cx - 1300, cy - 1200, cx + 1300, cy + 1200))
    lis = centre[centre.en_lisiere.astype(bool)]
    layout, _ = mise_en_page(
        "carte_2b_old_landiras", "OLD à la parcelle :\nbourg de Landiras",
        "Rayons de 50 m, parcelles cadastrales et priorité de contrôle",
        [bati, old_zoom, parcelles, L["incendie"], L["zone_old"], L["fond"]], zoom,
        [old_zoom, parcelles, bati, L["zone_old"], L["incendie"]],
        f"Landiras : {fr(len(centre))} obligations, dont {fr(len(lis))} « en lisière » : plus de la moitié "
        "du débroussaillement se fait chez un voisin non bâti, le plus souvent une parcelle forestière. "
        "Ces situations appellent une information du propriétaire forestier sur l'accès "
        "à son terrain pour les travaux.\n\n"
        "Une obligation regroupe la maison et ses annexes situées sur la même parcelle ; "
        "les emprises bâties sont retirées du rayon.",
        SOURCE)
    exporter(layout, "carte_2b_old_landiras")

    # --- carte 3 : diagnostic carreaux
    top = diag.nlargest(5, "indice_vigilance")
    layout, _ = mise_en_page(
        "carte_3_diagnostic_carreaux", "Vigilance DFCI\npar carreau de 2 km",
        "Accès, eau, combustible et pression bâtie sur le carroyage DFCI national",
        [L["incendie"], L["communes"], L["diag"], L["fond"]], etendue(L["diag"], 0.02),
        [L["diag"], L["incendie"]],
        "Indice 0–100 : massif à plus de 500 m d'une voie carrossable (30 %), à plus de 1 km d'un point d'eau "
        "recensé (25 %), part de résineux (25 %), nombre de constructions (20 %).\n\n"
        "Carreaux les plus vigilants : " + ", ".join(f"{r.code} ({r.indice_vigilance:.0f})" for r in top.itertuples()) +
        ".\n\nLes points d'eau DFCI ne sont que partiellement publiés en données ouvertes : l'indice doit être "
        "recalculé avec les inventaires des ASA DFCI et du SDIS.",
        SOURCE)
    exporter(layout, "carte_3_diagnostic_carreaux")

    # --- atlas opérationnel par carreau de 20 km
    f20 = C.TRAITE / "carreaux_dfci_20km.gpkg"
    gpd.read_file(BASE, layer="carreau_dfci").dissolve("code_20km").reset_index()[["code_20km", "geometry"]] \
        .to_file(f20, driver="GPKG", mode="w")
    diss = QgsVectorLayer(str(f20), "Carreaux DFCI 20 km", "ogr")
    diss.setRenderer(QgsSingleSymbolRenderer(remplissage("#00000000", "#2b5c8a", 1.0)))
    projet.addMapLayer(diss, False)
    groupe_mep.addLayer(diss)

    layout, carte = mise_en_page(
        "atlas_dfci_20km", "Atlas opérationnel DFCI", "",
        [L["eau"], L["pistes"], L["incendie"], L["carreaux"], L["communes"], L["massif"], L["fond"]],
        etendue(L["incendie"]), [L["pistes"], L["eau"], L["massif"], L["carreaux"], L["incendie"]],
        "Carroyage DFCI : grille nationale de localisation des moyens de lutte (carreaux de 2 km).\n\n"
        "Pistes : tronçons BD TOPO portant l'attribut piste DFCI (gabarit renseigné). "
        "Points d'eau : OpenStreetMap, à compléter par les inventaires ASA DFCI / SDIS avant usage opérationnel.",
        SOURCE, segment_km=1)
    atlas = layout.atlas()
    atlas.setCoverageLayer(diss)
    atlas.setEnabled(True)
    atlas.setPageNameExpression("code_20km")
    atlas.setSortFeatures(True)
    atlas.setSortExpression("code_20km")
    carte.setAtlasDriven(True)
    carte.setAtlasScalingMode(QgsLayoutItemMap.Auto)
    carte.setAtlasMargin(0.04)
    texte(layout, "", 318, 46, 95, 20, 11, POLICE_TITRE, gras=True).setText(
        "Carreau [% \"code_20km\" %] · page [% @atlas_featurenumber %] / [% @atlas_totalfeatures %]")
    reglages = QgsLayoutExporter.PdfExportSettings()
    reglages.dpi = 150
    res, err = QgsLayoutExporter.exportToPdf(atlas, str(CARTES / "atlas_dfci_20km.pdf"), reglages)
    print(f"   atlas_dfci_20km ({atlas.count()} pages) {err or ''}")
    # une image par page d'atlas, puis aperçus web
    atlas.setFilenameExpression("'atlas_' || \"code_20km\"")
    img = QgsLayoutExporter.ImageExportSettings()
    img.dpi = 110
    QgsLayoutExporter.exportToImage(atlas, str(CARTES / "atlas"), "png", img)
    for png in sorted(CARTES.glob("atlas_DE*.png")):
        apercu_web(png)
    projet.layoutManager().addLayout(layout)

    projet.write(str(PROJET / "dfci_landiras.qgz"))
    print(f"Projet QGIS : {PROJET / 'dfci_landiras.qgz'}")
    qgs.exitQgis()


if __name__ == "__main__":
    main()
