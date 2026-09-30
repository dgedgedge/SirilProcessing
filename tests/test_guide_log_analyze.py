"""Régressions du format KStars/PHD2 et des calculs de guidage."""
import importlib.util
import math
from pathlib import Path
import sys

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('guideLogAnalyze', Path(__file__).parents[1] / 'bin/guideLogAnalyze.py')
guide = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guide
spec.loader.exec_module(guide)

HEADER = 'Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,RADuration,RADirection,DECDuration,DECDirection,XStep,YStep,StarMass,SNR,ErrorCode\n'


def row(frame=1, time=1, ra=3, dec=4, mount='Mount', error=0):
    return f'{frame},{time},"{mount}",0,0,{ra},{dec},0,0,100,E,200,S,,,123,25,{error}\n'


def log(tmp_path, body=None, scale='2.0'):
    path = tmp_path / 'guide.txt'
    path.write_text('KStars version 3.8.3. PHD2 log version 2.5.\n'
                    'Guiding Begins at 2026-09-10 23:59:59\n'
                    + (f'Pixel scale = {scale} arc-sec/px\n' if scale else '')
                    + HEADER + (row() if body is None else body), encoding='utf-8')
    return guide.read_log(path)


def test_units_rms_and_direction(tmp_path):
    section = log(tmp_path)[0]
    stats = guide.statistics(section)
    assert stats['rms_ra'] == 6
    assert stats['rms_dec'] == 8
    assert stats['rms_total'] == 10
    assert stats['bias_ra'] == 6
    assert guide.statistics(section, 'px')['rms_total'] == 5
    data = guide.guide_data(section)
    assert data['ra_pulse'].tolist() == [100]
    assert data['dec_pulse'].tolist() == [-200]


def test_rejected_frames_never_reduce_rms(tmp_path):
    section = log(tmp_path, row() + row(2, 2, 0, 0, 'DROP', 7) + row(3, 3, 0, 0, error=1))[0]
    stats = guide.statistics(section)
    assert stats['frames'] == 3 and stats['valid'] == 1 and stats['rejected'] == 2
    assert stats['rms_total'] == 10
    assert np.isnan(guide.guide_data(section)['ra'][1:]).all()


def test_missing_scale_not_assumed(tmp_path):
    section = log(tmp_path, scale=None)[0]
    assert math.isnan(guide.statistics(section)['rms_total'])
    assert guide.statistics(section, 'px')['rms_total'] == 5
    assert section.warnings


def test_multiple_sections_empty_and_calibration(tmp_path):
    sections = log(tmp_path, row() + 'INFO: Server received PAUSE\n'
                   'Calibration Begins at 2026-09-11 00:01:00\n'
                   'Direction,Step,dx,dy,x,y,Dist\nWest,1,1,2,5,6,2.2\nCalibration complete\n'
                   'Guiding Begins at 2026-09-11 00:02:00\nPixel scale = 3 arc-sec/px\n' + HEADER
                   + 'Guiding Ends at 2026-09-11 00:03:00\n')
    assert len(sections) == 3
    assert sections[0].events == [(1., 'INFO: Server received PAUSE')]
    assert sections[0].end is None
    assert sections[1].rows[0]['dx'] == 1
    assert sections[2].rows == [] and sections[2].end.hour == 0
    assert sections[2].scale == 3
    assert guide.statistics(sections[2])['frames'] == 0


def test_bad_rows_and_nonfinite_distances(tmp_path):
    section = log(tmp_path, row() + '2,2,Mount\n' + row(3, 'nan') + row(4, .5)
                  + row(5, 5, 'nan', 4))[0]
    assert len(section.warnings) == 3
    assert len(section.rows) == 2
    assert guide.statistics(section)['valid'] == 1


def test_window_and_rolling_reset(tmp_path):
    section = log(tmp_path, row() + row(2, 2, 0, 0, 'DROP', 7) + row(3, 3, 0, 2)
                  + row(4, 40, 0, 1))[0]
    assert guide.statistics(section, low=3, high=3)['rms_total'] == 4
    values = guide.rolling_rms(guide.guide_data(section))
    assert values[0] == 10 and math.isnan(values[1])
    assert values[2:].tolist() == [4, 2]
    assert guide.statistics(section, low=100)['valid'] == 0


def test_discovery_and_unknown_files(tmp_path):
    log(tmp_path)
    assert guide.discover([str(tmp_path), str(tmp_path / 'guide.txt')]) == [tmp_path / 'guide.txt']
    with pytest.raises(ValueError, match='introuvable'):
        guide.discover([str(tmp_path / 'missing.txt')])
    bad = tmp_path / 'bad.txt'
    bad.write_text('ceci n’est pas un journal')
    with pytest.raises(ValueError, match='aucune section'):
        guide.read_log(bad)


def test_html_dates_selection_and_protect_sources(tmp_path):
    pytest.importorskip('plotly')
    sections = log(tmp_path, row() + 'INFO: <script>alert(1)</script>\n')
    fig = guide.build_figure(sections)
    assert fig.data[0].x[0] == '2026-09-11T00:00:00'
    assert fig.data[0].y[0] == 6
    assert len(fig.layout.updatemenus[0].buttons) == 2
    assert guide.build_figure(sections, True).data[0].x[0] == 1
    target = tmp_path / 'report.html'
    guide.write_report(sections, target)
    html = target.read_text()
    assert 'plotly.js' in html and '<script src=' not in html
    assert '<script>alert(1)</script>' not in html
    with pytest.raises(ValueError, match='source'):
        guide.write_report(sections, sections[0].path)
    link = tmp_path / 'alias.html'
    link.symlink_to(sections[0].path)
    with pytest.raises(ValueError, match='source'):
        guide.write_report(sections, link)
    assert sections[0].path.read_text().startswith('KStars')


def test_gap_does_not_draw_interpolated_guiding():
    x, y = guide.broken_line([0, 1, 40], [2, 3, 4], [0, 1, 40])
    assert x == [0, 1, 40, 40]
    assert math.isnan(y[2])


def test_cli_html_and_no_inputs(tmp_path):
    log(tmp_path)
    assert guide.main([str(tmp_path / 'guide.txt'), '--html', '-o', str(tmp_path / 'report.html')]) == 0
    with pytest.raises(SystemExit) as exc:
        guide.main(['--html'])
    assert exc.value.code == 2


def test_tracking_status_distinguishes_rejection_and_unknown(tmp_path):
    section = log(tmp_path, row() + row(2, 2, mount='DROP', error=7)
                  + row(3, 3, error=1) + row(4, 4, ra='nan')
                  + row(5, 5, mount='AO') + row(6, 6), scale=None)[0]
    assert guide.tracking_status(section).tolist() == [1, 0, 0, .5, .5, 1]
    fig = guide.build_figure([section], relative=True)
    states = [t for t in fig.data if t.yaxis == 'y7']
    assert len(states) == 3
    rejected = next(t for t in states if 'Rejet / erreur' in t.name)
    assert list(rejected.x) == [2, 3]
    assert rejected.mode == 'markers'
    assert fig.layout.yaxis7.range == (-.2, 1.2)
    assert fig.layout.xaxis7.matches == 'x'


def test_empty_tracking_has_no_invented_state(tmp_path):
    section = log(tmp_path, '')[0]
    assert guide.tracking_status(section).size == 0
    assert not guide.build_figure([section]).data


def test_guiding_keeps_previous_calibration_and_html_visibility(tmp_path):
    sections = log(tmp_path, row()
                   + 'Calibration Begins at 2026-09-11 00:01:00\n'
                   + 'Direction,Step,dx,dy,x,y,Dist\nWest,1,1,2,5,6,2.2\nCalibration complete\n'
                   + 'Guiding Begins at 2026-09-11 00:02:00\n' + HEADER + row()
                   + 'Guiding Begins at 2026-09-11 00:03:00\n' + HEADER
                   + 'Calibration Begins at 2026-09-11 00:04:00\n'
                   + 'Direction,Step,dx,dy,x,y,Dist\nNorth,1,3,4,5,6,5\n'
                   + 'Guiding Begins at 2026-09-11 00:05:00\n' + HEADER + row())
    assert sections[0].calibration is None
    assert sections[2].calibration is sections[1]
    assert sections[3].calibration is sections[1]
    assert sections[5].calibration is sections[4]
    assert guide.calibrations_for([sections[1], sections[2], sections[3]]) == [sections[1]]
    fig = guide.build_figure([sections[2], sections[3], sections[5]])
    curves = [(i, t) for i, t in enumerate(fig.data) if t.yaxis == 'y5']
    assert len(curves) == 2
    buttons = fig.layout.updatemenus[0].buttons
    for button in buttons[1:3]:
        visible = button.args[0]['visible']
        assert visible[curves[0][0]] and not visible[curves[1][0]]
    visible = buttons[3].args[0]['visible']
    assert not visible[curves[0][0]] and visible[curves[1][0]]
    target = tmp_path / 'selected.html'
    guide.write_report([sections[2]], target)
    assert 'Calibration précédente' in target.read_text()
    assert 'Calibration complete' in target.read_text()
    # Lire un autre fichier ne reprend jamais la calibration du fichier précédent.
    other = tmp_path / 'other.txt'
    other.write_text('Guiding Begins at 2026-09-11 00:10:00\n' + HEADER + row())
    assert guide.read_log(other)[0].calibration is None
