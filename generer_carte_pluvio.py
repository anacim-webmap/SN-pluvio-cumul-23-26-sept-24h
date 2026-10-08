#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generer_carte_pluvio.py
========================
Automatise la production d'une carte pluviometrique interactive (HTML, zoomable,
avec info-bulles) a partir d'un fichier CSV du type "Pluvio_journalier_DDMMAA.csv"
(export ANACIM : zone;feuille;station;lon;lat;pluie_originale;pluie_mm).

UTILISATION
-----------
    python3 generer_carte_pluvio.py
    (le script demande alors le chemin du fichier CSV, puis genere le HTML)

ou directement en ligne de commande :
    python3 generer_carte_pluvio.py chemin/vers/fichier.csv [chemin/sortie.html]

Aucune dependance externe : uniquement la bibliotheque standard Python (csv, json,
re, datetime, os). Le HTML genere utilise Leaflet via CDN (necessite une connexion
internet a l'OUVERTURE du fichier dans le navigateur, pour le fond de carte et la
bibliotheque JS - les donnees, elles, sont entierement embarquees dans le fichier).
"""

import csv
import json
import os
import re
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Config : seuils / couleurs / rayons, alignes sur la legende ANACIM
# ("Pluie 24 h (mm)" : 0.1-5, 5-10, 10-25, 25-50, 50-100, > 100)
# ---------------------------------------------------------------------------
PALIERS = [
    # (borne_min_incluse, borne_max_incluse, couleur_hex, libelle, rayon_px)
    (0.1,   5,    "#cfe2f5", "0.1 - 5 mm",   5),
    (5,     10,   "#7fb3e0", "5 - 10 mm",    7),
    (10,    25,   "#1a4fa0", "10 - 25 mm",   9),
    (25,    50,   "#2e8b3d", "25 - 50 mm",   12),
    (50,    100,  "#f0902d", "50 - 100 mm",  16),
    (100,   None, "#c0392b", "> 100 mm",     20),
]

MOIS_FR = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
           "aout", "septembre", "octobre", "novembre", "decembre"]


# ---------------------------------------------------------------------------
# 1. Saisie / validation du fichier CSV
# ---------------------------------------------------------------------------
def demander_fichier_csv():
    if len(sys.argv) > 1:
        chemin = sys.argv[1].strip().strip('"')
        if os.path.isfile(chemin):
            return chemin
        print(f"Fichier introuvable : {chemin}")

    while True:
        chemin = input("Chemin du fichier CSV de pluviometrie a cartographier : ").strip().strip('"')
        if os.path.isfile(chemin):
            return chemin
        print("  -> Fichier introuvable, reessayez (ou Ctrl+C pour quitter).")


def demander_fichier_sortie(chemin_csv):
    if len(sys.argv) > 2:
        return sys.argv[2].strip().strip('"')
    base = os.path.splitext(os.path.basename(chemin_csv))[0]
    defaut = f"{base}_carte.html"
    reponse = input(f"Nom du fichier HTML a generer [{defaut}] : ").strip()
    return reponse if reponse else defaut


# ---------------------------------------------------------------------------
# 2. Lecture et normalisation du CSV
#    (gere le format francais : separateur ';' et decimales a virgule)
# ---------------------------------------------------------------------------
def to_float(valeur):
    if valeur is None:
        return None
    valeur = valeur.strip().replace(",", ".")
    if valeur == "" or valeur.upper() in ("NA", "N/A", "-"):
        return None
    try:
        return float(valeur)
    except ValueError:
        return None


def lire_csv(chemin):
    with open(chemin, encoding="utf-8-sig") as f:
        # Detection automatique du separateur (';' par defaut, ',' en secours)
        premiere_ligne = f.readline()
        f.seek(0)
        sep = ";" if premiere_ligne.count(";") >= premiere_ligne.count(",") else ","
        lecteur = csv.DictReader(f, delimiter=sep)
        colonnes = {c.lower().strip(): c for c in lecteur.fieldnames or []}

        def col(nom, *alternatives):
            for candidat in (nom,) + alternatives:
                if candidat in colonnes:
                    return colonnes[candidat]
            return None

        c_zone = col("zone")
        c_feuille = col("feuille", "region")
        c_station = col("station", "nom", "localite", "poste")
        c_lon = col("lon", "longitude", "x")
        c_lat = col("lat", "latitude", "y")
        c_pluie = col("pluie_mm", "pluie", "valeur", "mm")
        c_pluie_orig = col("pluie_originale", "pluie_brute")

        if not (c_station and c_lon and c_lat and c_pluie):
            raise ValueError(
                "Colonnes minimales introuvables dans le CSV. "
                "Attendu au moins : station, lon, lat, pluie_mm "
                f"(colonnes detectees : {list(colonnes.values())})"
            )

        stations = []
        for ligne in lecteur:
            lon = to_float(ligne.get(c_lon, ""))
            lat = to_float(ligne.get(c_lat, ""))
            pluie = to_float(ligne.get(c_pluie, ""))
            if lon is None or lat is None:
                continue  # coordonnees invalides -> station ignoree
            stations.append({
                "zone": (ligne.get(c_zone) or "").strip(),
                "feuille": (ligne.get(c_feuille) or "").strip(),
                "station": (ligne.get(c_station) or "Station inconnue").strip(),
                "lon": lon,
                "lat": lat,
                "pluie_mm": pluie if pluie is not None else 0.0,
                "pluie_orig": (ligne.get(c_pluie_orig) or "").strip().strip('"'),
            })
        return stations


# ---------------------------------------------------------------------------
# 3. Style (couleur / rayon) par palier de pluie
# ---------------------------------------------------------------------------
def style_pour_valeur(valeur):
    for borne_min, borne_max, couleur, libelle, rayon in PALIERS:
        if valeur >= borne_min and (borne_max is None or valeur < borne_max):
            return couleur, libelle, rayon
    return "#999999", "Trace / 0 mm", 3


# ---------------------------------------------------------------------------
# 4. Titre / sous-titre automatiques (date extraite du nom de fichier si
#    possible, sinon date du jour ; stats calculees depuis les donnees)
# ---------------------------------------------------------------------------
def date_depuis_nom_fichier(chemin):
    nom = os.path.basename(chemin)
    m = re.search(r"(\d{2})(\d{2})(\d{2})(?:\D|$)", nom)
    if m:
        jj, mm, aa = m.groups()
        try:
            annee = 2000 + int(aa)
            d = datetime(annee, int(mm), int(jj))
            return f"{d.day} {MOIS_FR[d.month - 1].capitalize()} {d.year}"
        except ValueError:
            pass
    aujourdhui = datetime.now()
    return f"{aujourdhui.day} {MOIS_FR[aujourdhui.month - 1].capitalize()} {aujourdhui.year}"


def construire_titre_sous_titre(stations, chemin_csv):
    date_txt = date_depuis_nom_fichier(chemin_csv)
    titre = f"Situation pluviometrique du PLUVIO DU {date_txt}"

    actifs = [s for s in stations if s["pluie_mm"] and s["pluie_mm"] > 0]
    n_actifs = len(actifs)
    if actifs:
        s_max = max(actifs, key=lambda s: s["pluie_mm"])
        sous_titre = (f"Pluie journaliere observee \u2014 {n_actifs} postes actifs "
                      f"| Maximum : {s_max['pluie_mm']:g} mm ({s_max['station']})")
    else:
        sous_titre = f"Pluie journaliere observee \u2014 {len(stations)} postes"
    return titre, sous_titre


# ---------------------------------------------------------------------------
# 5. Generation du HTML (Leaflet, donnees embarquees, legende, info-bulles)
# ---------------------------------------------------------------------------
TEMPLATE_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">
<title>{titre}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
<style>
  * {{ box-sizing:border-box; }}
  html, body {{ margin:0; padding:0; height:100%; font-family: Arial, sans-serif; }}
  #map {{ position:absolute; top:74px; bottom:0; left:0; right:0; }}

  header {{
    position:absolute; top:0; left:0; right:0; height:74px; z-index:1000;
    background:#ffffff; border-bottom:3px solid #1c3f8f;
    padding:8px 16px; overflow:hidden;
  }}
  header h1 {{ margin:0; font-size:19px; color:#1c3f8f; }}
  header p  {{ margin:2px 0 0 0; font-size:12px; color:#444; }}

  #legend {{
    position:absolute; bottom:20px; right:12px; z-index:1000;
    background:rgba(255,255,255,0.95); padding:10px 14px; border-radius:6px;
    box-shadow:0 2px 6px rgba(0,0,0,0.3); font-size:12px;
  }}
  #legend b {{ display:block; margin-bottom:6px; font-size:13px; color:#1c3f8f; }}
  #legend div.legend-row {{ display:flex; align-items:center; margin:3px 0; }}
  #legend span.swatch {{ border-radius:50%; display:inline-block; margin-right:8px; border:1px solid #555; flex:none; }}

  .info-flottante {{
    position:absolute; pointer-events:none; z-index:1100;
    padding:8px 11px; font:13px Arial, Helvetica, sans-serif;
    background:rgba(255,255,255,0.97); box-shadow:0 0 12px rgba(0,0,0,0.25);
    border-radius:6px; display:none; max-width:240px; line-height:1.4;
  }}
  .info-flottante b.titre {{ color:#1c3f8f; font-size:14px; }}

  #recherche {{
    position:absolute; top:12px; right:12px; z-index:1000;
  }}
  #recherche input {{
    padding:6px 10px; border-radius:6px; border:1px solid #888; font-size:13px; width:180px;
    box-shadow:0 2px 6px rgba(0,0,0,0.25);
  }}

  #logo {{
    position:absolute; bottom:20px; left:12px; z-index:1000;
    background:rgba(255,255,255,0.9); padding:6px 8px; border-radius:6px;
    box-shadow:0 2px 6px rgba(0,0,0,0.3); display:block;
  }}
  #logo img {{ display:block; height:60px; width:auto; }}

  @media (max-width: 600px) {{
    header h1 {{ font-size:15px; }}
    header p {{ font-size:10px; }}
    #legend {{ font-size:10px; padding:6px 8px; }}
    #recherche input {{ width:120px; font-size:12px; }}
    #logo {{ bottom:20px; left:8px; padding:4px 6px; }}
    #logo img {{ height:44px; }}
  }}
</style>
</head>
<body>

<header>
  <h1>{titre}</h1>
  <p>{sous_titre}</p>
</header>

<div id="recherche">
  <input type="text" id="champ-recherche" placeholder="Rechercher une localite...">
</div>

<div id="legend">
  <b>Pluie 24h (mm)</b>
  {legende_html}
</div>

<div id="logo"><img src="logo_anacim.png" alt="Logo ANACIM"></div>

<div class="info-flottante" id="info-flottante"></div>

<div id="map"></div>

<script>
// ============================================================
// Donnees des postes pluviometriques (embarquees, generees automatiquement
// depuis le CSV source : {nom_source})
// ============================================================
const stations = {donnees_json};

// Paliers de couleur/rayon (doivent rester coherents avec la legende ci-dessus)
const paliers = {paliers_json};

function stylePourValeur(valeur) {{
  for (const p of paliers) {{
    if (valeur >= p.min && (p.max === null || valeur < p.max)) return p;
  }}
  return {{ couleur: "#999999", libelle: "Trace / 0 mm", rayon: 3 }};
}}

const map = L.map('map', {{ zoomControl: true }}).setView([{lat_centre}, {lon_centre}], {zoom_initial});

L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap contributors',
  maxZoom: 15
}}).addTo(map);

const infoDiv = document.getElementById('info-flottante');
const estTactile = ('ontouchstart' in window) || navigator.maxTouchPoints > 0;

function positionnerInfo(x, y) {{
  const mapEl = document.getElementById('map');
  const largeurCarte = mapEl.clientWidth;
  const hauteurCarte = mapEl.clientHeight;
  const largeurInfo = infoDiv.offsetWidth || 200;
  const hauteurInfo = infoDiv.offsetHeight || 90;
  let posX = x + 14;
  let posY = y - 10;
  if (posX + largeurInfo > largeurCarte) posX = x - largeurInfo - 14;
  if (posY < 0) posY = y + 14;
  if (posY + hauteurInfo > hauteurCarte) posY = hauteurCarte - hauteurInfo - 10;
  infoDiv.style.left = posX + 'px';
  infoDiv.style.top = posY + 'px';
}}

function afficherInfo(s) {{
  infoDiv.style.display = 'block';
  infoDiv.innerHTML =
    '<b class="titre">' + s.station + '</b><br>' +
    (s.feuille ? ('<b>Region :</b> ' + s.feuille + '<br>') : '') +
    (s.zone ? ('<b>Zone :</b> ' + s.zone + '<br>') : '') +
    '<b>Pluie 24h :</b> ' + s.pluie_mm.toLocaleString('fr-FR') + ' mm';
}}
function masquerInfo() {{ infoDiv.style.display = 'none'; }}

map.on('mousemove', function (e) {{
  if (!estTactile && infoDiv.style.display === 'block') positionnerInfo(e.containerPoint.x, e.containerPoint.y);
}});
map.on('click', function () {{ if (estTactile) masquerInfo(); }});

const marqueurs = [];

stations.forEach(function (s) {{
  const style = stylePourValeur(s.pluie_mm);
  const marker = L.circleMarker([s.lat, s.lon], {{
    radius: style.rayon,
    color: '#333',
    weight: 1,
    fillColor: style.couleur,
    fillOpacity: 0.85
  }}).addTo(map);

  marker.bindTooltip(s.station, {{ direction: 'top', offset: [0, -style.rayon], sticky: false }});

  marker.on({{
    mouseover: function (e) {{
      if (estTactile) return;
      afficherInfo(s);
      positionnerInfo(e.containerPoint.x, e.containerPoint.y);
    }},
    mouseout: function () {{ if (!estTactile) masquerInfo(); }},
    click: function (e) {{
      if (!estTactile) return;
      afficherInfo(s);
      positionnerInfo(e.containerPoint.x, e.containerPoint.y);
      L.DomEvent.stopPropagation(e);
    }}
  }});

  marqueurs.push({{ nom: s.station.toLowerCase(), marker: marker, lat: s.lat, lon: s.lon }});
}});

// ============================================================
// Recherche de localite : centre/zoome et ouvre l'info-bulle
// ============================================================
document.getElementById('champ-recherche').addEventListener('keydown', function (e) {{
  if (e.key !== 'Enter') return;
  const terme = this.value.trim().toLowerCase();
  if (!terme) return;
  const trouve = marqueurs.find(m => m.nom.includes(terme));
  if (trouve) {{
    map.setView([trouve.lat, trouve.lon], 10);
    trouve.marker.openTooltip();
  }}
}});
</script>
</body>
</html>
"""


def generer_html(stations, titre, sous_titre, chemin_csv, chemin_sortie):
    legende_lignes = []
    paliers_json = []
    for borne_min, borne_max, couleur, libelle, rayon in PALIERS:
        diam = rayon * 2
        legende_lignes.append(
            f'<div class="legend-row"><span class="swatch" style="width:{diam}px;height:{diam}px;'
            f'background:{couleur}"></span>{libelle}</div>'
        )
        paliers_json.append({"min": borne_min, "max": borne_max, "couleur": couleur,
                              "libelle": libelle, "rayon": rayon})

    if stations:
        lat_centre = sum(s["lat"] for s in stations) / len(stations)
        lon_centre = sum(s["lon"] for s in stations) / len(stations)
    else:
        lat_centre, lon_centre = 14.5, -14.5

    html = TEMPLATE_HTML.format(
        titre=titre,
        sous_titre=sous_titre,
        legende_html="\n  ".join(legende_lignes),
        donnees_json=json.dumps(stations, ensure_ascii=False),
        paliers_json=json.dumps(paliers_json, ensure_ascii=False),
        lat_centre=round(lat_centre, 4),
        lon_centre=round(lon_centre, 4),
        zoom_initial=7,
        nom_source=os.path.basename(chemin_csv),
    )

    with open(chemin_sortie, "w", encoding="utf-8") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# 6. Programme principal
# ---------------------------------------------------------------------------
def main():
    print("=== Generation automatique de carte pluviometrique interactive (ANACIM) ===\n")
    chemin_csv = demander_fichier_csv()
    stations = lire_csv(chemin_csv)
    print(f"  -> {len(stations)} postes lus dans le fichier.")

    chemin_sortie = demander_fichier_sortie(chemin_csv)
    titre, sous_titre = construire_titre_sous_titre(stations, chemin_csv)

    generer_html(stations, titre, sous_titre, chemin_csv, chemin_sortie)
    print(f"\nCarte generee : {chemin_sortie}")
    print("Ouvrez ce fichier dans un navigateur pour visualiser la carte interactive.")
    print("(Placez 'logo_anacim.png' a cote du fichier HTML si vous voulez que le logo s'affiche.)")


if __name__ == "__main__":
    main()
