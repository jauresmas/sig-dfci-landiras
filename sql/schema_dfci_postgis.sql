-- =============================================================================
--  Modèle de données DFCI (Défense des Forêts Contre l'Incendie) — PostgreSQL / PostGIS
--  Projet : sig-dfci-landiras
--
--  Thématiques : OLD (zonage, obligations, contrôles), équipements (pistes, points d'eau,
--  vigies), incendies, patrouilles, points d'intérêt, carroyage DFCI.
--
--  Principes :
--  - un schéma "dfci" pour les données métier, un schéma "ref" pour les listes de valeurs ;
--  - listes de valeurs en tables (clés étrangères) plutôt qu'en ENUM : modifiables sans migration
--    et lisibles directement par QGIS / QField (widget "Relation de valeur") ;
--  - Lambert-93 (EPSG:2154) partout, géométries contrôlées (type + validité) ;
--  - traçabilité : date de création / mise à jour et auteur alimentés par déclencheur.
--
--  Exécution : psql -d <base> -f sql/schema_dfci_postgis.sql
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS dfci;

-- -----------------------------------------------------------------------------
-- Listes de valeurs
-- -----------------------------------------------------------------------------
CREATE TABLE ref.type_point_eau (
    code    text PRIMARY KEY,
    libelle text NOT NULL
);
INSERT INTO ref.type_point_eau VALUES
    ('PI',  'Poteau / bouche d''incendie (réseau)'),
    ('CIT', 'Citerne / réserve artificielle'),
    ('RN',  'Réserve naturelle (mare, étang, lagune)'),
    ('FOR', 'Forage / point d''aspiration'),
    ('AUT', 'Autre');

CREATE TABLE ref.etat_equipement (
    code    text PRIMARY KEY,
    libelle text NOT NULL
);
INSERT INTO ref.etat_equipement VALUES
    ('BON', 'Bon état, opérationnel'),
    ('MOY', 'Utilisable, entretien à prévoir'),
    ('HS',  'Hors service'),
    ('NC',  'Non contrôlé');

CREATE TABLE ref.categorie_piste (
    code        text PRIMARY KEY,
    libelle     text NOT NULL,
    description text
);
-- Guide de normalisation des équipements DFCI (catégories 1 à 3)
INSERT INTO ref.categorie_piste VALUES
    ('1',  'Catégorie 1', 'Piste principale : croisement de deux engins, bande de roulement >= 4 m'),
    ('2',  'Catégorie 2', 'Piste secondaire : un engin, aires de croisement régulières'),
    ('3',  'Catégorie 3', 'Piste de desserte / tout terrain'),
    ('NC', 'Non classée', NULL);

CREATE TABLE ref.statut_old (
    code    text PRIMARY KEY,
    libelle text NOT NULL
);
INSERT INTO ref.statut_old VALUES
    ('CONF',   'Conforme'),
    ('NCONF',  'Non conforme'),
    ('PART',   'Partiellement conforme'),
    ('INACC',  'Inaccessible / absent'),
    ('AFAIRE', 'Contrôle à réaliser');

CREATE TABLE ref.suite_controle (
    code    text PRIMARY KEY,
    libelle text NOT NULL
);
INSERT INTO ref.suite_controle VALUES
    ('AUC',  'Aucune'),
    ('INFO', 'Courrier d''information'),
    ('MED',  'Mise en demeure (maire)'),
    ('CV',   'Contre-visite programmée'),
    ('PV',   'Procès-verbal');

CREATE TABLE ref.type_poi (
    code    text PRIMARY KEY,
    libelle text NOT NULL
);
INSERT INTO ref.type_poi VALUES
    ('VIG', 'Tour de guet / vigie'),
    ('RET', 'Aire de retournement'),
    ('CRO', 'Zone de croisement'),
    ('BAR', 'Barrière / accès fermé'),
    ('RDV', 'Point de rendez-vous des secours'),
    ('DZ',  'Hélisurface / DZ'),
    ('SEN', 'Site sensible (camping, ERP, industrie)');

-- -----------------------------------------------------------------------------
-- Déclencheur de traçabilité commun
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dfci.tg_tracabilite() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.date_creation := coalesce(NEW.date_creation, now());
        NEW.auteur_creation := coalesce(NEW.auteur_creation, current_user);
    END IF;
    NEW.date_maj := now();
    NEW.auteur_maj := current_user;
    RETURN NEW;
END $$;

-- -----------------------------------------------------------------------------
-- Référentiels spatiaux
-- -----------------------------------------------------------------------------
CREATE TABLE dfci.carreau_dfci (
    code      text PRIMARY KEY,                       -- ex. DE64L6 (carreau 2 km)
    code_20km text GENERATED ALWAYS AS (left(code, 4)) STORED,
    geom      geometry(Polygon, 2154) NOT NULL
);
CREATE INDEX ON dfci.carreau_dfci USING gist (geom);
COMMENT ON TABLE dfci.carreau_dfci IS 'Carroyage DFCI national 2x2 km (IGN), grille de localisation des secours';

CREATE TABLE dfci.zonage_old (
    id              bigserial PRIMARY KEY,
    type_zone       text NOT NULL CHECK (type_zone IN ('massif', 'zone_application')),
    nature          text CHECK (nature IN ('Forêt', 'Lande')),
    arrete_date     date,                                  -- date de l'arrêté préfectoral OLD
    source          text,
    geom            geometry(MultiPolygon, 2154) NOT NULL CHECK (ST_IsValid(geom)),
    CONSTRAINT chk_nature_massif CHECK (type_zone <> 'massif' OR nature IS NOT NULL)
);
CREATE INDEX ON dfci.zonage_old USING gist (geom);
COMMENT ON TABLE dfci.zonage_old IS
    'Zonage OLD (IGN) : massifs soumis (bois, forêts, landes) et zone d''application (massifs + 200 m, ajustements locaux)';

-- -----------------------------------------------------------------------------
-- Équipements DFCI
-- -----------------------------------------------------------------------------
CREATE TABLE dfci.piste (
    id               bigserial PRIMARY KEY,
    id_bdtopo        text UNIQUE,
    nom              text,
    categorie        text REFERENCES ref.categorie_piste DEFAULT 'NC',
    gabarit          text,
    largeur_m        numeric(4, 1) CHECK (largeur_m IS NULL OR largeur_m BETWEEN 0 AND 30),
    impasse          boolean DEFAULT false,
    debroussaillee   boolean,
    etat             text REFERENCES ref.etat_equipement DEFAULT 'NC',
    date_visite      date,
    commune_insee    char(5),
    date_creation    timestamptz, auteur_creation text, date_maj timestamptz, auteur_maj text,
    geom             geometry(MultiLineString, 2154) NOT NULL,
    longueur_m       numeric GENERATED ALWAYS AS (round(ST_Length(geom)::numeric, 1)) STORED
);
CREATE INDEX ON dfci.piste USING gist (geom);
CREATE TRIGGER trg_piste_trace BEFORE INSERT OR UPDATE ON dfci.piste
    FOR EACH ROW EXECUTE FUNCTION dfci.tg_tracabilite();

CREATE TABLE dfci.point_eau (
    id               bigserial PRIMARY KEY,
    id_source        text,
    type             text NOT NULL REFERENCES ref.type_point_eau,
    capacite_m3      numeric CHECK (capacite_m3 IS NULL OR capacite_m3 > 0),
    debit_m3h        numeric CHECK (debit_m3h IS NULL OR debit_m3h > 0),
    accessible_ccf   boolean,                              -- accessible aux camions-citernes feux de forêts
    etat             text REFERENCES ref.etat_equipement DEFAULT 'NC',
    date_visite      date,
    source           text,
    carreau_dfci     text REFERENCES dfci.carreau_dfci,
    date_creation    timestamptz, auteur_creation text, date_maj timestamptz, auteur_maj text,
    geom             geometry(Point, 2154) NOT NULL
);
CREATE INDEX ON dfci.point_eau USING gist (geom);
CREATE TRIGGER trg_point_eau_trace BEFORE INSERT OR UPDATE ON dfci.point_eau
    FOR EACH ROW EXECUTE FUNCTION dfci.tg_tracabilite();

-- renseigne automatiquement le carreau DFCI d'un point d'eau
CREATE OR REPLACE FUNCTION dfci.tg_carreau_auto() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    SELECT c.code INTO NEW.carreau_dfci
    FROM dfci.carreau_dfci c WHERE ST_Intersects(c.geom, NEW.geom) LIMIT 1;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_point_eau_carreau BEFORE INSERT OR UPDATE OF geom ON dfci.point_eau
    FOR EACH ROW EXECUTE FUNCTION dfci.tg_carreau_auto();

CREATE TABLE dfci.point_interet (
    id            bigserial PRIMARY KEY,
    type          text NOT NULL REFERENCES ref.type_poi,
    nom           text,
    observation   text,
    source        text,
    date_creation timestamptz, auteur_creation text, date_maj timestamptz, auteur_maj text,
    geom          geometry(Point, 2154) NOT NULL
);
CREATE INDEX ON dfci.point_interet USING gist (geom);
CREATE TRIGGER trg_poi_trace BEFORE INSERT OR UPDATE ON dfci.point_interet
    FOR EACH ROW EXECUTE FUNCTION dfci.tg_tracabilite();

-- -----------------------------------------------------------------------------
-- Incendies
-- -----------------------------------------------------------------------------
CREATE TABLE dfci.incendie (
    id              text PRIMARY KEY,                      -- ex. LANDIRAS_2022_1
    nom             text,
    code_bdiff      text,                                  -- identifiant BDIFF si connu
    date_debut      date,
    date_fin        date CHECK (date_fin IS NULL OR date_fin >= date_debut),
    surface_ha      numeric GENERATED ALWAYS AS (round((ST_Area(geom) / 10000)::numeric, 1)) STORED,
    methode         text,                                  -- relevé GPS, dNBR Sentinel-2, SDIS...
    geom            geometry(MultiPolygon, 2154) NOT NULL CHECK (ST_IsValid(geom))
);
CREATE INDEX ON dfci.incendie USING gist (geom);

-- -----------------------------------------------------------------------------
-- OLD : obligations calculées et contrôles terrain
-- -----------------------------------------------------------------------------
CREATE TABLE dfci.old_obligation (
    id               bigserial PRIMARY KEY,
    id_batiment      text NOT NULL,                        -- cleabs BD TOPO de la construction génératrice
    commune_insee    char(5),
    idu_parcelle     text,                                 -- parcelle portant la construction
    surface_old_m2   numeric,
    surface_hors_parcelle_m2 numeric,                      -- part à débroussailler chez des tiers
    score_priorite   numeric(4, 1),
    statut           text REFERENCES ref.statut_old DEFAULT 'AFAIRE',
    geom             geometry(MultiPolygon, 2154) NOT NULL
);
CREATE INDEX ON dfci.old_obligation USING gist (geom);
CREATE INDEX ON dfci.old_obligation (commune_insee, statut);

CREATE TABLE dfci.controle_old (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    id_obligation    bigint REFERENCES dfci.old_obligation ON DELETE SET NULL,
    date_controle    date NOT NULL DEFAULT current_date,
    agent            text NOT NULL,
    statut           text NOT NULL REFERENCES ref.statut_old,
    hauteur_strate_ok boolean,                             -- strate herbacée/arbustive rabattue
    elagage_ok       boolean,                              -- élagage à 2 m, houppiers à 3 m des murs
    dechets_verts_ok boolean,                              -- rémanents évacués
    observation      text,
    photo            text,                                 -- chemin relatif (pièce jointe QField)
    suite            text REFERENCES ref.suite_controle DEFAULT 'AUC',
    date_creation timestamptz, auteur_creation text, date_maj timestamptz, auteur_maj text,
    geom             geometry(Point, 2154) NOT NULL,
    CONSTRAINT chk_nconf_suite CHECK (statut <> 'NCONF' OR suite <> 'AUC')
);
CREATE INDEX ON dfci.controle_old USING gist (geom);
CREATE TRIGGER trg_controle_trace BEFORE INSERT OR UPDATE ON dfci.controle_old
    FOR EACH ROW EXECUTE FUNCTION dfci.tg_tracabilite();

-- le dernier contrôle met à jour le statut de l'obligation
CREATE OR REPLACE FUNCTION dfci.tg_maj_statut_obligation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE dfci.old_obligation o SET statut = NEW.statut
    WHERE o.id = NEW.id_obligation
      AND NOT EXISTS (SELECT 1 FROM dfci.controle_old c
                      WHERE c.id_obligation = NEW.id_obligation AND c.date_controle > NEW.date_controle);
    RETURN NEW;
END $$;
CREATE TRIGGER trg_controle_statut AFTER INSERT OR UPDATE OF statut ON dfci.controle_old
    FOR EACH ROW EXECUTE FUNCTION dfci.tg_maj_statut_obligation();

-- -----------------------------------------------------------------------------
-- Patrouilles estivales
-- -----------------------------------------------------------------------------
CREATE TABLE dfci.patrouille (
    id            bigserial PRIMARY KEY,
    date_patrouille date NOT NULL,
    equipe        text NOT NULL,
    niveau_risque text CHECK (niveau_risque IN ('faible', 'modéré', 'sévère', 'très sévère', 'exceptionnel')),
    observation   text,
    geom          geometry(MultiLineString, 2154) NOT NULL,
    longueur_km   numeric GENERATED ALWAYS AS (round((ST_Length(geom) / 1000)::numeric, 2)) STORED
);
CREATE INDEX ON dfci.patrouille USING gist (geom);

-- -----------------------------------------------------------------------------
-- Vues de pilotage
-- -----------------------------------------------------------------------------
-- Avancement des contrôles OLD par commune
CREATE OR REPLACE VIEW dfci.v_avancement_old_commune AS
SELECT commune_insee,
       count(*)                                          AS nb_obligations,
       count(*) FILTER (WHERE statut <> 'AFAIRE')        AS nb_controlees,
       count(*) FILTER (WHERE statut = 'NCONF')          AS nb_non_conformes,
       round(100.0 * count(*) FILTER (WHERE statut <> 'AFAIRE') / nullif(count(*), 0), 1) AS taux_controle_pct
FROM dfci.old_obligation
GROUP BY commune_insee;

-- Points d'eau non visités depuis plus d'un an (préparation de la saison feux)
CREATE OR REPLACE VIEW dfci.v_point_eau_a_visiter AS
SELECT * FROM dfci.point_eau
WHERE date_visite IS NULL OR date_visite < current_date - interval '1 year' OR etat IN ('HS', 'NC');

-- Pistes en impasse sans aire de retournement à moins de 50 m de leur extrémité
CREATE OR REPLACE VIEW dfci.v_impasse_sans_retournement AS
SELECT p.*
FROM dfci.piste p
WHERE p.impasse
  AND NOT EXISTS (SELECT 1 FROM dfci.point_interet r
                  WHERE r.type = 'RET' AND ST_DWithin(r.geom, ST_EndPoint(ST_GeometryN(p.geom, 1)), 50));
