# 1. Rapports synthétiques et audits détaillés de restauration

[Restauration](restoration.md) · [Manuel postProcess](README.md)

## 1.1. Organisation des sorties

La comparaison des chaînes produit un `05_restoration/restoration.json`
synthétique, identifié par `report_schema_version: 2` et
`report_detail: "summary"`. Il conserve les décisions, scores et contributions,
critères agrégés, seuils enregistrés, drapeaux bloquants, motifs de rejet,
durées, paramètres et signatures, chemins d'images, étapes et liens de cache.
Le classement et la sélection restent calculés sur les mesures complètes.

Chaque opération contient un `audit_path` vers son rapport JSON local,
également désigné par `report_path`. Chaque variante effectivement contrôlée
finalement référence son `verification_finale/verification.json` par
`audit_path`. Une variante exclue localement n'a pas de contrôle final : ses
étapes et leurs `operation_id` renvoient aux audits des opérations concernées.
La référence inchangée n'a pas d'audit final supplémentaire.

Les audits détaillés portent `report_detail: "detailed"`. Ils conservent les
paires stellaires, mesures de référence, populations de bruit, mesures par
étoile et par région, ainsi que les tentatives internes nécessaires à
l'explication des contrôles. Les FITS, masques, catalogues et aperçus gardent
leurs chemins. Un audit d'une autre entrée intermédiaire demeure distinct,
même si son moteur ou ses paramètres sont les mêmes.

## 1.2. Suppression des copies inutiles

Dans chaque audit détaillé, les mesures complètes des diagnostics restent dans
`artifacts`. Les copies publiées dans `criteria` et `quality.criteria`
contiennent seulement leurs synthèses. Le champ historique
`artifacts.high_frequency_residual`, copie exacte de `artifacts.channels`,
n'est plus écrit ; `artifacts.channels` est conservé. La même projection est
appliquée aux tentatives internes.

Le global ne recopie pas `comparison.pairs`,
`comparison.reference_measurements`, les listes de mesures par étoile,
les listes de régions diffuses ni les résidus par canal. Les verdicts globaux
`diffuse` et `stellar_flux`, y compris FAIL non bloquant et UNKNOWN, restent
visibles ; leurs mesures et seuils par région ou étoile sont consultables dans
l'audit référencé. L'absence d'une valeur agrégée ne devient pas une mesure zéro.

Les détails restent stockés une fois dans chaque audit concerné, sans chercher
à fusionner les observations de plusieurs contrôles locaux ou finaux. Il ne
s'agit pas d'une suppression des diagnostics scientifiques ni d'une nouvelle
politique d'acceptation.

## 1.3. Module et publication

`lib.restoration_report_storage.RestorationReportStorage` sépare trois rôles :
`summary` projette un audit en synthèse, `detailed` conserve les détails en
retirant leurs copies, et `write` publie le JSON. La projection n'altère pas
les données de calcul ni les caches utilisés par l'orchestrateur.

La synthèse conserve les champs scalaires et dictionnaires, les petites listes
de scalaires jusqu'à 32 éléments et les listes sémantiques d'étapes, opérations,
variantes, tentatives, enfants de mesure et synthèses par canal. Les populations
scientifiques non retenues restent dans les audits. Les signatures enregistrées
concernent les mêmes entrées et paramètres ; les pixels FITS ne sont pas modifiés
par cette sérialisation.

Le global est publié après chaque variante et en fin de traitement. Un fichier
temporaire est écrit dans son dossier, puis remplace atomiquement le JSON
précédent. Une erreur de sérialisation ou de remplacement est propagée et ne
laisse pas un rapport précédent partiellement écrasé. Il ne s'agit pas d'une
garantie de persistance après une panne matérielle ni d'une preuve que tous
les essais d'un run encore actif sont terminés.

## 1.4. Lecture et compatibilité

L'index HTML, la synthèse texte et le
[comparateur multi-répertoires](../compareRestoration/README.md) lisent les
mesures agrégées sans recharger les populations. L'index HTML et la synthèse
texte indiquent les audits finaux ; le comparateur présente les mesures
et décisions sans colonnes de chemins de fichiers. Les reprises continuent d'utiliser les
chemins d'entrée, de sortie et leurs contrôles d'intégrité habituels.

Les anciens rapports complets restent lisibles. Ce format concerne les nouveaux
rapports de la comparaison des chaînes ; il ne convertit pas les fichiers déjà
produits, ne modifie pas un run en cours et ne change pas les anciens rapports
du mode de comparaison séparée par étape. Pour obtenir des rapports compacts,
il faut relancer la restauration avec la nouvelle version ; une reprise limitée
à AutoGHS conserve le rapport de restauration existant.

Le champ `early_stop` décrit aussi les recherches arrêtées pour qualité
suffisante après AR → GraXpert : seules les variantes exécutées sont publiées,
avec le nombre de variantes restant non exécutées. La synthèse et l'index
précisent ce caractère partiel du classement. Voir
[la politique de recherche](restoration.md#restoration-early-stop).
