#!/usr/bin/env python3
"""
Documentation de la nouvelle fonctionnalité de validation des darks
================================================================

Cette fonctionnalité permet de détecter automatiquement les darks pris 
avec le capot du télescope ouvert (présence de lumière parasite).
"""

print("""
=== NOUVELLE FONCTIONNALITÉ : VALIDATION DES DARKS ===

🎯 OBJECTIF
Détecter et rejeter automatiquement les fichiers dark qui ont été pris
sans fermer le capot du télescope, évitant ainsi la contamination par
la lumière parasite ou les étoiles.

📊 MÉTHODE DE DÉTECTION
L'analyse statistique examine plusieurs paramètres de l'image :

1. MÉDIANE : La valeur centrale des pixels ne doit pas être trop élevée
   - Seuil par défaut : 200 ADU
   - Détecte : Lumière résiduelle générale

2. PIXELS CHAUDS : Pourcentage de pixels anormalement brillants
   - Seuil par défaut : 0.01% (1 pixel sur 10,000)
   - Détecte : Étoiles, points brillants

3. ÉCART-TYPE/MÉDIANE : Rapport entre la dispersion et la valeur centrale
   - Seuil par défaut : 0.5
   - Détecte : Illumination non-uniforme

4. 99ème PERCENTILE : Valeurs extrêmes dans l'image
   - Seuil calculé : médiane + 10×écart-type
   - Détecte : Zones très brillantes localisées

🚀 UTILISATION

# Générer un rapport de validation seul
python3 bin/darkLibUpdate.py --input-dirs /path/to/darks --validation-report

# Traiter en excluant automatiquement les darks suspects
python3 bin/darkLibUpdate.py --input-dirs /path/to/darks --validate-darks

# Combiner validation et autres options
python3 bin/darkLibUpdate.py --input-dirs /path/to/darks --validate-darks --force-recalc --log-level INFO

📋 RAPPORT DE VALIDATION
Le rapport affiche :
- Nombre total de darks analysés
- Nombre de darks valides vs suspects
- Liste détaillée des darks suspects avec raisons
- Exemples de darks valides
- Statistiques (médiane, écart-type, pixels chauds) pour chaque fichier

❌ EXEMPLES DE DÉTECTION
- "Median too high: 450.2 > 200.0 ADU (probable light leak)"
- "Too many hot pixels: 0.15% > 0.01% (probable stars/light)"
- "Standard deviation too high: std/median = 0.8 > 0.5 (non-uniform illumination)"
- "99th percentile too high: 2500.1 > 850.0 ADU (bright spots detected)"

⚙️ CONFIGURATION AVANCÉE
Les seuils peuvent être ajustés en modifiant les paramètres de is_valid_dark() :
- max_median_adu : Médiane maximale (défaut: 200 ADU)
- max_hot_pixels_percent : % max pixels chauds (défaut: 0.01%)
- max_std_factor : Facteur max std/médiane (défaut: 0.5)

✅ AVANTAGES
- Détection automatique des darks contaminés
- Amélioration de la qualité des master darks
- Économie de temps en évitant les reprises
- Diagnostics détaillés pour comprendre les problèmes

🔧 INTÉGRATION
La validation s'intègre parfaitement avec les fonctionnalités existantes :
- Compatible avec --temperature-precision
- Fonctionne avec --force-recalc
- Respecte les groupes par température/exposition/gain
- Logs détaillés des rejets

Cette fonctionnalité est totalement optionnelle et n'affecte pas l'utilisation normale.
""")

if __name__ == "__main__":
    pass