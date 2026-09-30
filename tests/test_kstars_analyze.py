"""Régressions du lecteur KStars et du rapport autonome."""
import importlib.util
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location('kstars_analyze', Path(__file__).parents[1] / 'bin/kstarsAnalyze.py')
analyze = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = analyze
spec.loader.exec_module(analyze)


def session(tmp_path, body):
    path = tmp_path / 'session.analyze'
    path.write_text('#KStars\nAnalyzeStartTime,2026-09-12 23:59:00.000,CEST\n' + body, encoding='utf-8')
    return analyze.read_session(path)


def test_capture_abort_and_incomplete(tmp_path):
    s = session(tmp_path, 'CaptureStarting,1,30,L\nCaptureComplete,32,30,L,2.5,image.fits,42,700,0.4\n'
                'CaptureStarting,40,30,R\nCaptureAborted,45,30\nCaptureStarting,50,30,G\nTemperature,55,12\n')
    spans = analyze.intervals(s)
    assert [(r,a,b,state) for r,a,b,state,_ in spans] == [
        ('Acquisition',1,32,'Terminée'), ('Acquisition',40,45,'Abandonnée'),
        ('Acquisition',50,55,'Acquisition')]
    assert 'inconnue' in spans[-1][-1]
    assert analyze.measurements(s)['HFR','px'] == [(32,2.5)]


def test_unavailable_old_fields_and_invalid_lines(tmp_path):
    s = session(tmp_path, 'CaptureComplete,2,30,L,-1,image.fits\nGuideStats,3,1,-2,50,-40,123,456,7\n'
                'Temperature,nan,12\nmalformed\nTemperature,4,nan\nUnknownFutureEvent,5,text\n')
    m = analyze.measurements(s)
    assert ('HFR','px') not in m
    assert m['SNR','rapport'] == [(3,123)]
    assert m['Erreur DEC','arcsec'] == [(3,-2)]
    assert s.duration == 5
    assert len(s.warnings) == 3


def test_separate_devices(tmp_path):
    s = session(tmp_path, 'CaptureStarting,1,30,L,A\nCaptureStarting,2,30,R,B\n'
                'CaptureComplete,31,30,L,2,a.fits,1,1,0.4,A\nCaptureAborted,32,30,B\n')
    assert [(a,b,state) for _,a,b,state,_ in analyze.intervals(s)] == [(1,31,'Terminée'),(2,32,'Abandonnée')]


def test_local_states_and_midnight(tmp_path):
    pytest.importorskip('plotly')
    s = session(tmp_path, 'MountState,1,à l’arrêt\nMountState,61,Suivi\nTemperature,62,12\n')
    assert analyze.intervals(s)[0][1:4] == (1,61,'à l’arrêt')
    fig = analyze.build_figure([s])
    assert fig.data[-1].x[0] == '2026-09-13T00:00:02'
    assert fig.layout.xaxis2.matches is not None
    relative = analyze.build_figure([s],True)
    assert relative.data[-1].x[0] == 62


def test_empty_session_html_and_source_protection(tmp_path):
    pytest.importorskip('plotly')
    s = session(tmp_path,'')
    out = tmp_path / 'report.html'
    analyze.write_report([s],out)
    html = out.read_text()
    assert 'plotly.js' in html
    assert '<script src=' not in html
    assert 'session.analyze' in html
    with pytest.raises(SystemExit): analyze.main([str(s.path),'-o',str(s.path)])
    assert s.path.read_text().startswith('#KStars')


def test_missing_header(tmp_path):
    p = tmp_path / 'bad.analyze'
    p.write_text('Temperature,1,10\n')
    with pytest.raises(ValueError,match='AnalyzeStartTime'): analyze.read_session(p)


@pytest.mark.parametrize('capture', [
    'CaptureStarting,1,30,L\n',
    'CaptureComplete,32,30,L,2.5,image.fits\n',
    'CaptureAborted,45,30\n',
])
def test_cli_with_captures(tmp_path, monkeypatch, capture):
    session(tmp_path, 'Temperature,1,12\n')
    capture_path = tmp_path / 'capture.analyze'
    capture_path.write_text('AnalyzeStartTime,2026-09-13 00:00:00,CEST\n' + capture)
    selected = []
    monkeypatch.setattr(analyze, 'write_report', lambda sessions, *_: selected.extend(sessions))
    assert analyze.main([str(tmp_path), '--with-captures']) == 0
    assert [s.path.name for s in selected] == ['capture.analyze']
    selected.clear()
    assert analyze.main([str(tmp_path)]) == 0
    assert len(selected) == 2


def test_cli_with_captures_no_match_preserves_report(tmp_path, capsys):
    s = session(tmp_path, 'GuideStats,1,1,2,0,0,10,20,3\n')
    output = tmp_path / 'existing.html'
    output.write_text('Rapport existant')
    with pytest.raises(SystemExit) as exc:
        analyze.main([str(s.path), '--with-captures', '-o', str(output)])
    assert exc.value.code == 2
    assert 'Aucun fichier ne contient' in capsys.readouterr().err
    assert output.read_text() == 'Rapport existant'


def test_rms_dedicated_axis_and_unscaled_optional_snr(tmp_path):
    pytest.importorskip('plotly')
    s = session(tmp_path, 'GuideStats,1,1,2,10,20,1234.5,9876,42\nTemperature,2,12\n')
    fig = analyze.build_figure([s])
    snr = next(t for t in fig.data if '· SNR ' in t.name)
    sky = next(t for t in fig.data if '· Fond du ciel ' in t.name)
    assert snr.yaxis == 'y4'
    assert snr.visible == 'legendonly'
    rms = next(t for t in fig.data if '· RMS ' in t.name)
    assert rms.yaxis == 'y3'
    assert rms.visible is True
    assert sky.yaxis == 'y4'
    assert list(snr.y) == [1234.5]
    assert fig.layout.yaxis3.title.text == 'RMS (″)'
    assert 'xaxis6.range' in fig.layout.updatemenus[0].buttons[1].args[1]


def test_kstars_rms_window_gap_and_invalid_samples(tmp_path):
    import math
    body = ''.join(f'GuideStats,{i},0,0,0,0,1000,0,1\n' for i in range(1, 41))
    body += 'GuideStats,41,4,0,0,0,1000,0,1\n'
    body += 'GuideStats,42,nan,0,0,0,1000,0,1\n'
    body += 'GuideStats,80,3,4,0,0,1000,0,1\n'
    body += 'GuideStats,81,3,4,0,0,1000,0,1\n'
    values = analyze.guiding_rms(session(tmp_path, body))
    # 39 zeros + 4: sample variance = 0.4, after the oldest point expires.
    assert values[40][1] == pytest.approx(math.sqrt(0.4))
    assert math.isnan(values[41][1])
    assert values[-2:] == [(80, 0), (81, 0)]
    assert len(values) == 44


def test_kstars_rms_two_axes(tmp_path):
    s = session(tmp_path, 'GuideStats,1,1,2,0,0,1000,0,1\n'
                'GuideStats,2,3,6,0,0,1000,0,1\n')
    assert analyze.measurements(s)['RMS', 'arcsec'][1][1] == pytest.approx(10**0.5)


def test_native_time_slider_preserves_zoom_on_redraw(tmp_path, monkeypatch):
    pytest.importorskip('matplotlib')
    tk = pytest.importorskip('tkinter')
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'bin'))
    from kstarsAnalyzeGui import AnalyzeWindow
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('Affichage graphique indisponible')
    try:
        s = session(tmp_path, 'Temperature,100,12\n')
        app = AnalyzeWindow(root, [s], relative=True)
        root.update()
        app.time_slider.set_val((20, 60))
        assert all(ax.get_xlim() == (20, 60) for ax in app.figure.axes)
        app.draw()
        root.update()
        assert app.time_window == (20, 60)
        app.figure.axes[0].set_xlim(30, 50)
        assert tuple(app.time_slider.val) == (30, 50)
        app.reset_time()
        assert app.time_window == (0, 100)
        app.time_slider.set_val((50, 50))
        assert app.time_window == (0, 100)
    finally:
        root.destroy()


def test_visible_y_limits_exclude_outliers_and_keep_clipped_lines(monkeypatch):
    pytest.importorskip('matplotlib')
    pytest.importorskip('tkinter')
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'bin'))
    from matplotlib.figure import Figure
    from kstarsAnalyzeGui import AnalyzeWindow
    from types import SimpleNamespace
    fig = Figure()
    timeline, ax, gap_ax = fig.subplots(3)
    ax.plot([0, 10, 20, 30], [1000, 1, 2, 1000])
    gap_ax.plot([0, 10, 20], [1, float('nan'), 3])
    original_gap_limits = gap_ax.get_ylim()
    app = SimpleNamespace(figure=fig)
    AnalyzeWindow.autoscale_visible_y(app, 10, 20)
    assert ax.get_ylim() == pytest.approx((.95, 2.05))
    AnalyzeWindow.autoscale_visible_y(app, 12, 18)
    assert ax.get_ylim() == pytest.approx((1.17, 1.83))
    # No interpolation through a missing interval.
    assert gap_ax.get_ylim() == pytest.approx((2.85, 3.15))
    AnalyzeWindow.autoscale_visible_y(app, 0, 30)
    assert ax.get_ylim()[1] > 1000
    ax.clear()
    ax.plot([0, 20], [0, 1000], linestyle='None', marker='.')
    before = ax.get_ylim()
    AnalyzeWindow.autoscale_visible_y(app, 5, 15)
    assert ax.get_ylim() == before


def test_native_panels_follow_selected_available_measurements(tmp_path, monkeypatch):
    pytest.importorskip('matplotlib')
    tk = pytest.importorskip('tkinter')
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'bin'))
    from kstarsAnalyzeGui import AnalyzeWindow
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('Affichage graphique indisponible')
    try:
        s = session(tmp_path, 'Temperature,10,12\nTemperature,100,14\n'
                    'MountCoords,100,20,30,40,50,0\n')
        app = AnalyzeWindow(root, [s], relative=True)
        root.update()
        assert len(app.figure.axes) == 2  # Temperature and altitude; no empty timeline.
        old_height = app.figure.axes[0].get_position().height
        app.time_slider.set_val((20, 60))
        app.metrics['Altitude'].set(False)
        app.draw()
        root.update()
        assert len(app.figure.axes) == 1
        assert app.figure.axes[0].get_position().height > old_height
        assert app.time_window == (20, 60)
        assert app.figure.axes[0].get_xlim() == (20, 60)
        assert app.figure.axes[0].get_ylim()[0] > 12  # First remaining axis still autoscales.
        app.metrics['SNR'].set(True)  # Unavailable: no extra panel.
        app.draw()
        assert len(app.figure.axes) == 1
        app.metrics['Température'].set(False)
        app.draw()
        assert app.figure.axes[0].texts[0].get_text() == 'Aucune mesure activée disponible'
        app.metrics['Altitude'].set(True)
        app.draw()
        assert len(app.figure.axes) == 1
        assert app.figure.axes[0].get_title(loc='left') == 'Monture (°)'
        assert app.time_window == (20, 60)
    finally:
        root.destroy()


@pytest.mark.parametrize('terminal', ['Succès', 'Terminé', 'Échec', 'Interrompu',
                                     'Suspendu', "à l'arrêt", 'à l’arrêt',
                                     'Complete', 'Successful', 'Failed', 'Aborted', 'Idle'])
def test_alignment_terminal_states_do_not_extend_to_end(tmp_path, terminal):
    s = session(tmp_path, f'AlignState,10,En cours\nAlignState,20,{terminal}\nTemperature,1000,12\n')
    spans = analyze.intervals(s)
    assert len(spans) == 1
    assert spans[0][:4] == ('Alignement', 10, 20, 'En cours')
    assert terminal in spans[0][4]


def test_alignment_multiple_phases_and_unfinished_attempt(tmp_path):
    s = session(tmp_path, 'AlignState,1,En cours\nAlignState,2,Succès\n'
                'AlignState,3,Synchronisation\nAlignState,4,Pointage\n'
                'AlignState,5,En cours\nAlignState,6,Succès\nAlignState,7,Terminé\n'
                'AlignState,10,En cours de rotation\nAlignState,12,à l’arrêt\n'
                'AlignState,15,En cours\nTemperature,20,12\n')
    spans = analyze.intervals(s)
    assert [(a,b) for _,a,b,_,_ in spans] == [(1,2),(3,4),(4,5),(5,6),(10,12),(15,20)]
    assert 'fin réelle inconnue' in spans[-1][-1]


def test_mount_colors_follow_states_not_coordinates(tmp_path):
    s = session(tmp_path, 'MountState,1,Parquée\nMountCoords,5,10,20,30,40,0\n'
                'MountState,10,En cours de suivi\nMountCoords,15,20,30,40,50,0\n'
                'MountState,20,à l’arrêt\nMountCoords,25,30,40,50,60,0\n')
    spans = analyze.intervals(s)
    assert [(a,b,state) for _,a,b,state,_ in spans] == [
        (1,10,'Parquée'), (10,20,'En cours de suivi'), (20,25,'à l’arrêt')]
    assert analyze.timeline_color('Monture','Parquée') != analyze.timeline_color('Monture','En cours de suivi')
    assert analyze.mount_style('à l’arrêt') == analyze.mount_style('Idle')
    assert analyze.mount_style('En cours de suivi') == analyze.mount_style('Tracking')
    assert analyze.mount_style('MOUNT_PARKED') == analyze.mount_style('Parquée')
    states = ['à l’arrêt','En cours de suivi','En mouvement','Pointage','Parcage','Parquée','Erreur']
    assert len({analyze.mount_style(state)[1] for state in states}) == len(states)
    assert analyze.mount_style('Unknown state')[0] == 'État inconnu'


def test_mount_coordinates_alone_do_not_create_activity(tmp_path):
    s = session(tmp_path, 'MountCoords,1,10,20,30,40,0\nMountCoords,10,20,30,40,50,0\n')
    assert analyze.intervals(s) == []
    assert analyze.measurements(s)['Altitude','°'] == [(1,40),(10,50)]


def test_scheduler_timeline_completed_and_unfinished_jobs(tmp_path):
    s = session(tmp_path, 'SchedulerJobStart,10,M 33\nSchedulerJobEnd,50,M 33,twilight\n'
                'SchedulerJobStart,70,M 42\nTemperature,100,12\n')
    spans = analyze.intervals(s)
    assert [(row,a,b,state) for row,a,b,state,_ in spans] == [
        ('Scheduler',10,50,'M 33'), ('Scheduler',70,100,'M 42')]
    assert 'Tâche : M 33' in spans[0][4]
    assert 'Fin : twilight' in spans[0][4]
    assert 'fin réelle inconnue' in spans[1][4]
    assert 'Scheduler' in analyze.ROWS
    assert analyze.timeline_color('Scheduler', 'M 33')


def test_mount_legend_has_reserved_space(tmp_path, monkeypatch):
    pytest.importorskip('matplotlib')
    tk = pytest.importorskip('tkinter')
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'bin'))
    from kstarsAnalyzeGui import AnalyzeWindow
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('Affichage graphique indisponible')
    try:
        states = ['à l’arrêt','En cours de suivi','En mouvement','Pointage','Parcage','Parquée','Erreur']
        s = session(tmp_path, ''.join(f'MountState,{i*10},{state}\n' for i,state in enumerate(states))
                    + 'Temperature,80,12\n')
        app = AnalyzeWindow(root, [s], relative=True)
        for width in (1450, 1000):
            root.geometry(f'{width}x950')
            root.update()
            app.canvas.draw()
            renderer = app.canvas.get_renderer()
            legend = app.figure.legends[0].get_window_extent(renderer)
            timeline = app.figure.axes[0]
            assert legend.y0 > timeline.get_window_extent(renderer).y1 + 15
            assert legend.y0 > timeline.title.get_window_extent(renderer).y1
            assert legend.width > app.figure.bbox.width * .85
            assert timeline.get_legend() is None
        app.time_slider.set_val((10, 30))
        assert app.time_window == (10, 30)
    finally:
        root.destroy()


def test_capture_colors_alternate_and_abort_stays_red(tmp_path):
    s = session(tmp_path, 'CaptureStarting,0,10,L\nCaptureComplete,10,10,L,2,a.fits\n'
                'Temperature,10,12\nCaptureStarting,10.01,10,L\nCaptureComplete,20,10,L,2,b.fits\n'
                'CaptureStarting,20.01,10,L\nCaptureAborted,25,10\n'
                'CaptureStarting,25.01,10,L\nCaptureComplete,35,10,L,2,c.fits\n'
                'CaptureStarting,35.01,10,L\nTemperature,40,12\n')
    spans = list(analyze.colored_intervals(analyze.intervals(s)))
    assert [span[-1] for span in spans] == ['#22d3ee','#fb923c','#f87171','#fb923c','#22d3ee']
    assert 'fin réelle inconnue' in spans[-1][-2]
    pytest.importorskip('plotly')
    fig = analyze.build_figure([s], relative=True)
    bars = [t for t in fig.data if t.type == 'bar']
    colors_by_start = {float(start): trace.marker.color for trace in bars for start in trace.base}
    assert colors_by_start == {span[1]: span[-1] for span in spans}


@pytest.mark.parametrize('filename,kind', [
    ('/images/Light/a.fits','Light'), ('/images/FLAT/a.fits','Flat'),
    (r'C:\images\dark\a.fits','Dark'), ('/images/darkness/a.fits','Autre'),
    ('/images/light.fits','Autre'), ('/images/light/flat/a.fits','Flat'),
])
def test_capture_type_directory_matching(filename, kind):
    assert analyze.capture_type(filename) == kind


def test_capture_palettes_alternate_per_type(tmp_path):
    body = ''
    kinds = ['Light','Flat','Dark','Light','Flat','Dark']
    for i, kind in enumerate(kinds):
        body += f'CaptureStarting,{i*10},10,L\nCaptureComplete,{i*10+9},10,L,2,/images/{kind}/a.fits\n'
    body += 'CaptureStarting,60,10,L\nCaptureAborted,65,10\n'
    s = session(tmp_path, body)
    spans = list(analyze.colored_intervals(analyze.intervals(s)))
    assert [span[-1] for span in spans] == [
        analyze.CAPTURE_PALETTES[kind][i//3] for i,kind in enumerate(kinds)] + ['#f87171']
    assert spans[0][3] == 'Terminée · Light'
