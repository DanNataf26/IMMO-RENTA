"""
Télécharge le fichier officiel "Statistiques DVF" (data.gouv.fr / Etalab)
et le fichier LOVAC (logements vacants, data.gouv.fr / Cerema), et régénère
prix-communes.json (prix moyen/médian, et taux de vacance locative, par commune).

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


print("Téléchargement du fichier source DVF...")
urllib.request.urlretrieve(SOURCE_URL, TMP_CSV)
print("Téléchargé.")

result = {}
with open(TMP_CSV, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row["echelle_geo"] != "commune":
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
            result[code] = entry

print("Téléchargement du fichier source LOVAC (vacance locative)...")
urllib.request.urlretrieve(LOVAC_URL, TMP_LOVAC)
print("Téléchargé.")

nb_lignes_lues = 0
nb_vacance_ajoutees = 0
with open(TMP_LOVAC, encoding="latin-1") as f:
    reader = csv.DictReader(f, delimiter=";")
    print("DIAGNOSTIC - Colonnes LOVAC détectées:", reader.fieldnames)
    for row in reader:
        nb_lignes_lues += 1
        code = row.get("CODGEO_26")
        vacants = to_int_lovac(row.get("pp_vacant_25"))
        total = to_int_lovac(row.get("ff_pp_total_25"))
        if nb_lignes_lues <= 3:
            print(f"DIAGNOSTIC - ligne {nb_lignes_lues}: code={code!r} vacants={vacants!r} total={total!r}")
        if vacants is None or not total:
            continue
        taux = round(vacants / total * 100, 1)
        if code not in result:
            result[code] = {"nom": row["LIBGEO_26"]}
        result[code]["vacance"] = {"taux": taux, "millesime": 2025}
        nb_vacance_ajoutees += 1

print(f"DIAGNOSTIC - {nb_lignes_lues} lignes LOVAC lues, {nb_vacance_ajoutees} taux de vacance ajoutés.")

with open(DEST_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

print(f"{len(result)} communes exportées dans {DEST_JSON}")
