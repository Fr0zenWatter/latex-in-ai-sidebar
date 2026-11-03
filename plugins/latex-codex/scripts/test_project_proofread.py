"""Run: python -B test_project_proofread.py (local TeX and Node; temporary project only)."""
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from unittest.mock import patch

from editor import compile_source_snapshot, document_source_files, make_server, snapshot, tex_options


with tempfile.TemporaryDirectory(prefix='latex-project-proofread-') as directory:
    root = Path(directory)
    parts = root / 'parts'
    parts.mkdir()
    main, first, second, nested, macro, other = [root / name for name in
        ('main.tex', 'parts/first.tex', 'parts/second.tex', 'parts/nested.tex', 'parts/macro.tex', 'other.tex')]
    sources = {
        main: '% !TeX program = pdflatex\n% Original preamble comment.\n'
              '\\documentclass{article}\n\\begin{document}\nMain original paragraph.\n\n'
              '\\input{parts/first}\n\\include{parts/second}\n'
              '\\def\\extra{parts/macro}\\input{\\extra}\n\\end{document}\n',
        first: '% Unicode offset: 😀\nFirst original paragraph.\n\n\\input{parts/nested}\n',
        second: 'Second original paragraph.\n',
        nested: 'Nested unchanged paragraph.\n',
        macro: 'Macro original paragraph.\n',
        other: '\\documentclass{article}\n\\begin{document}\nIndependent original paragraph.\n\\end{document}\n',
    }
    def write(file, source):
        raw = source.replace('\n', '\r\n').encode('utf-8')
        file.write_bytes((b'\xef\xbb\xbf' if file == second else b'') + raw)
    for file, source in sources.items():
        write(file, source)
    assert document_source_files(root, main) == [main, first, nested, second]
    server = make_server(main, main_thread='', preferences_path=root / 'preferences.sqlite3')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    direct = build_opener(ProxyHandler({}))
    base = f'http://127.0.0.1:{server.server_port}'
    def request(route, data=None):
        req = Request(base + route, None if data is None else json.dumps(data).encode(),
                      {'Content-Type': 'application/json'})
        try:
            with direct.open(req, timeout=60) as response:
                body = response.read()
                return response.status, body if body.startswith(b'%PDF') else json.loads(body)
        except HTTPError as error:
            return error.code, json.load(error)
    def history():
        with sqlite3.connect(root / '.latex-codex/history.sqlite3') as db:
            return tuple(db.execute('SELECT * FROM ' + table).fetchall() for table in
                         ('revisions', 'revision_annotations', 'project_review_baselines'))
    try:
        assert tex_options(server)['allow_installer'] is False
        state = request('/state')[1]
        code, result = request('/compile', {'source':sources[main], 'version':state['version'], 'allow_installer':False})
        assert code == 200 and result['ok'], result
        assert document_source_files(root, main, server.build.name) == [main, first, nested, second, macro], 'Recorder adds macro-expanded input files.'
        formal = (server.pdf, server.pdf_revision, server.sync_version)
        assert request('/preferences', {'latex-codex-proofread-project':'on'})[0] == 200
        for file in (main, first, second, macro, other):
            write(file, sources[file].replace('original', 'revised').replace('Original preamble', 'Revised preamble'))
        state = request('/state')[1]
        assert request('/source', {'path':str(other), 'version':state['version'], 'annotation_main_file':str(main)})[0] == 409
        assert request('/state')[1]['path'] == str(main), 'Pending article comments cannot be moved to an independent main file.'
        assert request('/source', {'path':str(nested), 'version':state['version'], 'annotation_main_file':str(other)})[0] == 409
        code, active = request('/source', {'path':str(nested), 'version':state['version'], 'annotation_main_file':str(main)})
        assert code == 200 and active['path'] == str(nested), active
        assert active['source_order'] == list(map(str, (main, first, nested, second, macro)))
        reviews = request('/project-review')[1]
        assert (server.pdf, server.pdf_revision) == formal[:2]
        formal = (server.pdf, server.pdf_revision, server.sync_version)
        order = reviews['source_order']
        assert order == list(map(str, (main, first, nested, second, macro)))
        assert len(reviews['files']) == 5, 'The project menu still includes independent documents.'
        groups, ids = [], {}
        for file in reviews['files']:
            if file['path'] not in order:
                continue
            items = []
            for hunk in file['hunks']:
                identifier = 100001 + len(ids)
                ids[identifier] = file['path']
                items.append({'id':identifier, 'start':hunk['start'], 'end':hunk['end'],
                              'selection':hunk['after'], 'replacement':hunk['after'], 'display_original':hunk['before']})
            groups.append({key:file[key] for key in ('path', 'source', 'version')} | {'items':items})
        payload = {'path':str(nested), 'version':active['version'], 'source':sources[nested], 'items':[],
                   'files':groups, 'main_file':str(main), 'project_root':str(root)}
        originals = {file:file.read_bytes() for file in sources}
        rows = history()
        with patch('editor.compile_source_snapshot', wraps=compile_source_snapshot) as compiled:
            code, preview = request('/proofread', payload)
            assert code == 200, preview
            assert compiled.call_count == 1, 'All files compile in one marked main-document overlay.'
        assert preview['proofread'] and not preview['sync'] and not preview['plain_preview']
        assert {region['id'] for region in preview['regions']} == set(ids)
        assert all(region['path'] == ids[region['id']] for region in preview['regions'])
        preamble = next(item['id'] for item in groups[0]['items'] if 'preamble' in item['selection'])
        assert any(region['id'] == preamble and region.get('source_only') for region in preview['regions']), 'Preamble-only changes remain actionable without TeX decoration.'
        checked = subprocess.run(['node', str(Path(__file__).with_name('pdf_analysis_test.mjs'))],
            input=json.dumps({'url':base + preview['pdf_url'] + '?v=' + preview['pdf_revision'], 'proofread':list(ids)}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, check=True)
        analysis = json.loads(checked.stdout)
        words = ' '.join(word[2] for word in analysis['words'])
        for label in ('Main', 'First', 'Second', 'Macro'):
            assert f'{label} original paragraph.' in words and f'{label} revised paragraph.' in words, words
        assert {mark['id'] for mark in analysis['proofread']} == set(ids) - {preamble}, 'Marked PDF text covers changes in every included file.'
        assert (server.pdf, server.pdf_revision, server.sync_version) == formal
        assert history() == rows and not server.history_pdfs and not (root / '.latex-codex/pdf-diff-cache').exists()
        assert all(file.read_bytes() == raw for file, raw in originals.items())

        # Invalid or stale cross-file metadata must be rejected before compiling.
        def invalid(data, status):
            with patch('editor.compile_source_snapshot', side_effect=AssertionError('Invalid requests cannot compile')):
                code, error = request('/proofread', data)
                assert code == status, error
        invalid(payload | {'main_file':str(other)}, 409)
        invalid(payload | {'project_root':str(parts)}, 409)
        invalid(payload | {'version':'stale'}, 409)
        invalid(payload | {'files':[groups[0], groups[0]]}, 400)
        invalid(payload | {'files':[groups[0] | {'path':str(other)}]}, 400)
        invalid(payload | {'files':[groups[0] | {'version':'stale'}]}, 409)
        invalid(payload | {'files':[groups[0] | {'source':'stale'}]}, 409)
        duplicate = groups[1] | {'items':[groups[1]['items'][0] | {'id':groups[0]['items'][0]['id']}]}
        invalid(payload | {'files':[groups[0], duplicate]}, 400)
        assert history() == rows and all(file.read_bytes() == raw for file, raw in originals.items())

        # Internal AI reviews in inactive files use their exact source, not a project baseline.
        ai_file = next(file for file in groups if file['path'] == str(first))
        ai_item = ai_file['items'][0]
        code, internal = request('/proofread', payload | {'files':[ai_file | {'items':[
            {key:value for key,value in ai_item.items() if key != 'display_original'} | {'replacement':'First AI revision.\n'}]}]})
        assert code == 200 and {region['id'] for region in internal['regions']} == {ai_item['id']}, internal
        assert all(region['path'] == str(first) for region in internal['regions'])
        assert history() == rows and all(file.read_bytes() == raw for file, raw in originals.items())

        # A current unsaved draft is combined with the other files without saving it.
        selection = 'Nested unchanged paragraph.'
        draft = sources[nested] + 'Unsaved extra sentence.\n'
        code, mixed = request('/proofread', payload | {'source':draft, 'items':[
            {'id':1, 'start':0, 'end':len(selection), 'selection':selection, 'replacement':'Nested improved paragraph.'}]})
        assert code == 200 and {region['id'] for region in mixed['regions']} == set(ids) | {1}, mixed
        assert history() == rows and all(file.read_bytes() == raw for file, raw in originals.items())

        # Keep/Undo in another file preserves the active source and checks both contexts.
        target = next(file for file in reviews['files'] if file['path'] == str(second))
        action = {key:target[key] for key in ('path', 'version', 'signature')} | {
            'id':target['hunks'][0]['id'], 'action':'undo', 'current_path':str(nested),
            'current_version':active['version'], 'main_file':str(main), 'project_root':str(root)}
        for stale in ({'current_version':'stale'}, {'current_path':str(first)}, {'main_file':str(other)}, {'project_root':str(parts)}):
            assert request('/project-review', action | stale)[0] == 409
        code, restored = request('/project-review', action)
        assert code == 200, restored
        assert second.read_bytes() == b'\xef\xbb\xbf' + sources[second].replace('\n', '\r\n').encode(), 'Inactive Undo preserves BOM and CRLF.'
        assert restored['state']['path'] == str(second) and restored['current_state']['path'] == str(nested)
        assert restored['current_state']['source'].replace('\r\n', '\n') == sources[nested]
        assert restored['current_state']['version'] == active['version']
        assert all(file.read_bytes() == raw for file, raw in originals.items() if file != second)
        target = next(file for file in restored['files'] if file['path'] == str(first))
        code, kept = request('/project-review', action | {key:target[key] for key in ('path', 'version', 'signature')} | {
            'id':target['hunks'][0]['id'], 'action':'keep'})
        assert code == 200 and not any(file['path'] == str(first) for file in kept['files']), kept
        assert first.read_bytes() == originals[first] and nested.read_bytes() == originals[nested]
        assert kept['current_state']['path'] == str(nested)
        assert snapshot(nested)['version'] == active['version']
        print('PASS: whole-article PDF, main/child/macro inputs, one compile, source-only preamble, draft overlay, scope/version guards, inactive Keep/Undo and source/history/PDF preservation')
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
        server.build.cleanup()
