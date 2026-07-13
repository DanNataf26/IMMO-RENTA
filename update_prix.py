"""
Télécharge :
- le fichier officiel "Statistiques DVF" (data.gouv.fr / Etalab) : prix par
  commune et par département
- le fichier LOVAC (logements vacants, data.gouv.fr / Cerema) : vacance par
  commune et par département
- les 4 fichiers "Carte des loyers 2025" (data.gouv.fr / ANIL), un par
  typologie (toutes tailles, T1-T2, T3+, maison) : loyer par commune

... et régénère prix-communes.json avec toutes ces données, par commune ET
par département (moyenne pondérée par nombre d'observations pour les loyers).

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
DEST_JSON = "prix-communes.json"

# Un fichier par typologie de loyer (identifiés et vérifiés manuellement sur
# une commune connue - voir historique du projet)
LOYERS_FICHIERS = {
    "toutes": "https://www.data.gouv.fr/api/1/datasets/r/55b34088-0964-415f-9df7-d87dd98a09be",
    "t1t2": "https://www.data.gouv.fr/api/1/datasets/r/14a1fe11-b2d1-49b3-9f6b-83d12df9482c",
    "t3plus": "https://www.data.gouv.fr/api/1/datasets/r/5e3b28a4-cf56-43a3-ae79-43cceeb27f8c",
    "maison": "https://www.data.gouv.fr/api/1/datasets/r/129f764d-b613-44e4-952c-5ff50a8c9b73",
}


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


def to_float_fr(v):
    """Nombre au format français (virgule décimale) -> float, ou None."""
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v.replace(",", "."))
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
# 3) Carte des loyers 2025, PAR COMMUNE, une fois par typologie
#    (toutes tailles / T1-T2 / T3+ / maison). Agrégation par département en
#    moyenne pondérée par nombre d'observations.
# ---------------------------------------------------------------------------
dept_loyer_pondere = {t: {} for t in LOYERS_FICHIERS}
dept_loyer_poids = {t: {} for t in LOYERS_FICHIERS}

for typologie, url in LOYERS_FICHIERS.items():
    print(f"Téléchargement Carte des loyers - {typologie}...")
    tmp = f"loyers_{typologie}.csv"
    urllib.request.urlretrieve(url, tmp)
    print("Téléchargé.")

    with open(tmp, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            code = row.get("INSEE_C")
            loyer = to_float_fr(row.get("loypredm2"))
            nb = to_int(row.get("nbobs_com")) or 0
            r2 = to_float_fr(row.get("R2_adj"))
            if not code or loyer is None:
                continue

            if code not in result:
                result[code] = {"nom": row.get("LIBGEO", "")}
            result[code].setdefault("loyer", {})
            result[code]["loyer"][typologie] = {
                "valeur": round(loyer, 2),
                "nb": nb,
                "r2": round(r2, 3) if r2 is not None else None,
            }

            dept = code_to_dept(code)
            if dept:
                poids = nb if nb > 0 else 1
                dept_loyer_pondere[typologie][dept] = (
                    dept_loyer_pondere[typologie].get(dept, 0) + loyer * poids
                )
                dept_loyer_poids[typologie][dept] = (
                    dept_loyer_poids[typologie].get(dept, 0) + poids
                )

for typologie in LOYERS_FICHIERS:
    for dept, poids in dept_loyer_poids[typologie].items():
        if poids > 0 and dept in departements:
            departements[dept].setdefault("loyer", {})
            departements[dept]["loyer"][typologie] = round(
                dept_loyer_pondere[typologie][dept] / poids, 2
            )

print("Loyers par typologie intégrés (commune + département).")

# ---------------------------------------------------------------------------
result["departements"] = departements

with open(DEST_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

print(f"{len(result) - 1} communes + {len(departements)} départements exportés dans {DEST_JSON}")
