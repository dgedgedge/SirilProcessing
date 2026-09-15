#!/usr/bin/env python3

"""
Détection de doublons entre deux répertoires A (référence) et B (à nettoyer).

Important sur les statistiques basename (--check-basename):
    - Les candidats "rejetés sur basename" sont des paires A/B qui avaient
        déjà validé les tests taille + SHA-256, puis ont été refusées car
        les basenames diffèrent.
    - Le compteur "fichiers B bloqués par basename" indique les fichiers de B
        qui avaient au moins une correspondance contenu, mais aucune avec
        basename identique.
"""

import argparse
import hashlib
import shlex
import sys
from pathlib import Path


BLOCK_SIZE = 4 * 1024 * 1024  # 4 MiB


def human_size(size):
    """Affichage lisible d'une taille en octets."""
    units = ["o", "Kio", "Mio", "Gio", "Tio", "Pio"]
    value = float(size)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024


def absolute_path(path):
    """
    Retourne le chemin absolu canonique.

    resolve() élimine notamment :
      - les /./
      - les /../
      - les chemins relatifs
      - les liens symboliques présents dans le chemin
    """
    return path.resolve()


def same_absolute_path(path1, path2):
    """
    Vérifie si deux chemins correspondent au même chemin absolu.
    """
    try:
        return absolute_path(path1) == absolute_path(path2)
    except OSError:
        return False


def sha256(path):
    """Calcule le SHA-256 d'un fichier."""
    h = hashlib.sha256()

    try:
        with path.open("rb") as f:
            while True:
                block = f.read(BLOCK_SIZE)

                if not block:
                    break

                h.update(block)

    except OSError as e:
        print(
            f"ERREUR lecture : {path} : {e}",
            file=sys.stderr
        )
        return None

    return h.digest()


def iter_files(directory, excluded_paths=None):
    """
    Parcourt récursivement un répertoire.

    Les liens symboliques sont ignorés.
    Les chemins indiqués dans excluded_paths sont également ignorés.
    """
    if excluded_paths is None:
        excluded_paths = set()

    for path in directory.rglob("*"):
        try:
            if path.is_symlink():
                continue

            if not path.is_file():
                continue

            resolved = path.resolve()

            if resolved in excluded_paths:
                continue

            yield path

        except OSError as e:
            print(
                f"ERREUR accès : {path} : {e}",
                file=sys.stderr
            )


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Recherche dans le répertoire B les fichiers dont le contenu "
            "est strictement identique à un fichier du répertoire A, "
            "puis génère un script shell contenant les commandes rm. "
            "Le programme Python ne supprime aucun fichier."
        ),
        epilog=r"""
Exemples :

  Analyse de /data/photos_archive comme référence A
  et recherche des doublons dans /data/photos_import :

    %(prog)s /data/photos_archive /data/photos_import

  Même opération en exigeant également que les noms de fichiers
  (basename) soient identiques :

    %(prog)s /data/photos_archive /data/photos_import --check-basename

  Génération du script de suppression dans un fichier particulier :

    %(prog)s /data/photos_archive /data/photos_import \
        --check-basename \
        --output /tmp/nettoyage_photos.sh

  Vérifier ensuite le script généré :

    less /tmp/nettoyage_photos.sh

  Et, uniquement après vérification, l'exécuter :

    /tmp/nettoyage_photos.sh


Principe :

  - A est le répertoire de référence.
  - Aucun fichier de A n'est supprimé.
  - B est le répertoire dans lequel les doublons sont recherchés.
  - Le programme Python ne supprime directement aucun fichier.
  - Il génère uniquement un script shell contenant les commandes rm.

  Sans --check-basename :

      La détection repose sur :
          taille identique
          + SHA-256 identique

  Avec --check-basename :

      La détection repose sur :
          taille identique
          + SHA-256 identique
      puis la suppression est autorisée seulement si
          basename identique

      Exemple accepté :

          A : /archives/2025/vacances/IMG_1234.jpg
          B : /import/DCIM/IMG_1234.jpg

      Exemple rejeté :

          A : /archives/2025/vacances/IMG_1234.jpg
          B : /import/DCIM/photo_1234.jpg

      même si le contenu des deux fichiers est identique.

Protections :

  - A et B ne peuvent pas désigner le même répertoire.
  - Un fichier n'est jamais comparé à lui-même lorsque les chemins
    absolus sont identiques.
  - Les liens symboliques sont ignorés.
  - Le script shell généré est exclu de l'analyse.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "A",
        type=Path,
        help="Répertoire de référence. Il n'est jamais modifié."
    )

    parser.add_argument(
        "B",
        type=Path,
        help="Répertoire dans lequel rechercher les doublons."
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("supprime_doublons.sh"),
        help=(
            "Nom du script shell généré "
            "(défaut : supprime_doublons.sh)"
        )
    )

    parser.add_argument(
        "--check-basename",
        action="store_true",
        help=(
            "Exige que le basename du fichier de A soit identique "
            "à celui du fichier de B après validation du contenu."
        )
    )

    args = parser.parse_args()

    # ------------------------------------------------------------
    # Résolution des chemins
    # ------------------------------------------------------------

    try:
        A = args.A.resolve()
        B = args.B.resolve()
        output = args.output.resolve()

    except OSError as e:
        print(
            f"ERREUR lors de la résolution des chemins : {e}",
            file=sys.stderr
        )
        return 1

    # ------------------------------------------------------------
    # Contrôles de sécurité
    # ------------------------------------------------------------

    if not A.exists():
        print(f"ERREUR : le répertoire A n'existe pas : {A}")
        return 1

    if not A.is_dir():
        print(f"ERREUR : A n'est pas un répertoire : {A}")
        return 1

    if not B.exists():
        print(f"ERREUR : le répertoire B n'existe pas : {B}")
        return 1

    if not B.is_dir():
        print(f"ERREUR : B n'est pas un répertoire : {B}")
        return 1

    if A == B:
        print()
        print("ERREUR DE SÉCURITÉ")
        print("A et B désignent le même répertoire :")
        print(f"    {A}")
        print()
        print("Aucune opération effectuée.")
        return 1

    print()
    print("Recherche de doublons")
    print("=====================")
    print()
    print(f"A (référence)       : {A}")
    print(f"B                   : {B}")
    print(f"Script généré       : {output}")
    print(
        f"Contrôle du basename : "
        f"{'ACTIVÉ' if args.check_basename else 'désactivé'}"
    )
    print()

    # ------------------------------------------------------------
    # Fichiers à exclure
    # ------------------------------------------------------------

    excluded_paths = {output}

    # ------------------------------------------------------------
    # Indexation de A par taille
    # ------------------------------------------------------------

    print("Indexation des fichiers de A...")

    files_by_size = {}

    nb_a = 0
    errors = 0

    for path in iter_files(A, excluded_paths):

        try:
            resolved_path = path.resolve()
            size = path.stat().st_size

        except OSError as e:
            print(
                f"ERREUR stat : {path} : {e}",
                file=sys.stderr
            )
            errors += 1
            continue

        files_by_size.setdefault(size, []).append(resolved_path)
        nb_a += 1

    print(f"  {nb_a} fichier(s) indexé(s).")
    print()

    # ------------------------------------------------------------
    # Cache des SHA-256 des fichiers de A
    # ------------------------------------------------------------

    hashes_a = {}

    # ------------------------------------------------------------
    # Statistiques
    # ------------------------------------------------------------

    nb_b = 0

    duplicates = 0
    duplicate_size = 0

    skipped_same_path = 0

    basename_rejected_sha_matches = 0
    basename_blocked_files = 0

    # ------------------------------------------------------------
    # Création du script shell
    # ------------------------------------------------------------

    try:
        output.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        script = output.open(
            "w",
            encoding="utf-8"
        )

    except OSError as e:
        print(
            f"ERREUR : impossible de créer {output} : {e}",
            file=sys.stderr
        )
        return 1

    with script:

        script.write("#!/bin/bash\n")
        script.write("\n")
        script.write("set -u\n")
        script.write("\n")

        script.write(
            "# ============================================================\n"
        )
        script.write(
            "# Script de suppression de fichiers dupliqués\n"
        )
        script.write(
            "# ============================================================\n"
        )
        script.write("#\n")
        script.write(
            "# Ce fichier a été généré automatiquement.\n"
        )
        script.write("#\n")
        script.write(
            "# IMPORTANT : vérifier son contenu avant de l'exécuter.\n"
        )
        script.write("#\n")
        script.write("# Répertoire de référence A :\n")
        script.write(f"#   {A}\n")
        script.write("#\n")
        script.write(
            "# Répertoire dans lequel les fichiers peuvent être supprimés B :\n"
        )
        script.write(f"#   {B}\n")
        script.write("#\n")

        if args.check_basename:
            script.write(
                "# Contrôle du basename : ACTIVÉ\n"
            )
        else:
            script.write(
                "# Contrôle du basename : désactivé\n"
            )

        script.write("#\n")
        script.write(
            "# Le programme générateur n'a supprimé aucun fichier.\n"
        )
        script.write(
            "# Les commandes ci-dessous ne concernent que des fichiers de B.\n"
        )
        script.write("\n")

        # --------------------------------------------------------
        # Parcours de B
        # --------------------------------------------------------

        print("Analyse des fichiers de B...")
        print()

        for fb in iter_files(B, excluded_paths):

            nb_b += 1

            try:
                fb_abs = fb.resolve()
                size_b = fb.stat().st_size

            except OSError as e:
                print(
                    f"ERREUR stat : {fb} : {e}",
                    file=sys.stderr
                )
                errors += 1
                continue

            # ----------------------------------------------------
            # 1. Candidats de même taille
            # ----------------------------------------------------

            candidates = files_by_size.get(size_b)

            if not candidates:
                continue

            # ----------------------------------------------------
            # 2. Élimination du même chemin absolu
            # ----------------------------------------------------

            candidates_different_path = []

            for fa_abs in candidates:

                if fa_abs == fb_abs:
                    skipped_same_path += 1
                    continue

                candidates_different_path.append(fa_abs)

            if not candidates_different_path:
                continue

            # ----------------------------------------------------
            # 3. SHA-256 de B
            #
            # On ne le calcule qu'après tous les contrôles rapides.
            # ----------------------------------------------------

            hash_b = sha256(fb_abs)

            if hash_b is None:
                errors += 1
                continue

            match = None
            has_sha_match_rejected_by_basename = False

            # ----------------------------------------------------
            # 4. Comparaison avec les candidats de A
            # ----------------------------------------------------

            for fa_abs in candidates_different_path:

                # Protection répétée juste avant comparaison.
                if same_absolute_path(fa_abs, fb_abs):
                    skipped_same_path += 1
                    continue

                # Le hash de chaque fichier de A n'est calculé
                # qu'une seule fois.
                if fa_abs not in hashes_a:
                    hashes_a[fa_abs] = sha256(fa_abs)

                hash_a = hashes_a[fa_abs]

                if hash_a is None:
                    errors += 1
                    continue

                if hash_a == hash_b:

                    # Le contrôle basename intervient après validation
                    # taille + SHA, comme demandé.
                    if args.check_basename and fa_abs.name != fb_abs.name:
                        basename_rejected_sha_matches += 1
                        has_sha_match_rejected_by_basename = True
                        continue

                    match = fa_abs
                    break

            if match is None:
                if args.check_basename and has_sha_match_rejected_by_basename:
                    basename_blocked_files += 1
                continue

            # ----------------------------------------------------
            # Doublon trouvé
            # ----------------------------------------------------

            duplicates += 1
            duplicate_size += size_b

            print(f"DOUBLON : {fb_abs}")
            print(f"       = : {match}")
            print(f"  taille : {human_size(size_b)}")
            print()

            # ----------------------------------------------------
            # Commande shell
            # ----------------------------------------------------

            script.write(
                f"# {human_size(size_b)}\n"
            )

            script.write(
                "# Copie conservée : "
                f"{shlex.quote(str(match))}\n"
            )

            script.write(
                "# Copie supprimée : "
                f"{shlex.quote(str(fb_abs))}\n"
            )

            script.write(
                "rm -- "
                f"{shlex.quote(str(fb_abs))}\n"
            )

            script.write("\n")

        # --------------------------------------------------------
        # Résumé inscrit dans le script
        # --------------------------------------------------------

        script.write(
            "# ============================================================\n"
        )
        script.write("# Résumé\n")
        script.write(
            "# ============================================================\n"
        )

        script.write(
            f"# Doublons : {duplicates}\n"
        )

        script.write(
            f"# Espace récupérable : {human_size(duplicate_size)}\n"
        )

        script.write(
            f"# Comparaisons même chemin ignorées : "
            f"{skipped_same_path}\n"
        )

        if args.check_basename:
            script.write(
                f"# Correspondances taille+SHA rejetées par basename : "
                f"{basename_rejected_sha_matches}\n"
            )

            script.write(
                f"# Fichiers B bloqués par basename après match SHA : "
                f"{basename_blocked_files}\n"
            )

    # ------------------------------------------------------------
    # Rendre le script exécutable
    # ------------------------------------------------------------

    try:
        output.chmod(0o755)

    except OSError as e:
        print(
            f"ATTENTION : impossible de rendre le script exécutable : {e}",
            file=sys.stderr
        )

    # ------------------------------------------------------------
    # Bilan
    # ------------------------------------------------------------

    print()
    print("=" * 68)
    print("BILAN")
    print("=" * 68)

    print(f"Fichiers indexés dans A              : {nb_a}")
    print(f"Fichiers examinés dans B             : {nb_b}")
    print(f"Doublons trouvés                     : {duplicates}")
    print(f"Espace récupérable                   : {human_size(duplicate_size)}")

    print(
        f"Comparaisons même chemin ignorées    : "
        f"{skipped_same_path}"
    )

    if args.check_basename:
        print(
            f"Matchs taille+SHA rejetés par basename : "
            f"{basename_rejected_sha_matches}"
        )

        print(
            f"Fichiers B bloqués par basename      : "
            f"{basename_blocked_files}"
        )

    if errors:
        print(f"Erreurs rencontrées                   : {errors}")

    print()
    print("Aucun fichier n'a été supprimé par ce programme.")
    print()
    print(f"Script de suppression : {output}")
    print()
    print("Vérifier d'abord son contenu :")
    print()
    print(f"    less {shlex.quote(str(output))}")
    print()
    print("Puis, si tout est correct :")
    print()
    print(f"    {shlex.quote(str(output))}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
