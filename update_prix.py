"""
Télécharge les fichiers officiels "Statistiques DVF" (data.gouv.fr / Etalab),
LOVAC (logements vacants, data.gouv.fr / Cerema) et interroge la "Carte des
loyers" (ANIL, via ArcGIS), pour régénérer prix-communes.json :
- prix moyen/médian et taux de vacance locative, par commune ET par département
- loyer moyen par département (moyenne pondérée des communes, calculée à la volée
  car la Carte des loyers n'est disponible que commune par commune)

Ce script est destiné à être exécuté automatiquement par GitHub Actions
(voir .github/workflows/update-prix.yml), mais peut aussi être lancé
manuellement : python update_prix.py
"""

import csv
import json
import urllib.request

SOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/851d342f-9c96-41c1-924a-11a7a7aae8a6"
TMP_CSV = "statistiques_dvf.csv"
LOVAC_URL = "https://www.data.gouv.fr/api/1/datasets/r/2e0417b4-902d-4c60-90e7-bf5df148cb87"
TMP_LOVAC = "lovac.csv"
LOYERS_BASE = "https://services.arcgis.com/d3voDfTFbHOCRwVR/ArcGIS/rest/services/Carte_des_loyers__Jan_2024_/FeatureServer"
DEST_JSON = "prix-communes.json"


def to_int(v):
    return int(v) if v not in (None, "", "0") else None


def to_int_lovac(v):
    v = (v or "").strip()
    if v in ("", "s"):
        return None
    try:
        return int(v)
    except ValueError:
        return None


def code_to_dept(code):
    """Déduit le code département à partir d'un code commune INSEE."""
    if not code:
        return None
    if code.startswith("97") or code.startswith("98"):
        return code[:3]
    return code[:2]


result = {}
departements = {}

# ---------------------------------------------------------------------------
# 1) DVF : prix par commune ET par département (déjà agrégé dans le fichier)
# ---------------------------------------------------------------------------
print("Téléchargement du fichier source DVF...")
urllib.request.urlretrieve(SOURCE_URL, TMP_CSV)
print("Téléchargé.")

with open(TMP_CSV, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        echelle = row["echelle_geo"]
        if echelle not in ("commune", "departement"):
            continue

        code = row["code_geo"]
        entry = {"nom": row["libelle_geo"]}

        nb_appt = to_int(row["nb_ventes_whole_appartement"])
        if nb_appt:
            entry["appartement"] = {
                "nb": nb_appt,
                "moyenne": to_int(row["moy_prix_m2_whole_appartement"]),
                "mediane": to_int(row["med_prix_m2_whole_appartement"]),
            }

        nb_maison = to_int(row["nb_ventes_whole_maison"])
        if nb_maison:
            entry["maison"] = {
                "nb": nb_maison,
                "moyenne": to_int(row["moy_prix_m2_whole_maison"]),
                "mediane": to_int(row["med_prix_m2_whole_maison"]),
            }

        nb_local = to_int(row["nb_ventes_whole_local"])
        if nb_local:
            entry["local"] = {
                "nb": nb_local,
                "moyenne": to_int(row["moy_prix_m2_whole_local"]),
                "mediane": to_int(row["med_prix_m2_whole_local"]),
            }

        if "appartement" in entry or "maison" in entry or "local" in entry:
            if echelle == "commune":
                result[code] = entry
            else:
                departements[code] = entry

print(f"{len(result)} communes et {len(departements)} départements (prix DVF) traités.")

# ---------------------------------------------------------------------------
# 2) LOVAC : vacance par commune, agrégée aussi par département (somme des
#    numérateurs/dénominateurs, plus correct statistiquement qu'une moyenne
#    de pourcentages)
# ---------------------------------------------------------------------------
print("Téléchargement du fichier source LOVAC (vacance locative)...")
urllib.request.urlretrieve(LOVAC_URL, TMP_LOVAC)
print("Téléchargé.")

dept_vacants = {}
dept_total = {}

with open(TMP_LOVAC, encoding="latin-1") as f:
    reader = csv.DictReader(f, delimiter=";")
    for row in reader:
        code = row.get("CODGEO_26")
        vacants = to_int_lovac(row.get("pp_vacant_25"))
        total = to_int_lovac(row.get("ff_pp_total_25"))
        if vacants is None or not total:
            continue

        taux = round(vacants / total * 100, 1)
        if code not in result:
            result[code] = {"nom": row["LIBGEO_26"]}
        result[code]["vacance"] = {"taux": taux, "millesime": 2025}

        dept = code_to_dept(code)
        if dept:
            dept_vacants[dept] = dept_vacants.get(dept, 0) + vacants
            dept_total[dept] = dept_total.get(dept, 0) + total

for dept, total in dept_total.items():
    if total > 0 and dept in departements:
        taux = round(dept_vacants[dept] / total * 100, 1)
        departements[dept]["vacance"] = {"taux": taux, "millesime": 2025}

print(f"Vacance agrégée pour {len(dept_total)} départements.")

# ---------------------------------------------------------------------------
# 3) Carte des loyers : disponible uniquement par commune -> on interroge
#    toutes les communes (pagination) et on calcule une moyenne pondérée
#    (par nombre d'observations) par département.
# ---------------------------------------------------------------------------
def fetch_loyers_layer(layer_id):
    """Récupère {code_insee: (loypredm2, nbobs_com)} pour une couche (0=appt, 1=maison)."""
    donnees = {}
    offset = 0
    page_size = 2000
    while True:
        url = (
            f"{LOYERS_BASE}/{layer_id}/query?where=1%3D1"
            f"&outFields=INSEE_COM,loypredm2,nbobs_com&returnGeometry=false&f=json"
            f"&resultRecordCount={page_size}&resultOffset={offset}"
        )
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        feats = data.get("features", [])
        for feat in feats:
            attrs = feat.get("attributes", {})
            code = attrs.get("INSEE_COM")
            loyer = attrs.get("loypredm2")
            nb = attrs.get("nbobs_com") or 0
            if code and loyer:
                donnees[code] = (loyer, nb)
        offset += page_size
        if not data.get("exceededTransferLimit"):
            break
    return donnees


print("Interrogation de la Carte des loyers (appartements)...")
loyers_appt = fetch_loyers_layer(0)
print(f"{len(loyers_appt)} communes (appartements).")

print("Interrogation de la Carte des loyers (maisons)...")
loyers_maison = fetch_loyers_layer(1)
print(f"{len(loyers_maison)} communes (maisons).")


def agreger_loyers_par_dept(donnees):
    """Moyenne pondérée (par nb d'observations) du loyer, par département."""
    somme_pondere = {}
    somme_poids = {}
    for code, (loyer, nb) in donnees.items():
        dept = code_to_dept(code)
        if not dept:
            continue
        poids = nb if nb > 0 else 1
        somme_pondere[dept] = somme_pondere.get(dept, 0) + loyer * poids
        somme_poids[dept] = somme_poids.get(dept, 0) + poids
    return {
        dept: round(somme_pondere[dept] / somme_poids[dept], 2)
        for dept in somme_pondere
        if somme_poids[dept] > 0
    }


loyer_dept_appt = agreger_loyers_par_dept(loyers_appt)
loyer_dept_maison = agreger_loyers_par_dept(loyers_maison)

for dept, entry in departements.items():
    if dept in loyer_dept_appt:
        entry["loyer_appartement"] = loyer_dept_appt[dept]
    if dept in loyer_dept_maison:
        entry["loyer_maison"] = loyer_dept_maison[dept]

print(f"Loyer moyen calculé pour {len(loyer_dept_appt)} départements (appartements).")

# ---------------------------------------------------------------------------
result["departements"] = departements

with open(DEST_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

print(f"{len(result) - 1} communes + {len(departements)} départements exportés dans {DEST_JSON}")
