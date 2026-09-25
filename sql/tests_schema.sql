-- Tests du modèle DFCI : à exécuter après schema_dfci_postgis.sql sur une base vide.
-- Chaque bloc lève une exception si le comportement attendu n'est pas observé.
-- psql -v ON_ERROR_STOP=1 -d <base> -f sql/tests_schema.sql

BEGIN;

INSERT INTO dfci.carreau_dfci (code, geom)
VALUES ('DE64L6', ST_GeomFromText('POLYGON((420000 6380000, 422000 6380000, 422000 6382000, 420000 6382000, 420000 6380000))', 2154));

-- 1. le code du carreau de 20 km est dérivé automatiquement
DO $$ BEGIN
    IF (SELECT code_20km FROM dfci.carreau_dfci WHERE code = 'DE64L6') <> 'DE64' THEN
        RAISE EXCEPTION 'code_20km non dérivé';
    END IF;
END $$;

-- 2. un point d'eau reçoit son carreau DFCI et sa traçabilité par déclencheur
INSERT INTO dfci.point_eau (type, capacite_m3, geom)
VALUES ('CIT', 120, ST_SetSRID(ST_MakePoint(421000, 6381000), 2154));
DO $$ BEGIN
    IF (SELECT carreau_dfci FROM dfci.point_eau LIMIT 1) IS DISTINCT FROM 'DE64L6' THEN
        RAISE EXCEPTION 'carreau DFCI non renseigné';
    END IF;
    IF (SELECT date_creation IS NULL OR auteur_creation IS NULL FROM dfci.point_eau LIMIT 1) THEN
        RAISE EXCEPTION 'traçabilité absente';
    END IF;
END $$;

-- 3. une valeur hors liste est refusée (clé étrangère vers ref.type_point_eau)
DO $$ BEGIN
    BEGIN
        INSERT INTO dfci.point_eau (type, geom) VALUES ('XXX', ST_SetSRID(ST_MakePoint(421000, 6381000), 2154));
        RAISE EXCEPTION 'type de point d''eau invalide accepté';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
END $$;

-- 4. un contrôle met à jour le statut de l'obligation ; seul le plus récent compte
INSERT INTO dfci.old_obligation (id_batiment, commune_insee, geom)
VALUES ('BATIMENT0000000000000001', '33225',
        ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(421000, 6381000), 2154), 50)));
INSERT INTO dfci.controle_old (id_obligation, date_controle, agent, statut, suite, geom)
SELECT id, '2026-06-01', 'agent.test', 'NCONF', 'MED', ST_SetSRID(ST_MakePoint(421010, 6381010), 2154)
FROM dfci.old_obligation;
INSERT INTO dfci.controle_old (id_obligation, date_controle, agent, statut, geom)
SELECT id, '2026-05-01', 'agent.test', 'CONF', ST_SetSRID(ST_MakePoint(421010, 6381010), 2154)
FROM dfci.old_obligation;
DO $$ BEGIN
    IF (SELECT statut FROM dfci.old_obligation LIMIT 1) <> 'NCONF' THEN
        RAISE EXCEPTION 'le statut devrait refléter le contrôle le plus récent (NCONF)';
    END IF;
END $$;

-- 5. une non-conformité sans suite est refusée
DO $$ BEGIN
    BEGIN
        INSERT INTO dfci.controle_old (date_controle, agent, statut, suite, geom)
        VALUES (current_date, 'agent.test', 'NCONF', 'AUC', ST_SetSRID(ST_MakePoint(421000, 6381000), 2154));
        RAISE EXCEPTION 'non-conformité sans suite acceptée';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;

-- 6. vue d'avancement par commune
DO $$ BEGIN
    IF (SELECT taux_controle_pct FROM dfci.v_avancement_old_commune WHERE commune_insee = '33225') <> 100 THEN
        RAISE EXCEPTION 'taux de contrôle attendu : 100 %%';
    END IF;
END $$;

-- 7. surface d'incendie calculée depuis la géométrie
INSERT INTO dfci.incendie (id, date_debut, geom)
VALUES ('TEST', '2022-07-12', ST_Multi(ST_GeomFromText('POLYGON((0 0, 1000 0, 1000 1000, 0 1000, 0 0))', 2154)));
DO $$ BEGIN
    IF (SELECT surface_ha FROM dfci.incendie WHERE id = 'TEST') <> 100 THEN
        RAISE EXCEPTION 'surface_ha attendue : 100';
    END IF;
END $$;

ROLLBACK;
\echo 'Tous les tests du modèle DFCI sont passés.'
