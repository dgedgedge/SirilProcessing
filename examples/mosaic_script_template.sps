# Script Siril pour créer une mosaïque
# Ce fichier est un modèle à personnaliser selon vos besoins

requires 1.2.0

# Charger les images de la mosaïque
# Les fichiers sont automatiquement nommés panel_01_*, panel_02_*, etc.
convert panel -out=converted
cd converted

# Prétraitement si nécessaire (généralement déjà fait)
# calibrate panel -dark=master_dark -flat=master_flat

# Détection automatique des étoiles pour l'alignement
# register panel
seqfindstar panel

# Alignement des images
register panel

# Empilement des images alignées pour créer la mosaïque
# Méthode par défaut: moyenne avec rejet sigma
stack r_panel rej 3 3 -norm=addscale -output_norm=mul -out=mosaic

# Alternative: stacking médian
# stack r_panel median -norm=addscale -output_norm=mul -out=mosaic

# Sauvegarde finale
# Le fichier sera sauvé comme mosaic.fit dans le répertoire de travail
save mosaic

# Nettoyage optionnel
# Les fichiers temporaires seront nettoyés automatiquement par le script Python