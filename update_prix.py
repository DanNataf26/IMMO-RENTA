"""
Télécharge :
- le fichier officiel "Statistiques DVF" (data.gouv.fr / Etalab) : prix par
  commune et par département
- le fichier LOVAC (logements vacants, data.gouv.fr / Cerema) : vacance par
  commune et par département
- les 4 fichiers "Carte des loyers" (data.gouv.fr / ANIL), un par typologie
  (toutes tailles, T1-T2, T3+, maison) : loyer par commune. L'édition la plus
  récente est recherchée automatiquement chaque année (le jeu de données
  change d'adresse à chaque nouvelle édition annuelle) ; à défaut, on retombe
  sur l'édition 2025 dont les URLs sont connues et fiables.

... et régénère prix-communes.json avec toutes ces données, par commune ET
par département (moyenne pondérée par nombre d'observations pour les loyers).

Ce script est destiné à être exécuté automatiquement par GitHub Actions
(voir .github/workflows/update-prix.yml), mais peut aussi être lancé
manuellement : python update_prix.py
"""

import csv
import datetime
import json
import urllib.request

SOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/851d342f-9c96-41c1-924a-11a7a7aae8a6"
TMP_CSV = "statistiques_dvf.csv"
LOVAC_URL = "https://www.data.gouv.fr/api/1/datasets/r/2e0417b4-902d-4c60-90e7-bf5df148cb87"
TMP_LOVAC = "lovac.csv"
DEST_JSON = "prix-communes.json"

# Repli garanti : URLs de l'édition 2025, vérifiées manuellement (voir
# historique du projet - typologies confirmées sur Bourg-en-Bresse, 01053).
LOYERS_FICHIERS_2025 = {
    "toutes": "https://www.data.gouv.fr/api/1/datasets/r/55b34088-0964-415f-9df7-d87dd98a09be",
    "t1t2": "https://www.data.gouv.fr/api/1/datasets/r/14a1fe11-b2d1-49b3-9f6b-83d12df9482c",
    "t3plus": "https://www.data.gouv.fr/api/1/datasets/r/5e3b28a4-cf56-43a3-ae79-43cceeb27f8c",
    "maison": "https://www.data.gouv.fr/api/1/datasets/r/129f764d-b613-44e4-952c-5ff50a8c9b73",
}
COMMUNE_REFERENCE = "01053"  # Bourg-en-Bresse : sert à identifier les typologies automatiquement


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


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "immo-renta-simulateur/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def lire_commune_reference(url):
    """Télécharge un fichier de loyer et renvoie (loyer, nbobs_com) pour la
    commune de référence, ou None si absent/illisible."""
    tmp = "_tmp_ref.csv"
    urllib.request.urlretrieve(url, tmp)
    with open(tmp, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row.get("INSEE_C") == COMMUNE_REFERENCE:
                loyer = to_float_fr(row.get("loypredm2"))
                nb = to_int(row.get("nbobs_com")) or 0
                if loyer is not None:
                    return loyer, nb
    return None


def identifier_typologies(urls):
    """À partir de 4 URLs de fichiers "Carte des loyers", détermine
    automatiquement laquelle correspond à quelle typologie, en se basant sur
    la commune de référence :
    - le fichier avec le MOINS d'observations = maison
    - parmi les 3 restants (appartements), celui avec le PLUS d'observations
      = toutes tailles confondues (jeu de données le plus large)
    - parmi les 2 restants, le loyer le plus élevé = T1-T2, le plus bas = T3+
    Renvoie {typologie: url} ou None si l'identification échoue.
    """
    mesures = []
    for url in urls:
        r = lire_commune_reference(url)
        if r is None:
            return None
        loyer, nb = r
        mesures.append({"url": url, "loyer": loyer, "nb": nb})

    if len(mesures) != 4:
        return None

    mesures.sort(key=lambda m: m["nb"])
    maison = mesures[0]
    reste = mesures[1:]
    reste.sort(key=lambda m: m["nb"], reverse=True)
    toutes = reste[0]
    appts = reste[1:]
    appts.sort(key=lambda m: m["loyer"], reverse=True)
    t1t2, t3plus = appts[0], appts[1]

    return {
        "toutes": toutes["url"],
        "t1t2": t1t2["url"],
        "t3plus": t3plus["url"],
        "maison": maison["url"],
    }


def trouver_edition_loyers():
    """Cherche la dernière édition "Carte des loyers" disponible sur
    data.gouv.fr (nouvelle adresse à chaque nouvelle édition annuelle), et
    identifie automatiquement les 4 fichiers par typologie. Retombe sur
    l'édition 2025 (connue et fiable) si rien de plus récent n'est trouvé ou
    reconnu.
    """
    annee_courante = datetime.date.today().year
    for annee in range(annee_courante + 1, 2024, -1):
        slug = f"carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-{annee}"
        try:
            data = get_json(f"https://www.data.gouv.fr/api/1/datasets/{slug}/")
        except Exception:
            continue

        resources = [
            r for r in data.get("resources", [])
            if (r.get("format") or "").lower() == "csv"
            and (r.get("filesize") or 0) > 1_000_000  # écarte d'éventuels petits fichiers annexes
        ]
        if len(resources) != 4:
            continue

        urls = [r["url"] for r in resources]
        typologies = identifier_typologies(urls)
        if typologies:
            print(f"Édition {annee} de la Carte des loyers trouvée et identifiée automatiquement.")
            return typologies, annee

    print("Aucune édition plus récente trouvée/reconnue : repli sur l'édition 2025 (connue).")
    return LOYERS_FICHIERS_2025, 2025


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
# 3) Carte des loyers, PAR COMMUNE, une fois par typologie
#    (toutes tailles / T1-T2 / T3+ / maison). Édition la plus récente trouvée
#    automatiquement. Agrégation par département en moyenne pondérée par
#    nombre d'observations.
# ---------------------------------------------------------------------------
LOYERS_FICHIERS, millesime_loyers = trouver_edition_loyers()

dept_loyer_pondere = {t: {} for t in LOYERS_FICHIERS}
dept_loyer_poids = {t: {} for t in LOYERS_FICHIERS}

for typologie, url in LOYERS_FICHIERS.items():
    print(f"Téléchargement Carte des loyers {millesime_loyers} - {typologie}...")
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

print(f"Loyers {millesime_loyers} par typologie intégrés (commune + département).")

# ---------------------------------------------------------------------------
result["departements"] = departements
result["_millesime_loyers"] = millesime_loyers

with open(DEST_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

print(f"{len(result) - 2} communes + {len(departements)} départements exportés dans {DEST_JSON}")
