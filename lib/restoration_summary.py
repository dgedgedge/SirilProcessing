"""Synthèse lisible des contrôles locaux, finaux et du classement des variantes."""

from __future__ import annotations

import argparse
from collections import Counter
import logging
from pathlib import Path

from lib.type_defs import JSONReport, JSONValue


class RestorationSummary:
    """Présente les audits existants sans recalculer ni modifier les verdicts."""

    PARAMETER_NAMES = (
        "quality_mode",
        "deconvolution_min_stars",
        "deconvolution_min_match_fraction",
        "deconvolution_match_radius",
        "deconvolution_min_gain",
        "deconvolution_max_noise",
        "deconvolution_max_rings",
        "denoise_max_blur",
        "quality_min_noise_gain",
        "quality_max_flux_loss",
        "quality_enforce_significance",
        "quality_enforce_diffuse",
        "quality_tile_size",
        "quality_min_tiles",
        "quality_source_sigma",
        "quality_scales",
        "quality_mask_growth",
    )

    DECISIONS = {
        "selected": "RETENUE",
        "reference": "RÉFÉRENCE",
        "local_rejection": "REJET LOCAL",
        "final_rejection": "REJET GLOBAL",
        "technical_error": "ERREUR TECHNIQUE",
        "unavailable": "INDISPONIBLE",
        "unknown": "CONTRÔLE IMPOSSIBLE",
        "no_gain": "NE BAT PAS L’IMAGE INCHANGÉE",
        "lower_score": "SCORE INFÉRIEUR AU MEILLEUR",
        "tie": "ÉGALITÉ, ORDRE D’ÉVALUATION",
        "aborted": "TRAITEMENT INTERROMPU",
    }

    @staticmethod
    def _mapping(value: JSONValue) -> JSONReport:
        """Retourne un dictionnaire JSON, ou un audit vide si la donnée manque."""
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _entries(value: JSONValue) -> list[JSONReport]:
        """Lit les enregistrements d’une liste sans inventer les données absentes."""
        return (
            [item for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )

    @staticmethod
    def _value(value: JSONValue) -> str:
        """Affiche les mesures manquantes comme UNKNOWN et garde les codes bruts."""
        if value is None:
            return "UNKNOWN"
        if isinstance(value, float):
            return f"{value:.8g}"
        if isinstance(value, list):
            return ", ".join(RestorationSummary._value(item) for item in value)
        return str(value)

    @classmethod
    def _criteria(cls, audit: JSONReport) -> JSONReport:
        """Récupère les critères publiés par le moteur ou son verdict de qualité."""
        original = cls._mapping(audit.get("criteria")) or cls._mapping(
            cls._mapping(audit.get("quality")).get("criteria")
        )
        return {
            name: cls._compact(cls._mapping(value)) for name, value in original.items()
        }

    @classmethod
    def _compact(cls, item: JSONReport) -> JSONReport:
        """Conserve le verdict et ses mesures, sans recopier les tableaux par tuile."""
        keys = (
            "status",
            "reason",
            "value",
            "threshold",
            "uncertainty",
            "sample_count",
            "blocking",
            "interval",
            "ratio_interval",
            "noise_ratio",
            "channel",
            "region",
        )
        result = {key: item[key] for key in keys if key in item}
        for key in ("per_channel", "per_region"):
            if key in item:
                result[key] = [cls._compact(child) for child in cls._entries(item[key])]
        return result

    @classmethod
    def _reasons(cls, audit: JSONReport) -> JSONValue:
        """Préserve les motifs, erreurs et indisponibilités de l’audit d’origine."""
        return (
            audit.get("rejection_reasons")
            or audit.get("reason")
            or cls._mapping(audit.get("quality")).get("reasons")
            or audit.get("error")
            or audit.get("availability")
            or []
        )

    @classmethod
    def _decision(
        cls,
        audit: JSONReport,
        selected: bool,
        best: JSONValue,
        *,
        local: bool,
        legacy: bool,
        aborted: bool,
    ) -> str:
        """Distingue rejet scientifique, erreur et simple perte du classement."""
        if selected:
            return "selected"
        status = audit.get("status")
        if status == "reference":
            return "reference"
        if status == "unavailable":
            return "unavailable"
        if status == "failed":
            return "technical_error"
        if status == "no_safe_improvement":
            return "local_rejection" if local else "final_rejection"
        if status == "unknown" or audit.get("error"):
            return "unknown"
        if status != "accepted":
            return "local_rejection" if local else "final_rejection"
        if aborted:
            return "aborted"
        score = audit.get("selection_score")
        if isinstance(score, (int, float)):
            if not legacy and score <= 0:
                return "no_gain"
            if score == best:
                return "tie"
        return "lower_score"

    @classmethod
    def _criterion_lines(cls, criteria: JSONReport, prefix: str = "") -> list[str]:
        """Expose valeurs, limites, effectifs et incertitudes des indicateurs."""
        lines = []
        for name, raw in criteria.items():
            item = cls._mapping(raw)
            if not item:
                continue
            label = f"{prefix}{name}"
            if "status" in item:
                fields = [f"{label}={item['status']}"]
                for key in (
                    "value",
                    "threshold",
                    "uncertainty",
                    "sample_count",
                    "interval",
                    "ratio_interval",
                    "noise_ratio",
                ):
                    if key in item:
                        fields.append(f"{key}={cls._value(item[key])}")
                if "blocking" in item:
                    fields.append("bloquant" if item["blocking"] else "observation")
                if item.get("reason"):
                    fields.append(f"motif={item['reason']}")
                lines.append(" ; ".join(fields))
            for index, channel in enumerate(cls._entries(item.get("per_channel"))):
                lines.extend(
                    cls._criterion_lines({f"canal[{index}]": channel}, f"{label}.")
                )
            for index, region in enumerate(cls._entries(item.get("per_region"))):
                lines.extend(
                    cls._criterion_lines({f"région[{index}]": region}, f"{label}.")
                )
        return lines

    @classmethod
    def _explanation(cls, summary: JSONReport, result: JSONReport) -> list[str]:
        """Explique le choix et les limites mesurées, sans déduire la qualité visuelle.

        Les comptes portent sur les variantes, pas sur les calculs distincts.
        Les régions sont celles du contrôle final retenu ; à défaut, les mesures
        des audits locaux sont regroupées, sans prétendre compter des zones uniques.
        """
        rows = cls._entries(summary.get("rows"))
        counts = Counter(row.get("decision") for row in rows)
        selected = next((row for row in rows if row.get("decision") == "selected"), {})
        chain = summary.get("selected_chain")
        unchanged = (
            not chain
            if summary.get("mode") == "chains"
            else not summary.get("selected_engine")
        )
        lines = ["CONCLUSION EXPLICATIVE"]
        if result.get("status") == "failed":
            lines.append(
                "Le traitement a été interrompu : aucune sortie validée n’a été sélectionnée."
            )
        elif unchanged:
            lines.append(
                "L’image reçue à l’entrée de la restauration est conservée : "
                "aucun candidat n’a satisfait les contrôles et le classement requis. "
                "Cela ne remet pas en cause le gradient ou la photométrie déjà appliqués."
            )
        else:
            label = str(selected.get("label", summary.get("selected_engine")))
            label = label.replace("denoise:", "débruitage avec ").replace(
                "sharpen:", "netteté avec "
            )
            lines.append(
                f"Traitement retenu : {label}. Il a satisfait les contrôles appliqués "
                "et remporté le classement des candidats admissibles."
            )
        lines.append(
            f"Bilan des variantes : {counts['local_rejection']} rejet(s) local(aux), "
            f"{counts['final_rejection']} rejet(s) au contrôle final, "
            f"{counts['technical_error'] + counts['unavailable'] + counts['unknown']} "
            "erreur(s), indisponibilité(s) ou contrôle(s) impossible(s), "
            f"{counts['lower_score'] + counts['tie'] + counts['no_gain']} "
            "candidat(s) admissible(s) non retenu(s) au classement."
        )
        if summary.get("mode") == "chains":
            lines.append(
                "Les chaînes admissibles sont comparées à la même image après gradient/photométrie. "
                "Le score combine les gains et pénalités mesurés ; ce n’est pas un pourcentage de qualité."
            )
        else:
            lines.append(
                "Cette comparaison concerne uniquement l’étape courante, pas les chaînes complètes."
            )
        audits = (
            [selected]
            if not unchanged and selected
            else cls._entries(result.get("operations")) or rows
        )
        diffuse = [
            cls._mapping(cls._criteria(audit).get("diffuse")) for audit in audits
        ]
        regions = [
            region
            for item in diffuse
            for region in cls._entries(item.get("per_region"))
        ]
        if regions:
            states = Counter(region.get("status") for region in regions)
            lines.append(
                f"Signal diffus : {len(regions)} mesure(s) de régions ; "
                f"{states['PASS']} conforme(s), {states['FAIL']} hors tolérance, "
                f"{states['UNKNOWN']} non concluante(s)."
            )
            reasons = Counter(
                region.get("reason")
                for region in regions
                if region.get("status") == "UNKNOWN"
            )
            if reasons["NO_LOCAL_SKY"]:
                lines.append(
                    f"Pour {reasons['NO_LOCAL_SKY']} mesure(s), NO_LOCAL_SKY signifie "
                    "qu’aucune zone de ciel locale suffisamment fiable ne permet d’estimer "
                    "le fond autour de la structure diffuse. Sa variation de flux ne peut donc pas être évaluée."
                )
            if reasons["LOW_DIFFUSE_SNR"]:
                lines.append(
                    f"Pour {reasons['LOW_DIFFUSE_SNR']} mesure(s), le signal diffus est trop faible "
                    "par rapport au bruit pour conclure sur sa conservation."
                )
        if any(item.get("status") == "UNKNOWN" for item in diffuse) or any(
            region.get("status") == "UNKNOWN" for region in regions
        ):
            lines.append(
                "UNKNOWN signifie mesure non concluante : cela ne prouve ni une dégradation "
                "ni la conservation du signal astrophysique. Le choix retenu ne valide donc "
                "pas les régions que ces contrôles n’ont pas pu mesurer."
            )
        if any(item.get("blocking") is False for item in diffuse):
            lines.append(
                "Le contrôle du signal diffus est ici diagnostique : il n’élimine pas les candidats."
            )
        return lines

    @classmethod
    def write(
        cls,
        result: JSONReport,
        report: Path,
        *,
        mode: str,
        operation: str = "",
        legacy: bool = False,
        args: argparse.Namespace | None = None,
    ) -> None:
        """Ajoute une synthèse JSON et écrit/logue son équivalent texte français.

        En mode chains, chaque version expose ses étapes locales et son contrôle
        final face à la référence commune. En mode isolated, les moteurs sont
        comparés sur l’entrée de l’étape ; aucune comparaison globale n’est
        annoncée. Un candidat accepté mais moins bien classé n’est pas présenté
        comme un échec scientifique. Les états UNKNOWN restent explicitement
        distincts des erreurs et des FAIL. Les détails par région diffuse sont
        journalisés en DEBUG ; le texte et le JSON conservent tous les détails.
        """
        rows = []
        if mode == "chains":
            audits = cls._entries(result.get("variants"))
            selected_chain = result.get("selected_chain", [])
            for index, audit in enumerate(audits, start=1):
                selected = audit.get("chain", []) == selected_chain
                local = audit.get("rejection_stage") == "local"
                decision_audit = audit
                if local:
                    steps = cls._entries(audit.get("steps"))
                    if steps and steps[-1].get("status") in {"failed", "unavailable"}:
                        decision_audit = dict(audit, status=steps[-1]["status"])
                rows.append(
                    dict(
                        number=index,
                        label=" → ".join(audit.get("chain", [])) or "image inchangée",
                        decision=cls._decision(
                            decision_audit,
                            selected,
                            result.get("selection_score"),
                            local=local,
                            legacy=False,
                            aborted=False,
                        ),
                        status=audit.get("status"),
                        selection_score=audit.get("selection_score"),
                        reasons=cls._reasons(audit),
                        criteria=cls._criteria(audit),
                        steps=audit.get("steps", []),
                        rejection_stage=audit.get("rejection_stage"),
                    )
                )
        else:
            evaluations = cls._mapping(result.get("evaluations")) or {"siril": result}
            selected = result.get("selected_engine")
            if not result.get("evaluations") and result.get("status") == "accepted":
                selected = "siril"
            for index, (name, raw) in enumerate(evaluations.items(), start=1):
                audit = cls._mapping(raw)
                rows.append(
                    dict(
                        number=index,
                        label=f"{operation}:{name}",
                        status=audit.get("status"),
                        decision=cls._decision(
                            audit,
                            name == selected,
                            result.get("selection_score"),
                            local=True,
                            legacy=legacy,
                            aborted=result.get("status") == "failed",
                        ),
                        selection_score=audit.get("selection_score"),
                        reasons=cls._reasons(audit),
                        criteria=cls._criteria(audit),
                        steps=[],
                        attempts=audit.get("attempts", []),
                    )
                )
        summary = dict(
            mode=mode,
            rows=rows,
            selected_chain=result.get("selected_chain"),
            selected_engine=selected if mode != "chains" else None,
            selection_score=result.get("selection_score"),
        )
        summary["parameters"] = {
            name: getattr(args, name)
            for name in cls.PARAMETER_NAMES
            if hasattr(args, name)
        }
        if mode == "chains":
            summary["operation_count"] = result.get("operation_count")
            summary["reference"] = result.get("image_path")
        result["summary"] = summary
        path = report.with_name(report.stem + "_summary.txt")
        result["summary_path"] = str(path)
        lines = [
            "SYNTHÈSE DES ESSAIS — "
            + ("CHAÎNES COMPLÈTES" if mode == "chains" else "ÉTAPE ISOLÉE"),
            "PASS/FAIL/UNKNOWN décrivent les indicateurs ; les motifs de rejet expliquent les éliminations.",
            "Variations et seuils relatifs en fractions (0.02 = 2 %). UNKNOWN n’est pas zéro.",
        ]
        if mode == "chains":
            lines.append(
                f"{len(rows)} variantes ; {result.get('operation_count')} calculs distincts ; référence commune : {result.get('image_path')}"
            )
        else:
            lines.append(
                "Pas de classement global des chaînes. Activer --restoration-compare-chains pour les comparer."
            )
        lines.append(
            "Paramètres configurés (les critères des moteurs indiquent leurs seuils effectifs) :"
        )
        lines.extend(
            f"    {name}={cls._value(value)}"
            for name, value in summary["parameters"].items()
        )
        operations = cls._entries(result.get("operations"))
        for row in rows:
            lines.append(
                f"{row['number']:02d}. {row['label']} — {cls.DECISIONS[row['decision']]} ; statut={row['status']} ; score={cls._value(row['selection_score'])}"
            )
            if row.get("reasons"):
                lines.append(f"    Motifs : {cls._value(row['reasons'])}")
            for step in cls._entries(row.get("steps")):
                number = step.get("operation_id")
                detail = (
                    operations[number - 1]
                    if isinstance(number, int) and 0 < number <= len(operations)
                    else step
                )
                reused = "réutilisé" if step.get("reused") else "calculé"
                lines.append(
                    f"    Local {step.get('operation')}:{step.get('engine')} : {step.get('status')} ; calcul #{number} {reused} ; audit={detail.get('report_path', 'UNKNOWN')}"
                )
                lines.extend(
                    "        " + line
                    for line in cls._criterion_lines(cls._criteria(detail))
                )
                if cls._reasons(detail):
                    lines.append(f"        Motifs : {cls._value(cls._reasons(detail))}")
                lines.extend(cls._attempt_lines(detail))
            if row.get("criteria"):
                if mode == "chains":
                    lines.append("    Contrôle global contre la référence commune :")
                lines.extend(
                    "        " + line
                    for line in cls._criterion_lines(cls._mapping(row["criteria"]))
                )
            lines.extend(cls._attempt_lines(row))
        chosen = (
            result.get("selected_chain")
            if mode == "chains"
            else summary.get("selected_engine")
        )
        lines.append(
            f"Choix final : {cls._value(chosen) if chosen else 'image inchangée'} ; score={cls._value(result.get('selection_score'))} ; sortie={result.get('output_image')}"
        )
        summary["explanation"] = cls._explanation(summary, result)
        lines.extend(summary["explanation"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for line in lines:
            if line.lstrip().startswith("diffuse.région["):
                logging.debug("%s", line)
            else:
                logging.info("%s", line)

    @classmethod
    def _attempt_lines(cls, audit: JSONReport) -> list[str]:
        """Conserve aussi les essais internes Siril, sans les compter comme chaînes."""
        lines = []
        for attempt in cls._entries(audit.get("attempts")):
            quality = cls._mapping(attempt.get("quality"))
            lines.append(
                f"        Essai interne #{attempt.get('number')} ({attempt.get('label', 'UNKNOWN')}) : pas={cls._value(attempt.get('step'))} ; accepté={quality.get('accepted', 'UNKNOWN')} ; motifs={cls._value(cls._reasons(attempt))}"
            )
            lines.extend(
                "            " + line
                for line in cls._criterion_lines(cls._criteria(attempt))
            )
        return lines
