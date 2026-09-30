#!/bin/env python3
"""
DarkLib class for managing dark frame library.
This module provides functionality to group, stack, and maintain master dark frames.
"""
import os
import datetime
import shutil
import logging

from lib.fits_info import FitsInfo
from lib.siril_utils import Siril



class DarkLib:
    """
    Classe pour gérer une bibliothèque de master darks.
    Fournit des méthodes pour grouper, empiler et maintenir des master darks.
    """

    CONFIG_DEFAULTS = {
        'dark_library_path': os.path.abspath(os.path.expanduser("~/darkLib")),
        'bias_library_path': os.path.abspath(os.path.expanduser("~/biasLib")),
        'work_dir': os.path.abspath(os.path.expanduser("~/tmp/sirilWorkDir")),
        'cfa': False,
        'output_norm': "noscale",
        'rejection_method': "winsorizedsigma",
        'rejection_param1': 3.0,
        'rejection_param2': 3.0,
        'max_age_days': 182,
        'stack_method': "average",
        'temperature_precision': 0.2,
        'min_darks_threshold': 0,
        'validate_darks': False,
        'report': False,
        'max_hot_pixels_percent': 1.0,
        'input_dirs': None,
    }

    @classmethod
    def add_arguments(cls, parser):
        """Déclare les paramètres du traitement avec leurs défauts locaux."""
        config = cls.CONFIG_DEFAULTS
        # Configuration des arguments
        parser.add_argument(
            '-i', '--input-dirs',
            dest='input_dirs',
            nargs='+',
            default=config.get("input_dirs"),
            help="Liste des répertoires contenant les fichiers dark à traiter"
        )
        parser.add_argument(
            '-d', '--dark-library-path',
            dest='dark_library_path',
            type=str,
            default=config.get("dark_library_path"),
            help=f"Répertoire où sont stockés les master darks. (Défaut: '{config.get('dark_library_path')}')"
        )
        parser.add_argument(
            '-w', '--work-dir',
            dest='work_dir',
            type=str,
            default=config.get("work_dir"),
            help=f"Répertoire de travail temporaire. (Défaut: '{config.get('work_dir')}')"
        )
        parser.add_argument(
            '-D', '--dummy',
            dest='dummy',
            action='store_true',
            help="Mode test: analyse les fichiers mais n'exécute pas Siril"
        )

        parser.add_argument(
            '-l', '--log-level',
            dest='log_level',
            choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
            default='WARNING',
            help=f"Niveau de journalisation. (Défaut: 'WARNING')"
        )
    
        parser.add_argument(
            '-L', '--list-darks',
            dest='list_darks',
            action='store_true',
            help="Liste tous les master darks disponibles dans la bibliothèque avec leurs caractéristiques"
        )
        parser.add_argument(
            '--log-skipped',
            dest='log_skipped',
            action='store_true',
            help="Log les fichiers ignorés (non-DARK ou FITS invalides)"
        )
        parser.add_argument(
            '-a', '--max-age',
            dest='max_age',
            type=int,
            default=config.get("max_age_days"),
            help=f"Nombre maximum de jours d'écart entre le dark le plus récent et le plus ancien d'un groupe. (Défaut: {config.get('max_age_days')} jours)"
        )
        parser.add_argument(
            '-c', '--cfa',
            dest='cfa',
            action='store_true',
            default=config.get("cfa", False),
            help="Indique que les images sont en couleur (CFA). Par défaut, les images sont considérées monochromes."
        )
        parser.add_argument(
            '-o', '--output-norm',
            dest='output_norm',
            choices=['addscale', 'noscale', 'rejection'],
            default=config.get("output_norm"),
            help=f"Méthode de normalisation pour Siril. (Défaut: '{config.get('output_norm')}')"
        )
        parser.add_argument(
            '-r', '--rejection-method',
            dest='rejection_method',
            choices=['winsorizedsigma', 'sigma', 'minmax', 'percentile', 'none'],
            default=config.get("rejection_method"),
            help=f"Méthode de rejet pour Siril. (Défaut: '{config.get('rejection_method')}')"
        )
        parser.add_argument(
            '--rejection-param1',
            dest='rejection_param1',
            type=float,
            default=config.get("rejection_param1"),
            help=f"Premier paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param1')})"
        )
        parser.add_argument(
            '--rejection-param2',
            dest='rejection_param2',
            type=float,
            default=config.get("rejection_param2"),
            help=f"Second paramètre de rejet pour Siril. (Défaut: {config.get('rejection_param2')})"
        )
        parser.add_argument(
            '--stack-method',
            dest='stack_method',
            choices=['average', 'median'],
            default=config.get("stack_method"),
            help=f"Méthode d'empilement: 'average' (Empilement par moyenne avec rejet) ou 'median' (Empilement médian). (Défaut: '{config.get('stack_method')}')"
        )
        parser.add_argument(
            '-t', '--temperature-precision',
            dest='temperature_precision',
            type=float,
            default=config.get("temperature_precision"),
            help=f"Précision d'arrondi pour la température en degrés Celsius. (Défaut: {config.get('temperature_precision')}°C)"
        )
        parser.add_argument(
            '-n', '--min-darks-threshold',
            dest='min_darks_threshold',
            type=int,
            default=config.get("min_darks_threshold", 10),
            help=f"Seuil minimum de darks pour mettre à jour un master dark existant. Un master dark sera remplacé si le nombre de darks disponibles dépasse ce seuil OU s'il dépasse le nombre de darks utilisés dans le master dark précédent. (Défaut: {config.get('min_darks_threshold', 0)})"
        )
        parser.add_argument(
            '-f', '--force-recalc',
            dest='force_recalc',
            action='store_true',
            help="Force le recalcul de tous les master darks existants, même s'ils sont plus récents que les fichiers sources. Utile pour tester de nouveaux paramètres de regroupement."
        )
        parser.add_argument(
            '-v', '--validate-darks',
            dest='validate_darks',
            action='store_true',
            default=config.get("validate_darks", False),
            help="Valide les fichiers darks en analysant leurs statistiques pour détecter ceux pris avec le capot ouvert (présence de lumière parasite)."
        )
        parser.add_argument(
            '--no-validate-darks',
            dest='validate_darks',
            action='store_false',
            help="Désactive la validation des fichiers darks."
        )
        parser.add_argument(
            '-R', '--report',
            dest='report',
            action='store_true',
            default=config.get("report", False),
            help="Génère un rapport détaillé du traitement et de la validation effectués."
        )
        parser.add_argument(
            '--no-report',
            dest='report',
            action='store_false',
            help="Désactive la génération du rapport détaillé."
        )
        parser.add_argument(
            '--min-median-for-tests',
            dest='min_median_for_tests',
            type=float,
            default=10.0,
            help="Seuil minimal de médiane (en ADU) au-dessus duquel les tests de robustesse (MAD/median et dispersion centrale) sont effectués lors de la validation des darks. (Défaut: 10.0 ADU)"
        )
        parser.add_argument(
            '--max-median-adu',
            dest='max_median_adu',
            type=float,
            default=200.0,
            help="TEST 1: Médiane maximale acceptable (en ADU) pour un dark valide. Au-delà de ce seuil, le dark est rejeté car il contient probablement de la lumière parasite (capot mal fermé, pollution lumineuse). (Défaut: 200.0 ADU)"
        )
        parser.add_argument(
            '--max-hot-pixels-percent',
            dest='max_hot_pixels_percent',
            type=float,
            default=1.0,
            help="TEST 2: Pourcentage maximal acceptable de pixels chauds (définis comme mean + 3×std). Au-delà de ce seuil, le dark est rejeté car il contient probablement des étoiles ou de la lumière. (Défaut: 1.0%%)"
        )
        parser.add_argument(
            '--max-mad-factor',
            dest='max_mad_factor',
            type=float,
            default=0.15,
            help="TEST 3: Facteur maximal acceptable pour le ratio MAD/médiane (bruit relatif robuste). Ce test n'est effectué que si médiane > min_median_for_tests. Un ratio élevé indique une illumination non uniforme ou un gradient. (Défaut: 0.15)"
        )
        parser.add_argument(
            '--max-central-dispersion',
            dest='max_central_dispersion',
            type=float,
            default=0.4,
            help="TEST 4: Dispersion centrale maximale acceptable, calculée comme (p90-p10)/médiane. Ce test n'est effectué que si médiane > min_median_for_tests. Une dispersion élevée indique une illumination variable. (Défaut: 0.4)"
        )



    parameter_persistence = dict.fromkeys(
        ('dummy', 'log_level', 'list_darks', 'log_skipped', 'force_recalc'), False)
    config_keys = {'max_age': 'max_age_days'}
    config_path_parameters = {'dark_library_path', 'bias_library_path', 'work_dir', 'input_dirs'}
    legacy_parameters = {
        'min_median_for_tests', 'max_median_adu', 'max_hot_pixels_percent',
        'max_mad_factor', 'max_central_dispersion',
    }

    def __init__(self, config=None, force_recalc=None):
        """
        Initialise la bibliothèque de master darks avec les paramètres de configuration.
        
        Args:
            config: Configuration object
            force_recalc: Force recalculation of all master darks regardless of age
        """

        logging.info("Initializing DarkLib instance.")
        if config is None:
            from lib.config import Config
            config = Config()
        if force_recalc is None:
            force_recalc = config.get('force_recalc', False)

        # Configuration
        self.dark_library_path = config.get("dark_library_path")
        self.work_dir = config.get("work_dir")
        self.siril_cfa = config.get("cfa", False)
        self.siril_output_norm = config.get("output_norm", "noscale")
        self.siril_rejection_method = config.get("rejection_method", "winsorizedsigma")
        self.siril_rejection_param1 = config.get("rejection_param1", 3.0)
        self.siril_rejection_param2 = config.get("rejection_param2", 3.0)
        self.siril_stack_method = config.get("stack_method", "average")
        self.max_age_days = config.get("max_age_days", 182)
        
        # Initialisation de l'instance Siril avec la configuration par défaut
        self.siril = Siril.create_with_defaults()
        self.temperature_precision = config.get("temperature_precision", 0.5)
        self.min_darks_threshold = config.get("min_darks_threshold", 0)
        
        # Paramètres de validation des darks
        self.min_median_for_tests = config.get("min_median_for_tests", 10.0)
        self.max_median_adu = config.get("max_median_adu", 200.0)
        self.max_hot_pixels_percent = config.get("max_hot_pixels_percent", 1.0)
        self.max_mad_factor = config.get("max_mad_factor", 0.15)
        self.max_central_dispersion = config.get("max_central_dispersion", 0.4)
        
        self.force_recalc = force_recalc

        # Structure pour collecter les données de validation et de traitement
        self.validation_data = {
            'updated_masters': [],  # Liste des master darks mis à jour
            'rejected_files': {},   # Dict[group_key, list[rejected_files_info]]
            'validation_stats': {}  # Dict[group_key, validation_summary]
        }

        # Créer les répertoires nécessaires
        os.makedirs(self.dark_library_path, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)

    def group_dark_files(self, input_dirs: list[str], log_groups: bool = True, log_skipped: bool = False) -> dict[str, list[FitsInfo]]:
        """
        Groupe les fichiers dark par température, temps d'exposition, gain et nom de caméra.
        Ne conserve que les fichiers FITS les plus récents dans l'intervalle de temps spécifié.
        """
        dark_groups = {}
        skipped_files = []
        filtered_by_date = []  # Liste des fichiers filtrés par la date

        for input_dir in input_dirs:
            if not os.path.isdir(input_dir):
                logging.warning(f"Input directory not found: {input_dir}. Ignored.")
                continue

            logging.info(f"Scanning directory: {input_dir}")
            for root, _, files in os.walk(input_dir):
                for filename in files:
                    if filename.lower().endswith(('.fit', '.fits')):
                        filepath = os.path.join(root, filename)
                        info = FitsInfo(filepath)
                        group_key = None
                        if info.validData():
                            group_key = info.group_key(self.temperature_precision)
                        if group_key and info.is_dark():
                            dark_groups.setdefault(group_key, []).append(info)
                        else:
                            skipped_files.append(filepath)

        # Tri des groupes par date décroissante et filtrage par intervalle de temps
        for key in list(dark_groups.keys()):
            infos = dark_groups[key]
            infos.sort(key=lambda x: x.date_obs(), reverse=True)
            if infos:
                latest_date = infos[0].date_obs()
                max_age = datetime.timedelta(days=self.max_age_days)
                filtered = [info for info in infos if (latest_date - info.date_obs()) <= max_age]
                removed = [info for info in infos if (latest_date - info.date_obs()) > max_age]
                dark_groups[key] = filtered
                if removed:
                    filtered_by_date.extend(removed)
                    logging.info(f"Fichiers filtrés par la date (>{self.max_age_days} jours du plus récent) pour le groupe {key}:")
                    for info in removed:
                        logging.info(f"  FILTERED: {info.filepath} | DATE-OBS={info.date_obs()}")

        # Affichage des groupes et fichiers
        if log_groups:
            for group_key, infos in dark_groups.items():
                logging.info(
                    f"GROUP: {group_key}"
                )
                for info in infos:
                    logging.info(
                        f"  FILE: {info.filepath} | DATE-OBS={info.date_obs()} | BINNING={info.binning()}"
                    )
        if log_skipped and skipped_files:
            logging.info("Fichiers ignorés (non conformes ou non DARK) :")
            for f in skipped_files:
                logging.info(f"  SKIPPED: {f}")

        return dark_groups

    def stack_and_save_master_dark(self, group_key: str, fitsinfo_list: list[FitsInfo], process_dir: str, link_dir: str, validate_darks: bool = False) -> None:
        """
        Empile les darks d'un groupe en utilisant Siril et enregistre le master dark
        dans la bibliothèque, en gérant les remplacements selon la date.
        """
        # Récupérer l'objet FitsInfo avec la date la plus récente
        latest_infoFile: FitsInfo | None = None
        for info in fitsinfo_list:
            if info.validData():
                if latest_infoFile is None:
                    latest_infoFile = info
                else:
                    if latest_infoFile.is_equivalent(info, self.temperature_precision):
                        if info.date_obs() > latest_infoFile.date_obs():
                            latest_infoFile = info
                    else:
                        logging.error(f"Inconsistent in group {group_key}. File {info.filepath} has GAIN={info.gain()}, CAMERA={info.camera()}. Skipping group.")
                        return
            else:
                logging.warning(f"Invalid FITS data in {info.filepath}, skipping for date comparison.")
        if latest_infoFile is None:
            logging.warning("No valid DATE-OBS found in group, skipping stacking.")
            return

        # Utilise directement group_key pour le nom du fichier
        os.makedirs(self.dark_library_path, exist_ok=True)
        master_dark_filename = f"{group_key}.fit"
        master_dark_path = os.path.join(self.dark_library_path, master_dark_filename)

        # Déplacer la génération de stack_line avant la vérification du master existant
        cfa_param = "-cfa" if self.siril_cfa else ""
        siril_output_name = "master_dark_temp.fit"

        # Mapping Python -> Siril
        SIRIL_REJECTION_METHOD_MAP = {
            "winsorizedsigma": "w",   # Siril attend "w" ou "winsorized"
            "sigma": "s",
            "minmax": "minmax",
            "percentile": "p",
            "none": "n"
        }
        siril_rejection_method = SIRIL_REJECTION_METHOD_MAP.get(self.siril_rejection_method, self.siril_rejection_method)

        if self.siril_stack_method == "average":
            if self.siril_rejection_method != "none":
                # Empilement par moyenne avec rejet
                stack_line = (
                    f"stack dark rej {siril_rejection_method} {self.siril_rejection_param1} {self.siril_rejection_param2} "
                    f"-norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
                )
            else:
                # Empilement par moyenne sans rejet
                stack_line = (
                    f"stack dark rej n -norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
                )
        else:
            # Empilement médian (ne prend pas de paramètres de rejet)
            stack_line = (
                f"stack dark median -norm={self.siril_output_norm} {cfa_param} -out={siril_output_name}"
            )

        # Mémoriser la ligne de commande pour la stocker dans l'en-tête
        stack_command = stack_line
        
        # --- Check for existing master dark for overwrite logic ---
        existing_master = None
        if os.path.exists(master_dark_path):
            info = FitsInfo(master_dark_path)
            if info.validData():
                existing_master = info
            else:
                logging.warning(f"Cannot read metadata from existing master dark {master_dark_path}. Will be treated as non-existent for comparison.")

        if existing_master and not self.force_recalc:
            # Vérification de la commande de stacking si elle est disponible
            different_stack_cmd = (existing_master.stack_command() is None or 
                                stack_command != existing_master.stack_command())
            
            # Si la commande de stacking est différente, toujours remplacer
            if different_stack_cmd:
                logging.info(
                    f"Existing master dark for {group_key} has different stacking command. Replacing."
                )
                # Pas de 'return' ici pour permettre le remplacement
            else:
                # Évaluation des critères de mise à jour
                current_dark_count = len(fitsinfo_list)
                existing_dark_count = existing_master.ndarks() if existing_master.ndarks() is not None else 0
                
                # Critère 1: Nombre de darks supérieur ou égal au seuil configuré
                meets_threshold = current_dark_count >= self.min_darks_threshold
                
                # Critère 2: Nombre de darks supérieur à celui du master dark existant
                more_darks_than_existing = current_dark_count > existing_dark_count
                
                # Critère 3: Date plus récente (critère obligatoire)
                newer_date = latest_infoFile.date_obs() > existing_master.date_obs()
                
                # Décision de mise à jour: date plus récente ET au moins un critère de nombre satisfait
                should_update = (meets_threshold or more_darks_than_existing) and newer_date
                
                if not should_update:
                    if not newer_date:
                        logging.info(
                            f"Master dark for {group_key} kept unchanged: "
                            f"current darks={current_dark_count}, existing darks={existing_dark_count}, "
                            f"threshold={self.min_darks_threshold}, "
                            f"existing date={existing_master.date_obs().date()}, "
                            f"latest date={latest_infoFile.date_obs().date()}. "
                            f"Date not newer."
                        )
                    else:
                        logging.info(
                            f"Master dark for {group_key} kept unchanged: "
                            f"current darks={current_dark_count}, existing darks={existing_dark_count}, "
                            f"threshold={self.min_darks_threshold}, "
                            f"existing date={existing_master.date_obs().date()}, "
                            f"latest date={latest_infoFile.date_obs().date()}. "
                            f"Date is newer but no dark count criteria met."
                        )
                    return
                else:
                    reasons = []
                    if meets_threshold:
                        reasons.append(f"meets threshold ({current_dark_count} >= {self.min_darks_threshold})")
                    if more_darks_than_existing:
                        reasons.append(f"more darks than existing ({current_dark_count} > {existing_dark_count})")
                    
                    logging.info(
                        f"Updating master dark for {group_key}: newer date ({latest_infoFile.date_obs().date()} > {existing_master.date_obs().date()}) and {', '.join(reasons)}."
                    )
        elif existing_master and self.force_recalc:
            logging.info(
                f"Force recalculation enabled: recreating master dark for {group_key}."
            )
        else:
            logging.info(
                f"No master dark found for {group_key} or unreadable date. Creating new one."
            )

        # Validation des darks seulement si le master dark doit être mis à jour
        rejected_files = []
        if validate_darks:
            logging.info(f"Validating dark files for group {group_key} before stacking...")
            valid_files = []
            for info in fitsinfo_list:
                is_valid, reason = info.is_valid_dark(
                    max_median_adu=self.max_median_adu,
                    max_hot_pixels_percent=self.max_hot_pixels_percent,
                    max_mad_factor=self.max_mad_factor,
                    max_central_dispersion=self.max_central_dispersion,
                    min_median_for_tests=self.min_median_for_tests
                )
                if not is_valid:
                    logging.warning(f"Invalid dark rejected: {info.filepath} - {reason}")
                    rejected_files.append({
                        'filepath': info.filepath,
                        'reason': reason,
                        'statistics': info.get_validation_report()['statistics'] if hasattr(info, 'get_validation_report') else None
                    })
                else:
                    valid_files.append(info)
            
            if len(valid_files) < len(fitsinfo_list):
                logging.info(f"Group {group_key}: {len(fitsinfo_list) - len(valid_files)} invalid dark(s) rejected, {len(valid_files)} valid dark(s) remaining.")
            
            if len(valid_files) < 2:
                logging.warning(f"Group {group_key} contains only {len(valid_files)} valid file(s) after validation. Stacking ignored (Siril requires at least 2).")
                logging.warning(f"Files in group {group_key}:")
                for info in fitsinfo_list:
                    # Vérifier si ce fichier a été rejeté
                    is_rejected = any(rf['filepath'] == info.filepath for rf in rejected_files)
                    status = "❌ REJECTED" if is_rejected else "✅ VALID"
                    logging.warning(f"  {status}: {info.filepath}")
                    if is_rejected:
                        # Afficher la raison du rejet
                        reason = next((rf['reason'] for rf in rejected_files if rf['filepath'] == info.filepath), "Unknown")
                        logging.warning(f"           Reason: {reason}")
                
                # Enregistrer les données même si le stacking est annulé
                if rejected_files:
                    self.validation_data['rejected_files'][group_key] = rejected_files
                return
            
            # Remplacer la liste originale par les fichiers validés
            fitsinfo_list = valid_files

        # Créer les liens symboliques après la validation (ou directement si pas de validation)
        linked_infos = []
        for i, info in enumerate(fitsinfo_list):
            newInfo = info.create_symlink(link_dir, index=i)
            if newInfo:
                linked_infos.append(newInfo)

        if not linked_infos:
            logging.warning(f"No dark files to stack for group {group_key}. Ignored.")
            return

        # Les fichiers dark_files sont maintenant des liens dans link_dir, nommés dark_XXXX.fit
        siril_file_list = [os.path.basename(info.filepath) for info in linked_infos if os.path.exists(info.filepath)]

        if not siril_file_list:
            logging.warning(f"No dark files to stack for group {group_key}. Ignored.")
            return

        siril_script_content = f"""requires 1.2
# Siril script generated by Python to stack darks
cd "{link_dir}"
convert dark -out={process_dir}
cd {process_dir}
{stack_line}
"""
        if not self.siril.run_siril_script(siril_script_content, process_dir):
            logging.error(f"Erreur critique : l'exécution du script Siril a échoué pour le groupe {group_key}. Le répertoire de travail est conservé pour inspection : {process_dir}")
            exit(1)

        temp_master_dark_path = os.path.join(process_dir, siril_output_name)
        if os.path.exists(temp_master_dark_path):
            shutil.move(temp_master_dark_path, master_dark_path)
            logging.info(f"Master dark successfully created/updated: {master_dark_path}")
            
            # Enregistrer les données de traitement pour le rapport
            master_info = {
                'group_key': group_key,
                'master_path': master_dark_path,
                'total_files': len(fitsinfo_list) + len(rejected_files),
                'used_files': len(fitsinfo_list),
                'rejected_files': len(rejected_files)
            }
            self.validation_data['updated_masters'].append(master_info)
            
            if rejected_files:
                self.validation_data['rejected_files'][group_key] = rejected_files
            
            masterDark=FitsInfo(master_dark_path)
            try:
                # Met à jour le header du master dark avec le nombre de fichiers réellement utilisés
                masterDark.set_ndarks(len(linked_infos))
                masterDark.set_stack_command(stack_command)
                masterDark.update_header(latest_infoFile, self.temperature_precision)
                logging.info(f"Header of {master_dark_path} updated with group metadata, stack command, and number of frames ({len(linked_infos)}).")
            except Exception as e:
                logging.error(f"Failed to update FITS header for {master_dark_path}: {e}")
        else:
            logging.error(f"Siril script executed, but master dark '{siril_output_name}' not found in {process_dir}.")


    def read_existing_master_darks(self) -> list[FitsInfo]:
        """
        Parcourt le répertoire de la dark library et lit les entêtes FITS de chaque master dark.
        Retourne une liste d'objets FitsInfo représentant les master darks existants.
        """
        existing_darks = []
        if not os.path.isdir(self.dark_library_path):
            return existing_darks

        for fname in os.listdir(self.dark_library_path):
            if fname.lower().endswith(('.fit', '.fits')):
                fpath = os.path.join(self.dark_library_path, fname)
                try:
                    info = FitsInfo(fpath)
                    if info.validData() and info.is_dark():
                        existing_darks.append(info)
                except Exception as e:
                    logging.warning(f"Impossible de lire l'entête FITS de {fpath}: {e}")
        return existing_darks

    def list_master_darks(self) -> None:
        """
        Liste tous les master darks de la bibliothèque avec leurs caractéristiques
        lues directement depuis les en-têtes FITS.
        """
        print(f"Lecture des master darks dans : {self.dark_library_path}")
        existing_darks = self.read_existing_master_darks()
        
        if not existing_darks:
            logging.info("Aucun master dark trouvé dans la bibliothèque.")
            return
        
        # Calculate base length (sum of fixed width columns and spaces)
        base_length = 25 + 1 + 10 + 1 + 10 + 1 + 8 + 1 + 10 + 1 + 20 + 1 + 8 + 1
        
        # Calculate the maximum length needed for the variable part (command and filename)
        max_variable_length = len("Commande/Fichier")  # Start with header length
        for dark in existing_darks:
            filename = os.path.basename(dark.filepath)
            stack_cmd = dark.stack_command() if hasattr(dark, 'stack_command_value') and dark.stack_command() else "N/A"
            
            # Update max_variable_length if either stack_cmd or filename is longer
            max_variable_length = max(max_variable_length, len(stack_cmd), len(filename) + 2)  # +2 for "→ "
        
        # Calculate total max line length
        max_line_length = base_length + max_variable_length
        
        # Create the separator based on the maximum line length
        separator = "-" * max_line_length
        
        # Affiche un en-tête pour le tableau avec colonne combinée
        header = "{:<25} {:<10} {:<10} {:<8} {:<10} {:<20} {:<8} {:<}".format(
            "Caméra", "Temp (°C)", "Exp (s)", "Gain", "Binning", "Date d'observation", "N darks", "Commande/Fichier"
        )
        
        print(f"\nListe des {len(existing_darks)} master darks disponibles :")
        print(separator)
        print(header)
        print(separator)
        
        for dark in sorted(existing_darks, key=lambda x: (x.exptime(), -x.temperature())):
            # Format des valeurs pour l'affichage
            filename = os.path.basename(dark.filepath)
            date_str = dark.date_obs().strftime("%Y-%m-%d %H:%M:%S") if dark.date_obs() else "N/A"
            n_darks = dark.ndarks() if hasattr(dark, 'ndarks_value') and dark.ndarks() is not None else "N/A"
            
            # Récupérer la commande de stacking (ou N/A)
            stack_cmd = dark.stack_command() if hasattr(dark, 'stack_command_value') and dark.stack_command() else "N/A"
            
            # Ligne principale avec les infos et la commande de stacking
            main_row = "{:<25} {:<10.1f} {:<10.1f} {:<8.1f} {:<10} {:<20} {:<8} {:<}".format(
                dark.camera()[:24], 
                dark.temperature() if dark.temperature() is not None else float('nan'),
                dark.exptime() if dark.exptime() is not None else float('nan'),
                dark.gain() if dark.gain() is not None else float('nan'),
                dark.binning(),
                date_str,
                n_darks,
                stack_cmd
            )
            print(main_row)
            
            # Ligne secondaire avec le nom du fichier (avec indentation)
            file_row = "{:<25} {:<10} {:<10} {:<8} {:<10} {:<20} {:<8} → {}".format(
                "", "", "", "", "", "", "", filename
            )
            print(file_row)
        
        print(separator)
        print()  # Ligne vide à la fin pour améliorer la lisibilité

    def process_all_groups(self, dark_groups, validate_darks: bool = False):
        """
        Traite tous les groupes de darks pour créer des master darks.
        """
        total_groups = len(dark_groups)
        processed_groups = 0
        
        try:
            for group_key, files in dark_groups.items():
                processed_groups += 1
                logging.info(f"Processing group {processed_groups}/{total_groups}: {group_key}")
                
                if len(files) < 2:
                    logging.warning(f"Group {group_key} contains only {len(files)} file(s). Stacking ignored (Siril requires at least 2).")
                    logging.warning(f"File(s) in group {group_key}:")
                    for info in files:
                        logging.warning(f"  - {info.filepath}")
                        if info.date_obs():
                            logging.warning(f"    Date: {info.date_obs().strftime('%Y-%m-%d %H:%M:%S')}")
                    continue

                # Utiliser un sous-répertoire 'process' dans WORK_DIR pour le traitement Siril
                process_dir = os.path.join(self.work_dir, "processs")
                link_dir = os.path.join(process_dir, "link")
                if os.path.exists(process_dir):
                    shutil.rmtree(process_dir)
                os.makedirs(link_dir, exist_ok=True)

                # Passer les fichiers originaux au stacking (les liens seront créés après validation)
                self.stack_and_save_master_dark(group_key, files, process_dir, link_dir, validate_darks)

                # Nettoyer le répertoire process après traitement
                shutil.rmtree(process_dir, ignore_errors=True)
                
        except KeyboardInterrupt:
            logging.warning(f"Traitement interrompu après {processed_groups}/{total_groups} groupes.")
            # Nettoyer le répertoire process en cours si il existe
            process_dir = os.path.join(self.work_dir, "processs")
            if os.path.exists(process_dir):
                shutil.rmtree(process_dir, ignore_errors=True)
            raise  # Re-lancer l'exception pour la gestion au niveau supérieur

    def generate_processing_report(self) -> None:
        """
        Génère un rapport détaillé basé sur les données collectées pendant le traitement.
        """
        print(f"\n=== RAPPORT DE TRAITEMENT ET VALIDATION ===")
        
        updated_masters = self.validation_data['updated_masters']
        rejected_files = self.validation_data['rejected_files']
        
        if not updated_masters and not rejected_files:
            print("Aucun master dark mis à jour et aucun fichier rejeté.")
            print("=== FIN DU RAPPORT ===\n")
            return
        
        # Section master darks mis à jour
        if updated_masters:
            print(f"\n--- MASTER DARKS MIS À JOUR ({len(updated_masters)}) ---")
            for master in updated_masters:
                print(f"✅ {master['group_key']}")
                print(f"   Fichier: {os.path.basename(master['master_path'])}")
                print(f"   Fichiers utilisés: {master['used_files']}/{master['total_files']}")
                if master['rejected_files'] > 0:
                    print(f"   Fichiers rejetés: {master['rejected_files']}")
                print()
        
        # Section fichiers rejetés
        if rejected_files:
            total_rejected = sum(len(files) for files in rejected_files.values())
            print(f"\n--- FICHIERS REJETÉS PAR VALIDATION ({total_rejected}) ---")
            
            for group_key, files in rejected_files.items():
                print(f"\nGroupe: {group_key}")
                for file_info in files:
                    print(f"  ❌ {os.path.basename(file_info['filepath'])}")
                    print(f"     Raison: {file_info['reason']}")
                    if file_info['statistics']:
                        stats = file_info['statistics']
                        print(f"     Médiane: {stats['median']:.1f} ADU, MAD: {stats['mad']:.1f}")
                        print(f"     MAD/median: {stats['mad_ratio']:.3f}, Pixels chauds: {stats['hot_pixels_percent_std']:.2f}%")
        
        # Statistiques globales
        total_masters = len(updated_masters)
        total_rejected = sum(len(files) for files in rejected_files.values())
        total_used = sum(master['used_files'] for master in updated_masters)
        total_processed = sum(master['total_files'] for master in updated_masters)
        
        print(f"\n--- STATISTIQUES GLOBALES ---")
        print(f"Master darks créés/mis à jour: {total_masters}")
        print(f"Fichiers darks traités: {total_processed}")
        print(f"Fichiers darks utilisés: {total_used}")
        print(f"Fichiers darks rejetés: {total_rejected}")
        if total_processed > 0:
            success_rate = (total_used / total_processed) * 100
            print(f"Taux de réussite: {success_rate:.1f}%")
        
        # Section bibliothèque de master darks
        self.list_master_darks()
        
        print("=== FIN DU RAPPORT ===\n")
