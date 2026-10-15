"""Acquisition indépendante, dégradation et libération des ressources D-Bus."""

import os
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lib.power_guard import PowerGuard


class UnsignedCookie(int):
    """Jeton typé simulant le contrat UInt32 sans dépendance obligatoire à dbus-python."""


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Simule les services en transférant un vrai descripteur de pipe Linux."""
    read_fd, write_fd = os.pipe()
    manager = Mock()
    manager.Inhibit.return_value = SimpleNamespace(take=lambda: read_fd)
    profile = Mock()
    profile.HoldProfile.return_value = 0
    bus = Mock()
    bus.get_object.side_effect = lambda name, path, **kwargs: name
    api = SimpleNamespace(
        SystemBus=Mock(return_value=bus),
        Interface=lambda proxy, interface: (
            manager if proxy == "org.freedesktop.login1" else profile
        ),
    )
    monkeypatch.setattr("lib.power_guard.import_module", lambda name: api)
    yield SimpleNamespace(
        manager=manager, profile=profile, bus=bus, api=api, fd=read_fd
    )
    for fd in (read_fd, write_fd):
        try:
            os.close(fd)
        except OSError:
            pass


def test_holds_are_released_and_fd_not_inherited(services: SimpleNamespace) -> None:
    """Même le cookie zéro est valide ; le descripteur ne fuit pas dans les enfants."""
    with PowerGuard(reason="test", timeout=1.0) as guard:
        assert guard.sleep_inhibited and guard.performance_held
        assert not os.get_inheritable(services.fd)
        services.api.SystemBus.assert_called_once_with(private=True)
        services.manager.Inhibit.assert_called_once_with(
            "sleep:idle", "SirilProcessing", "test", "block", timeout=1.0
        )
        services.profile.HoldProfile.assert_called_once_with(
            "performance", "test", "SirilProcessing", timeout=1.0
        )
    services.profile.ReleaseProfile.assert_called_once_with(0, timeout=1.0)
    services.bus.close.assert_called_once()
    assert not guard.sleep_inhibited and not guard.performance_held
    with pytest.raises(OSError):
        os.fstat(services.fd)
    guard.close()
    services.bus.close.assert_called_once()


@pytest.mark.parametrize("value", [0, 7, 2**31, 2**32 - 1])
def test_release_preserves_typed_cookie(services: SimpleNamespace, value: int) -> None:
    """Le jeton reste typé jusqu’à la libération, même à zéro ou au-delà des int32."""
    cookie = UnsignedCookie(value)
    services.profile.HoldProfile.return_value = cookie
    with PowerGuard() as guard:
        assert guard.performance_held
    released = services.profile.ReleaseProfile.call_args.args[0]
    assert released is cookie


@pytest.mark.parametrize("value", [0, 7, 2**31, 2**32 - 1])
def test_release_serializes_unsigned_dbus_signature(
    services: SimpleNamespace, value: int
) -> None:
    """La vraie sérialisation dbus-python émet « u », sans connexion à un bus système."""
    dbus = pytest.importorskip("dbus")
    from dbus.lowlevel import MethodCallMessage

    services.profile.HoldProfile.return_value = dbus.UInt32(value)
    with PowerGuard():
        pass
    cookie = services.profile.ReleaseProfile.call_args.args[0]
    message = MethodCallMessage(
        "org.freedesktop.UPower.PowerProfiles",
        "/org/freedesktop/UPower/PowerProfiles",
        "org.freedesktop.UPower.PowerProfiles",
        "ReleaseProfile",
    )
    message.append(cookie)
    assert str(message.get_signature()) == "u"


@pytest.mark.parametrize(
    "failure", [ImportError("module absent"), RuntimeError("bus inaccessible")]
)
def test_missing_dependency_or_bus_does_not_stop_work(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """Le corps du contexte s’exécute même sans bibliothèque ou bus système."""
    monkeypatch.setattr("lib.power_guard.import_module", Mock(side_effect=failure))
    with PowerGuard() as guard:
        assert not guard.sleep_inhibited and not guard.performance_held


@pytest.mark.parametrize("failed_service", ["sleep", "performance"])
def test_services_fail_independently(
    services: SimpleNamespace, failed_service: str
) -> None:
    """Un refus de droits ou profil absent ne supprime pas la protection disponible."""
    if failed_service == "sleep":
        services.manager.Inhibit.side_effect = RuntimeError("AccessDenied")
    else:
        services.profile.HoldProfile.side_effect = RuntimeError("profil absent")
    with PowerGuard() as guard:
        assert guard.sleep_inhibited == (failed_service != "sleep")
        assert guard.performance_held == (failed_service != "performance")
    services.bus.close.assert_called_once()


def test_old_service_name_is_supported(services: SimpleNamespace) -> None:
    """Le nom historique est essayé si le service moderne n’est pas accessible."""
    services.profile.HoldProfile.side_effect = [RuntimeError("service absent"), 7]
    with PowerGuard() as guard:
        assert guard.performance_held
    assert services.bus.get_object.call_args.args[:2] == (
        "net.hadess.PowerProfiles",
        "/net/hadess/PowerProfiles",
    )
    services.profile.ReleaseProfile.assert_called_once_with(7, timeout=2.0)


def test_cleanup_failure_preserves_processing_exception(
    services: SimpleNamespace,
) -> None:
    """L’inhibiteur et le bus sont fermés même si ReleaseProfile échoue."""
    services.profile.ReleaseProfile.side_effect = RuntimeError("service arrêté")
    with pytest.raises(ValueError, match="traitement"):
        with PowerGuard():
            raise ValueError("traitement échoué")
    services.bus.close.assert_called_once()
    with pytest.raises(OSError):
        os.fstat(services.fd)


def test_bus_connection_failure_is_non_blocking(services: SimpleNamespace) -> None:
    """Une bibliothèque installée ne rend pas obligatoire une connexion D-Bus."""
    services.api.SystemBus.side_effect = RuntimeError("connexion refusée")
    with PowerGuard() as guard:
        assert not guard.performance_held
    services.manager.Inhibit.assert_not_called()


def test_interrupt_during_acquisition_releases_inhibitor(
    services: SimpleNamespace,
) -> None:
    """Une interruption avant la fin de __enter__ libère les ressources déjà acquises."""
    services.profile.HoldProfile.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        with PowerGuard():
            pytest.fail("Le corps ne doit pas commencer après une interruption")
    services.bus.close.assert_called_once()
    with pytest.raises(OSError):
        os.fstat(services.fd)
