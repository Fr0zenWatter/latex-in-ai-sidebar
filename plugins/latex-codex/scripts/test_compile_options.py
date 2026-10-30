"""Run: python -B test_compile_options.py (stdlib only; no installs or real TeX calls)."""
import json
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from editor import compiler, engine_flags, compile_tex, missing_tex_package, tex_options, remember_tex_options, bibliography_signature, history_pdf_context
from preferences import Preferences

with patch('editor.tex_tool', side_effect=lambda name: '/tex/' + name):
    assert compiler(r'\documentclass{article}')[0] == 'pdflatex'
    assert compiler('% 中文注释\n' + r'\documentclass{article}')[0] == 'pdflatex'
    assert compiler(r'\documentclass{article}\\% 中文注释')[0] == 'pdflatex'
    for source in (r'\documentclass{ctexart}', r'\usepackage{fontspec}', r'\usepackage{unicode-math}',
                   r'\setmainfont{Example}', r'\documentclass{article}正文'):
        assert compiler(source)[0] == 'xelatex', source
    assert compiler('% !TeX program = xelatex\ntext')[0] == 'xelatex'
    assert compiler('% !TeX program = xelatex\ntext', 'pdflatex')[0] == 'pdflatex'
    for choice in ('cmd.exe', None, [], True):
        try:
            compiler('text', choice)
            raise AssertionError('Invalid engine accepted')
        except ValueError:
            pass

engine_flags.cache_clear()
with patch('editor.subprocess.run', return_value=SimpleNamespace(stdout=b'MiKTeX', stderr=b'')):
    assert engine_flags('mock-tex') == ['--disable-installer']
    assert engine_flags('mock-tex', True) == ['--enable-installer']
with patch('editor.subprocess.run', return_value=SimpleNamespace(stdout=b'TeX Live', stderr=b'')):
    assert engine_flags('other-tex', True) == []
engine_flags.cache_clear()
assert missing_tex_package("! LaTeX Error: File `diagbox.sty' not found.") == 'diagbox.sty'
assert missing_tex_package("! LaTeX Error: File `example.cls' not found.") == 'example.cls'
assert missing_tex_package('Undefined control sequence.') is None

with TemporaryDirectory(prefix='latex options ') as directory:
    root = Path(directory)
    server = SimpleNamespace(main_file=root / 'one.tex', preferences=Preferences(root / 'settings.sqlite3'))
    assert tex_options(server) == {'engine_choice': 'auto', 'allow_installer': False}
    original_context = history_pdf_context(server, server.main_file)
    remember_tex_options(server, 'xelatex', True)
    assert history_pdf_context(server, server.main_file) != original_context, 'History PDF caches include compilation options.'
    assert tex_options(server) == {'engine_choice': 'xelatex', 'allow_installer': True}
    server.main_file = root / 'two.tex'
    assert tex_options(server)['engine_choice'] == 'auto', 'Engine choices belong to individual main files.'
    remember_tex_options(server, 'pdflatex', False)
    server.main_file = root / 'one.tex'
    assert tex_options(server) == {'engine_choice': 'xelatex', 'allow_installer': False}
    remember_tex_options(server, 'auto', False)
    assert len(json.loads(server.preferences.read()['latex-codex-tex-engines'])) == 1
    lookup = root / 'lookup'; lookup.mkdir()
    (lookup / 'main.aux').write_text(r'\bibstyle{external}', encoding='utf-8')
    style = root / 'library' / 'external.bst'; style.parent.mkdir(); style.write_text('Style.', encoding='utf-8')
    with patch('editor.tex_tool', side_effect=lambda name: 'fake-' + name), patch('editor.engine_flags', return_value=['--disable-installer']), \
            patch('editor.subprocess.run', return_value=SimpleNamespace(stdout=str(style).encode())) as run:
        assert bibliography_signature(root / 'main.tex', lookup, {})
        assert run.call_args.args[0] == ['fake-findtexmf', 'external.bst'], 'MiKTeX lookups never invoke its auto-installing kpsewhich.'

    main = root / '中文 paper.tex'
    source = r'\documentclass[review]{siamart220329}\begin{document}Body.\end{document}'
    main.write_text(source, encoding='utf-8')
    error = b"Use of \\cref@override@label@type doesn't match its definition."
    for template, failure, should_repair in ((source, error, True),
            (source.replace('siamart220329', 'article'), error, False),
            (source, b'Undefined control sequence.', False)):
        with TemporaryDirectory(dir=root) as build:
            commands = []
            def run(command, **options):
                commands.append(command)
                assert options['timeout'] == 45
                if command[-1] == str(main):
                    return CompletedProcess(command, 1, failure)
                wrapper = Path(command[-1]).read_text(encoding='utf-8')
                assert '\\input{\\detokenize{' + main.resolve().as_posix() + '}}' in wrapper
                assert '-jobname=' + main.stem in command
                assert 'Body.' not in wrapper, 'The wrapper inputs the original file to preserve SyncTeX lines.'
                return CompletedProcess(command, 0, b'Temporary build succeeded.')
            with patch('editor.compiler', return_value=('pdflatex', 'fake-tex')), \
                    patch('editor.engine_flags', return_value=[]), patch('editor.subprocess.run', side_effect=run):
                ok, log, _ = compile_tex(main, build, template)
            assert ok == should_repair
            assert (len(commands) > 1) == should_repair
            assert main.read_text(encoding='utf-8') == source
            if should_repair:
                assert 'SIAM 2022 counter compatibility' in log and "doesn't match" not in log
print('PASS: automatic/explicit engines, per-file persistence, opt-in MiKTeX flags, missing packages and narrowly scoped SIAM retry without source edits')
