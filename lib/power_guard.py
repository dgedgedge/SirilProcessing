"""Demandes D-Bus temporaires de performance et d’inhibition de veille sous Linux."""

from __future__ import annotations

from importlib import import_module
import logging
import os
from types import TracebackType
from typing import Literal, Protocol, cast


class _UnixFD(Protocol):
    """Descripteur transféré par D-Bus dont l’application devient propriétaire."""

    def take(self) -> int:
        """Transfère la propriété du descripteur Linux."""
        ...


class _Service(Protocol):
    """Méthodes des services optionnels utilisés, avec délai maximal explicite."""

    def Inhibit(
        self, what: str, who: str, why: str, mode: str, *, timeout: float
    ) -> _UnixFD:
        """Acquiert un inhibiteur logind."""
        ...

    def HoldProfile(
        self, profile: str, reason: str, application_id: str, *, timeout: float
    ) -> int:
        """Retourne le jeton dbus.UInt32, sous-classe d’int dont le type doit être conservé."""
        ...

    def ReleaseProfile(self, cookie: int, *, timeout: float) -> None:
        """Libère uniquement le maintien détenu par cet appelant."""
        ...


class _Bus(Protocol):
    """Connexion privée conservée pendant le traitement."""

    def get_object(
        self, bus_name: str, object_path: str, *, introspect: bool
    ) -> object:
        """Obtient un proxy sans introspection réseau supplémentaire."""
        ...

    def close(self) -> None:
        """Ferme la connexion et libère les maintiens associés à l’appelant."""
        ...


class _DBusAPI(Protocol):
    """Façade typée du module dbus-python chargé seulement à l’entrée du contexte."""

    def SystemBus(self, *, private: bool) -> _Bus:
        """Ouvre une connexion au bus système."""
        ...

    def Interface(self, obj: object, dbus_interface: str) -> _Service:
        """Construit l’interface du proxy."""
        ...


class PowerGuard:
    """Maintient des demandes temporaires sans rendre les services obligatoires.

    `with PowerGuard(app_id="SirilProcessing", reason="Traitement"):` demande
    indépendamment l’inhibition logind sleep:idle et le profil performance.
    dbus-python est optionnel. Tout incident de ces services est journalisé et
    laisse le traitement continuer ; ses propres exceptions restent propagées.
    Aucun profil permanent n’est écrit. Le service peut privilégier une demande
    concurrente d’économie d’énergie ou annuler le maintien après un choix manuel.
    Les protections thermiques restent à la charge du système.
    """

    PROFILE_SERVICES = (
        (
            "org.freedesktop.UPower.PowerProfiles",
            "/org/freedesktop/UPower/PowerProfiles",
        ),
        ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"),
    )

    def __init__(
        self,
        app_id: str = "SirilProcessing",
        reason: str = "Traitement astrophotographique",
        *,
        timeout: float = 2.0,
    ) -> None:
        """Configure l’identification et le délai D-Bus, en secondes, sans accès système."""
        if timeout <= 0:
            raise ValueError("Le délai D-Bus doit être positif")
        self.app_id, self.reason, self.timeout = app_id, reason, timeout
        self._bus: _Bus | None = None
        self._fd: int | None = None
        self._profile: _Service | None = None
        self._cookie: int | None = None

    @property
    def sleep_inhibited(self) -> bool:
        """Indique si un descripteur d’inhibition a été acquis."""
        return self._fd is not None

    @property
    def performance_held(self) -> bool:
        """Indique si une demande de maintien du profil a été acquise."""
        return self._cookie is not None

    def __enter__(self) -> PowerGuard:
        """Acquiert les deux protections indépendamment ; indisponibilité non bloquante."""
        if self._bus is not None:
            raise RuntimeError(
                "Un PowerGuard actif ne peut pas être imbriqué avec lui-même"
            )
        try:
            return self._acquire()
        except BaseException:
            # __exit__ n’est pas appelé si l’entrée du contexte est interrompue.
            self.close()
            raise

    def _acquire(self) -> PowerGuard:
        """Acquiert les ressources facultatives ; l’entrée du contexte couvre leur interruption."""
        try:
            api = cast(_DBusAPI, import_module("dbus"))
            self._bus = api.SystemBus(private=True)
        except Exception as error:
            logging.warning(
                "PowerGuard indisponible (D-Bus optionnel) : %s ; traitement poursuivi",
                error,
            )
            return self
        try:
            proxy = self._bus.get_object(
                "org.freedesktop.login1", "/org/freedesktop/login1", introspect=False
            )
            manager = api.Interface(proxy, "org.freedesktop.login1.Manager")
            self._fd = manager.Inhibit(
                "sleep:idle", self.app_id, self.reason, "block", timeout=self.timeout
            ).take()
            os.set_inheritable(self._fd, False)
            logging.info(
                "PowerGuard : mise en veille automatique et inactivité inhibées pendant le traitement"
            )
        except Exception as error:
            logging.warning(
                "PowerGuard : inhibition de veille indisponible : %s ; traitement poursuivi",
                error,
            )
        errors = []
        for name, path in self.PROFILE_SERVICES:
            try:
                proxy = self._bus.get_object(name, path, introspect=False)
                profile = api.Interface(proxy, name)
                cookie = profile.HoldProfile(
                    "performance", self.reason, self.app_id, timeout=self.timeout
                )
                # Sans introspection, dbus-python déduit la signature de ce type.
                # int(cookie) perdrait UInt32 et enverrait « i » au lieu de « u ».
                self._profile, self._cookie = profile, cookie
                logging.info(
                    "PowerGuard : maintien du profil performance demandé (%s)", name
                )
                break
            except Exception as error:
                errors.append(str(error))
        if self._cookie is None:
            logging.warning(
                "PowerGuard : profil performance indisponible : %s ; traitement poursuivi",
                "; ".join(errors),
            )
        return self

    def close(self) -> None:
        """Libère les ressources indépendamment en conservant le type UInt32 du jeton.

        Le jeton retourné par HoldProfile est transmis tel quel à ReleaseProfile
        pour garder la signature D-Bus non signée, même sans introspection.
        Une erreur d’un service n’empêche pas la libération des autres ressources.
        """
        if self._cookie is not None and self._profile is not None:
            try:
                self._profile.ReleaseProfile(self._cookie, timeout=self.timeout)
            except Exception as error:
                logging.warning(
                    "PowerGuard : libération du maintien de profil : %s", error
                )
            finally:
                self._cookie, self._profile = None, None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError as error:
                logging.warning("PowerGuard : fermeture de l’inhibiteur : %s", error)
            finally:
                self._fd = None
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception as error:
                logging.warning("PowerGuard : fermeture du bus : %s", error)
            finally:
                self._bus = None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Libère les demandes sur succès, erreur ou interruption, sans masquer l’erreur."""
        self.close()
        return False
