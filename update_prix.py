"""
Télécharge le fichier officiel "Statistiques DVF" (data.gouv.fr / Etalab)
et regénère prix-communes.json (prix moyen/médian par commune).

Ce script est destiné à être exécuté automatiquement par GitHub Actions
(voir .github/workflows/update-prix.yml), mais peut aussi être lancé
manuellement : python update_prix.py
"""

import csv
import json
import urllib.request

SOURCE_URL = "https://www.data.gouv.fr/api/1/datasets/r/851d342f-9c96-41c1-924a-11a7a7aae8a6"
TMP_CSV = "statistiques_dvf.csv"
DEST_JSON = "prix-communes.json"


def to_int(v):
    return int(v) if v not in (None, "", "0") else None


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

with open(DEST_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

print(f"{len(result)} communes exportées dans {DEST_JSON}")
