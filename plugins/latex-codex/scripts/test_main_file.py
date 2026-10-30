"""Run: python -B test_main_file.py (stdlib only; no TeX or model calls)."""
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from editor import MainFileChoice, make_server, project_main_file, tex_file_inputs


def document(*inputs):
    return '\n'.join([r'\documentclass{article}', r'\begin{document}', *inputs, r'\end{document}'])


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    sources = {
        'paper.tex': document(r'\input{chapter}', r'\include{shared}'),
        'chapter.tex': r'\input nested.tex',
        'nested.tex': r'\input{chapter}',  # Cyclic input graphs must terminate.
        'letter.tex': document(r'\input{"letter body.tex"}'),
        'letter body.tex': 'A letter.',
        'third.tex': document(r'\input{shared}'),
        'shared.tex': 'Shared preamble.',
        'notes.md': '# Markdown',
    }
    for name, text in sources.items():
        (root / name).write_text(text, encoding='utf-8')
    paper, letter, third = [root / (name + '.tex') for name in ('paper', 'letter', 'third')]
    assert project_main_file(root, root / 'nested.tex', letter) == paper
    assert project_main_file(root, root / 'letter body.tex', paper) == letter
    assert project_main_file(root, letter, paper) == letter
    assert project_main_file(root, root / 'shared.tex', paper) == paper
    assert project_main_file(root, root / 'shared.tex', third) == third
    try:
        project_main_file(root, root / 'shared.tex', letter)
        raise AssertionError('An unrelated current main cannot resolve a shared child.')
    except MainFileChoice as error:
        assert set(error.candidates) == {paper, third}
    (root / 'explicit.tex').write_text('% !TeX root = "third.tex"\nSome text.', encoding='utf-8')
    assert project_main_file(root, root / 'explicit.tex', paper) == third
    assert project_main_file(root, root / 'notes.md', paper) == root / 'notes.md'
    standalone, inputs = tex_file_inputs('\n'.join([
        r'% \documentclass{article} \input{ignored}', r'\\input{escaped}',
        r'\verb|\input{example}|', r'\begin{verbatim}\input{sample}\end{verbatim}',
        r'\begin{comment}\documentclass{article}\end{comment}',
        r'\input % ignored comment', '{actual}', r'\input{\dynamic}',
    ]))
    assert not standalone and inputs == ['actual'], (standalone, inputs)
    print('PASS: standalone mains, transitive/cyclic input/include, shared ownership, explicit TeX root, comments and verbatim')

    with patch('editor.compiler', return_value=('pdflatex', 'unused')):
        server = make_server(paper, main_thread='', preferences_path=root / '.preferences.sqlite3')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base, opener = f'http://127.0.0.1:{server.server_port}', build_opener(ProxyHandler({}))

        def request(route, data=None):
            req = Request(base + route, None if data is None else json.dumps(data).encode(), {'Content-Type': 'application/json'})
            try:
                with opener.open(req, timeout=5) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)

        def state():
            return request('/state')[1]

        def switch(name, **options):
            return request('/source', {'path': name, 'version': state()['version'], **options})

        try:
            old_build = server.build.name
            server.pdf, server.pdf_revision = b'old paper PDF', 'old-paper'
            server.labels, server.citations = {'old': '1'}, {'old': '2'}
            server.page_boxes = [[0, 0, 1, 1]]
            code, active = switch('letter body.tex')
            assert code == 200 and active['main_file'] == str(letter) and active['path'] == str(root / 'letter body.tex')
            assert active['project_root'] == str(root) and active['main_source'].replace('\r\n', '\n') == sources['letter.tex']
            assert server.build.name != old_build and not Path(old_build).exists()
            assert not server.pdf and not server.pdf_revision and not server.labels and not server.citations and not server.page_boxes
            server.pdf, server.pdf_revision = b'letter PDF', 'letter-pdf'
            before = state()
            code, pending = switch('shared.tex')
            assert code == 200 and {item['path'] for item in pending['main_candidates']} == {str(paper), str(third)}
            assert state()['path'] == before['path'] and state()['main_file'] == before['main_file']
            assert server.pdf == b'letter PDF' and server.pdf_revision == 'letter-pdf'
            assert switch('shared.tex', main_file=str(letter))[0] == 400
            third.write_text(document('No shared input.'), encoding='utf-8')
            assert switch('shared.tex', main_file=str(third))[0] == 400, 'Revalidate the graph after a selection dialog.'
            third.write_text(sources['third.tex'], encoding='utf-8')
            code, selected = switch('shared.tex', main_file=str(third))
            assert code == 200 and selected['main_file'] == str(third)
            assert switch('third.tex')[1]['main_file'] == str(third)
            assert switch('shared.tex')[1]['main_file'] == str(third)
            assert switch('nested.tex')[1]['main_file'] == str(paper)
            code, opened = request('/open', {'path': str(letter)})
            assert code == 200 and opened['main_file'] == str(letter) and opened['project_root'] == str(root)
            code, opened = request('/open', {'path': str(root / 'chapter.tex')})
            assert code == 200 and opened['main_file'] == str(paper)
            with patch('editor.compile_tex', return_value=(False, 'Test failure.', 'pdflatex')) as compilation:
                current = state()
                assert request('/compile', {'source': current['source'], 'version': current['version'], 'allow_installer': False})[0] == 200
                assert compilation.call_args.args[0] == paper
                assert compilation.call_args.args[2].replace('\r\n', '\n') == sources['paper.tex']
            assert request('/source', {'path': str(letter), 'version': 'stale'})[0] == 409
            assert state()['main_file'] == str(paper)
            (root / 'explicit.tex').write_text('% !TeX root = ../outside.tex\nText.', encoding='utf-8')
            assert switch('explicit.tex')[0] == 400
            assert state()['main_file'] == str(paper)
            assert all((root / name).read_text(encoding='utf-8') == text for name, text in sources.items())
            print('PASS: source/open auto-switch mains, clean build/PDF state, safe ambiguity choices, correct compile target and stale/path guards')
        finally:
            server.shutdown()
            worker.join()
            server.server_close()
            server.build.cleanup()
