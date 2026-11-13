import pytest

from lib.cpu_config import CpuConfig
from lib.siril import Siril


@pytest.fixture(autouse=True)
def restore_cpu_config(monkeypatch):
    monkeypatch.setattr(CpuConfig, '_limit', None)


@pytest.mark.parametrize('count, expected', [(16, 15), (4, 3), (1, 1), (None, 1)])
def test_automatic_limit(monkeypatch, count, expected):
    monkeypatch.setattr('lib.cpu_config.os.cpu_count', lambda: count)
    assert CpuConfig.get_limit() == expected


def test_global_override_and_reset(monkeypatch):
    monkeypatch.setattr('lib.cpu_config.os.cpu_count', lambda: 16)
    CpuConfig.configure(8)
    assert CpuConfig.get_limit() == 8
    CpuConfig.configure(32)
    assert CpuConfig.get_limit() == 16
    CpuConfig.configure()
    assert CpuConfig.get_limit() == 15


@pytest.mark.parametrize('limit', [0, -1, True, 2.5, '4'])
def test_invalid_limit(limit):
    with pytest.raises(ValueError):
        CpuConfig.configure(limit)


@pytest.mark.parametrize('initial', [[], ['1.2'], ['1.2', '1.3']])
def test_existing_siril_instance_uses_global_limit(tmp_path, monkeypatch, initial):
    """Chaque préparation de lot prend la limite CPU courante, sans état global de session."""
    from lib.siril_api import command
    monkeypatch.setattr('lib.cpu_config.os.cpu_count', lambda: 16)
    for limit in (8, 3):
        CpuConfig.configure(limit)
        operations = Siril.prepare_commands([*(command('requires', value) for value in initial),
                                              command('close')])
        assert sum(operation.name == 'requires' for operation in operations) == 1
        assert operations[0].arguments == ('1.4.4',)
        assert operations[1].arguments == (str(limit),)
        assert operations[-1].name == 'close'
