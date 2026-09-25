// Carte web du projet SIG DFCI Landiras (MapLibre GL, données exportées par scripts/07_export_web.py)

const WMTS = (couche, format) =>
  "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=" + couche +
  "&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=" + format;

const PRIORITE = { "faible": "#b9c2a8", "moyenne": "#e8b13c", "forte": "#e0672b", "très forte": "#9e1b1b" };
const SEVERITE = { 1: "#c9d3a8", 2: "#fed976", 3: "#fd8d3c", 4: "#e31a1c", 5: "#67000d", 0: "#bdbdbd" };
const VIGILANCE = [[35, "#f1eef6"], [40, "#d4b9da"], [45, "#c994c7"], [50, "#df65b0"], [101, "#980043"]];

// espace insécable classique : la police Barlow ne contient pas l'espace fine du format français
const fmt = new Intl.NumberFormat("fr-FR");
const nf = { format: (x) => fmt.format(x).replace(/\u202f/g, "\u00a0") };
const pct = (x) => Math.round(x * 100) + " %";
const echapper = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const carte = new maplibregl.Map({
  container: "carte",
  style: {
    version: 8,
    glyphs: "https://orangemug.github.io/font-glyphs/glyphs/{fontstack}/{range}.pbf",
    sources: {
      plan: { type: "raster", tiles: [WMTS("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", "image/png")], tileSize: 256,
              maxzoom: 19, attribution: "© IGN Géoplateforme" },
      ortho: { type: "raster", tiles: [WMTS("ORTHOIMAGERY.ORTHOPHOTOS", "image/jpeg")], tileSize: 256,
               maxzoom: 19, attribution: "© IGN Géoplateforme" },
    },
    layers: [
      { id: "plan", type: "raster", source: "plan", paint: { "raster-saturation": -0.7, "raster-opacity": 0.85 } },
      { id: "ortho", type: "raster", source: "ortho", layout: { visibility: "none" } },
    ],
  },
  center: [-0.6, 44.51],
  zoom: 9.8,
  attributionControl: { compact: true },
});
carte.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
carte.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
// la grille CSS peut finir sa mise en page après la création de la carte : tant que
// l'utilisateur n'a pas bougé la carte, on recadre sur l'incendie à chaque redimensionnement
let cadrageInitial = null;
let interaction = false;
carte.on("movestart", (e) => { if (e.originalEvent) interaction = true; });
new ResizeObserver(() => {
  carte.resize();
  if (cadrageInitial && !interaction) carte.fitBounds(cadrageInitial, { padding: 30, animate: false });
}).observe(document.getElementById("carte"));

const COUCHES = {
  incendie: ["severite", "incendie-contour"],
  old: ["old-points", "communes-contour", "incendie-contour-leger"],
  carreaux: ["carreaux-fond", "carreaux-contour", "carreaux-etiquette", "pistes", "points-eau", "incendie-contour-leger"],
  methode: ["severite", "incendie-contour", "pistes", "points-eau"],
};

const LEGENDES = {
  incendie: [["Forte", SEVERITE[5]], ["Modérée-forte", SEVERITE[4]], ["Modérée-faible", SEVERITE[3]],
             ["Faible", SEVERITE[2]], ["Non brûlé", SEVERITE[1]]],
  old: Object.entries(PRIORITE).map(([k, c]) => ["Priorité " + k, c, "rond"]).reverse(),
  carreaux: [["Indice ≥ 50", "#980043"], ["45–50", "#df65b0"], ["40–45", "#c994c7"], ["< 40", "#d4b9da"],
             ["Piste DFCI", "#8a4b20", "trait"], ["Point d'eau", "#1c6fb8", "rond"]],
  methode: [["Sévérité (dNBR)", SEVERITE[4]], ["Piste DFCI", "#8a4b20", "trait"], ["Point d'eau", "#1c6fb8", "rond"]],
};

let synthese;
let communesGeo;
let carreauxGeo;
let ongletCourant = "incendie";

// onglets et fonds : actifs dès l'ouverture, avant la fin du chargement des données
document.querySelectorAll(".onglets button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".onglets button").forEach((x) => x.setAttribute("aria-selected", x === b));
  document.querySelectorAll(".contenu").forEach((s) => { s.hidden = s.id !== "onglet-" + b.dataset.onglet; });
  ongletCourant = b.dataset.onglet;
  afficher(ongletCourant);
}));

document.querySelectorAll(".fonds button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".fonds button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  carte.setLayoutProperty("plan", "visibility", b.dataset.fond === "plan" ? "visible" : "none");
  carte.setLayoutProperty("ortho", "visibility", b.dataset.fond === "ortho" ? "visible" : "none");
}));

async function charger(nom) {
  const r = await fetch("data/" + nom);
  if (!r.ok) throw new Error(nom + " : " + r.status);
  return r.json();
}

// téléchargement des données en parallèle du chargement du fond
const donnees = Promise.all([
  charger("synthese.json"), charger("incendie.geojson"), charger("communes.geojson"),
  charger("carreaux.geojson"), charger("old_points.geojson"), charger("pistes.geojson"),
  charger("points_eau.geojson"),
]);

carte.once("style.load", async () => {
  const [syn, incendie, communes, carreaux, old, pistes, eau] = await donnees;
  synthese = syn;
  communesGeo = communes;
  carreauxGeo = carreaux;

  carte.addSource("severite", { type: "image", url: "data/severite.png", coordinates: syn.severite_coins });
  carte.addSource("incendie", { type: "geojson", data: incendie });
  carte.addSource("communes", { type: "geojson", data: communes });
  carte.addSource("carreaux", { type: "geojson", data: carreaux });
  carte.addSource("old", { type: "geojson", data: old });
  carte.addSource("pistes", { type: "geojson", data: pistes });
  carte.addSource("eau", { type: "geojson", data: eau });

  const vigilance = ["step", ["coalesce", ["get", "indice_vigilance"], 0], "#f1eef6"];
  VIGILANCE.slice(0, -1).forEach(([seuil], i) => vigilance.push(seuil, VIGILANCE[i + 1][1]));

  carte.addLayer({ id: "severite", type: "raster", source: "severite", paint: { "raster-opacity": 0.9, "raster-resampling": "nearest" } });
  carte.addLayer({ id: "carreaux-fond", type: "fill", source: "carreaux",
    filter: ["has", "indice_vigilance"], paint: { "fill-color": vigilance, "fill-opacity": 0.72 } });
  carte.addLayer({ id: "carreaux-contour", type: "line", source: "carreaux", paint: { "line-color": "#ffffff", "line-width": 0.6 } });
  carte.addLayer({ id: "carreaux-etiquette", type: "symbol", source: "carreaux", minzoom: 11,
    layout: { "text-field": ["get", "code"], "text-size": 11, "text-font": ["Open Sans Regular"] },
    paint: { "text-color": "#2b2b2b", "text-halo-color": "#ffffff", "text-halo-width": 1 } });
  carte.addLayer({ id: "communes-contour", type: "line", source: "communes",
    paint: { "line-color": "#39433d", "line-width": 1, "line-dasharray": [3, 2] } });
  carte.addLayer({ id: "pistes", type: "line", source: "pistes",
    paint: { "line-color": "#8a4b20", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 14, 2.4] } });
  carte.addLayer({ id: "incendie-contour", type: "line", source: "incendie", paint: { "line-color": "#111111", "line-width": 1.6 } });
  carte.addLayer({ id: "incendie-contour-leger", type: "line", source: "incendie",
    paint: { "line-color": "#111111", "line-width": 1.2, "line-dasharray": [2, 1.5], "line-opacity": 0.7 } });
  carte.addLayer({ id: "points-eau", type: "circle", source: "eau",
    paint: { "circle-color": "#1c6fb8", "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 14, 6],
             "circle-stroke-color": "#ffffff", "circle-stroke-width": 1 } });
  carte.addLayer({ id: "old-points", type: "circle", source: "old",
    paint: {
      "circle-color": ["match", ["get", "priorite"], ...Object.entries(PRIORITE).flat(), "#999999"],
      // rayon proche des 50 m réels aux grands zooms (0,43 m/px au zoom 18 à cette latitude)
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 9, 1.4, 12, 3, 15, 16, 18, 128],
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0.9, 15, 0.45],
      "circle-stroke-color": ["match", ["get", "priorite"], ...Object.entries(PRIORITE).flat(), "#999999"],
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 12, 0, 15, 1],
    } });

  remplirPanneau();
  afficher(ongletCourant);
  brancherInteractions();
  cadrageInitial = emprise(incendie.features);
  carte.resize();
  carte.fitBounds(cadrageInitial, { padding: 30, animate: false });
});

function remplirPanneau() {
  const s = synthese;
  document.getElementById("k-feu").textContent = nf.format(s.incendie.surface_ha);
  document.getElementById("k-avant").textContent = new Date(s.images.avant).toLocaleDateString("fr-FR");
  document.getElementById("k-apres").textContent = new Date(s.images.apres).toLocaleDateString("fr-FR");
  const classes = s.incendie.classes.filter((c) => c.code > 0).sort((a, b) => b.code - a.code);
  const max = Math.max(...classes.map((c) => c.surface_ha));
  document.getElementById("barres-severite").innerHTML = classes.map((c) => `
    <div class="barre"><span>${echapper(c.classe.replace("Sévérité ", "").replace(/^./, (m) => m.toUpperCase()))}</span>
      <div class="piste"><div class="rempli" style="width:${(100 * c.surface_ha / max).toFixed(1)}%;background:${SEVERITE[c.code]}"></div></div>
      <span class="val">${nf.format(c.surface_ha)} ha</span></div>`).join("");

  document.getElementById("k-obl").textContent = nf.format(s.old.obligations);
  document.getElementById("k-old-ha").textContent = nf.format(s.old.surface_nette_ha);
  document.getElementById("k-voisins").textContent = nf.format(s.old.chez_voisins_non_batis_ha);
  document.getElementById("k-lisiere").textContent = nf.format(s.old.en_lisiere);
  const tbody = document.querySelector("#table-communes tbody");
  tbody.innerHTML = s.old.par_commune.map((c) => `
    <tr tabindex="0" data-commune="${echapper(c.commune)}"><td>${echapper(c.commune)}</td><td>${nf.format(c.obligations)}</td>
      <td>${nf.format(Math.round(c.surface_nette_ha))}</td><td>${nf.format(c.priorite_forte_ou_plus)}</td></tr>`).join("");

  const k = s.carreaux;
  document.getElementById("k-massif").textContent = nf.format(k.massif_ha);
  document.getElementById("k-pistes").textContent = nf.format(k.pistes_dfci_km);
  document.getElementById("k-voie").textContent = (k.part_loin_voie * 100).toLocaleString("fr-FR", { maximumFractionDigits: 1 }) + " %";
  document.getElementById("k-eau").textContent = pct(k.part_loin_eau);
  document.getElementById("liste-carreaux").innerHTML = k.plus_vigilants.map((c) => `
    <li tabindex="0" data-carreau="${echapper(c.code)}"><b>${echapper(c.code)}</b>
      <span class="detail">${nf.format(c.constructions)} constructions · ${pct(c.part_resineux)} résineux</span>
      <span class="indice">${Math.round(c.indice_vigilance)}</span></li>`).join("");
}

function afficher(onglet) {
  const visibles = new Set(COUCHES[onglet]);
  const toutes = new Set(Object.values(COUCHES).flat());
  toutes.forEach((id) => carte.getLayer(id) && carte.setLayoutProperty(id, "visibility", visibles.has(id) ? "visible" : "none"));
  document.getElementById("legende").innerHTML = LEGENDES[onglet].map(([lib, c, forme]) =>
    `<span><i class="${forme || ""}" style="background:${c}"></i>${echapper(lib)}</span>`).join("");
}

function emprise(features) {
  const b = new maplibregl.LngLatBounds();
  const parcourir = (coords) => (typeof coords[0] === "number" ? b.extend(coords) : coords.forEach(parcourir));
  features.forEach((f) => parcourir(f.geometry.coordinates));
  return b;
}

function zoomSur(features, padding = 40) {
  interaction = true;
  carte.fitBounds(emprise(features), { padding, maxZoom: 14 });
}

function fiche(titre, lignes, pastille) {
  return `<div class="fiche"><h3>${echapper(titre)}</h3>${pastille || ""}<dl>${lignes
    .map(([k, v]) => `<dt>${echapper(k)}</dt><dd>${echapper(v)}</dd>`).join("")}</dl></div>`;
}

function brancherInteractions() {
  const activer = (sel, attr, action) => document.querySelector(sel).addEventListener("click", (e) => {
    const cible = e.target.closest(`[${attr}]`);
    if (cible) action(cible.getAttribute(attr));
  });
  activer("#table-communes", "data-commune", (nom) =>
    zoomSur(communesGeo.features.filter((f) => f.properties.commune === nom)));
  activer("#liste-carreaux", "data-carreau", (code) =>
    zoomSur(carreauxGeo.features.filter((f) => f.properties.code === code), 120));
  ["#table-communes", "#liste-carreaux"].forEach((sel) => document.querySelector(sel).addEventListener("keydown", (e) => {
    if (e.key === "Enter") e.target.click();
  }));

  const popup = (e, html) => new maplibregl.Popup({ maxWidth: "290px" }).setLngLat(e.lngLat).setHTML(html).addTo(carte);

  carte.on("click", "old-points", (e) => {
    const p = e.features[0].properties;
    const pastille = `<span class="pastille" style="background:${PRIORITE[p.priorite]}">Priorité ${echapper(p.priorite)} · ${String(p.score_priorite).replace(".", ",")}/10</span>`;
    popup(e, fiche("Parcelle " + p.id_obligation, [
      ["Commune", p.commune], ["Constructions", p.nb_batiments],
      ["Surface OLD", nf.format(p.surface_old_m2) + " m²"],
      ["Chez voisins non bâtis", nf.format(p.surface_tiers_non_batis_m2) + " m²"],
      ["En lisière", p.en_lisiere === true || p.en_lisiere === "true" ? "oui" : "non"],
      ["Distance au massif", Math.round(p.dist_massif_m) + " m"],
      ["Résineux dans 200 m", pct(p.part_resineux)],
      ["Point d'eau recensé", p.dist_point_eau_m != null ? nf.format(p.dist_point_eau_m) + " m" : "–"],
    ], pastille));
  });

  carte.on("click", "carreaux-fond", (e) => {
    const p = e.features[0].properties;
    popup(e, fiche("Carreau DFCI " + p.code, [
      ["Indice de vigilance", p.indice_vigilance], ["Massif", nf.format(p.massif_ha) + " ha"],
      ["Pistes DFCI recensées", p.pistes_km + " km"], ["Points d'eau recensés", p.points_eau],
      ["Massif > 500 m d'une voie", pct(p.part_loin_voie)], ["Massif > 1 km d'un point d'eau", pct(p.part_loin_eau)],
      ["Résineux", pct(p.part_resineux)], ["Constructions", nf.format(p.constructions)],
      ["Brûlé en 2022", nf.format(p.brule_2022_ha) + " ha"],
    ]));
  });

  carte.on("click", "pistes", (e) => {
    const p = e.features[0].properties;
    popup(e, fiche("Piste DFCI", [["Gabarit", p.gabarit || "–"], ["Revêtement", p.revetement || "–"],
      ["Débroussaillée", p.debroussaillee === true || p.debroussaillee === "true" ? "oui" : "non renseigné"],
      ["Longueur", nf.format(Math.round(p.longueur_m)) + " m"]]));
  });

  ["old-points", "carreaux-fond", "pistes"].forEach((id) => {
    carte.on("mouseenter", id, () => { carte.getCanvas().style.cursor = "pointer"; });
    carte.on("mouseleave", id, () => { carte.getCanvas().style.cursor = ""; });
  });
}
