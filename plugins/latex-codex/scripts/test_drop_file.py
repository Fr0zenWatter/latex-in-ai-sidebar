"""Run: python -B test_drop_file.py (stdlib only; no real dialogs or TeX)."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from editor import make_server


def fingerprint(file):
    raw = file.read_bytes()
    return {'name': file.name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


with tempfile.TemporaryDirectory() as directory:
    workspace = Path(directory).resolve()
    project, external = workspace / 'project', workspace / 'external'
    project.mkdir(); external.mkdir()
    main, body = project / 'main.tex', project / 'body.tex'
    main.write_bytes(b'\\documentclass{article}\n\\begin{document}\n\\input{body}\n\\end{document}')
    original = b'\xef\xbb\xbfOriginal child.\r\n'
    body.write_bytes(original)
    note = external / 'notes.md'
    note.write_bytes(b'# External original\r\n')
    duplicate = project / 'nested' / 'body.tex'
    duplicate.parent.mkdir()
    duplicate.write_bytes(b'Different file with the same name.')
    with patch('editor.compiler', return_value=('pdflatex', 'unused')):
        server = make_server(main, main_thread='', preferences_path=workspace / 'preferences.sqlite3')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        opener, base = build_opener(ProxyHandler({})), f'http://127.0.0.1:{server.server_port}'

        def request(route, data=None):
            req = Request(base + route, None if data is None else json.dumps(data).encode(), {'Content-Type': 'application/json'})
            try:
                with opener.open(req, timeout=5) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)

        def state():
            return request('/state')[1]

        try:
            with patch('editor.choose_file') as picker:
                code, opened = request('/open', {'dropped_file': fingerprint(body)})
                assert code == 200 and opened['path'] == str(body) and opened['main_file'] == str(main)
                picker.assert_not_called()
                assert body.read_bytes() == original
            before = state()
            for invalid in (None, {}, {'name':'../body.tex'}, {'name':'C:\\body.tex'}, {'name':'bad.txt'},
                            {'name':'body.tex','size':True,'sha256':'0'*64},
                            {'name':'body.tex','size':1,'sha256':'not a hash'}, {'name':'body.tex','size':1}):
                with patch('editor.choose_file') as picker:
                    assert request('/open', {'dropped_file':invalid})[0] == 400
                    picker.assert_not_called()
                assert state()['path'] == before['path']
            duplicate.write_bytes(original)
            with patch('editor.choose_file', return_value='') as picker:
                code, cancelled = request('/open', {'dropped_file': fingerprint(body)})
                assert code == 200 and cancelled == {'cancelled':True}
                picker.assert_called_once_with(body, 'body.tex')
                assert state()['path'] == before['path']
            with patch('editor.choose_file', return_value=str(duplicate)):
                code, opened = request('/open', {'dropped_file':fingerprint(body)})
                assert code == 200 and opened['path'] == str(duplicate), 'Identical copies require an explicit selection.'
            with patch('editor.choose_file', return_value=str(body)) as picker:
                code, opened = request('/open', {'dropped_file':{'name':'body.tex'}})
                assert code == 200 and opened['path'] == str(body)
                picker.assert_called_once_with(duplicate, 'body.tex')
            with patch('editor.choose_file', return_value=str(body)):
                before = state()
                assert request('/open', {'dropped_file':fingerprint(note)})[0] == 400
                assert state()['path'] == before['path'] and state()['project_root'] == str(project)
            with patch('editor.choose_file', side_effect=OSError('Picker unavailable')):
                assert request('/open', {'dropped_file':fingerprint(note)})[0] == 500
                assert state()['path'] == before['path']
            stale = fingerprint(body)
            body.write_bytes(b'Changed after the drop.')
            duplicate.write_bytes(b'Also changed.')
            with patch('editor.choose_file', return_value=str(body)):
                assert request('/open', {'dropped_file':stale})[0] == 400
                assert state()['path'] == before['path']
            body.write_bytes(original)
            with patch('editor.choose_file', return_value=str(note)) as picker:
                code, opened = request('/open', {'dropped_file':fingerprint(note)})
                assert code == 200 and opened['path'] == str(note) and opened['project_root'] == str(external)
                picker.assert_called_once_with(body, 'notes.md')
            assert note.read_bytes() == b'# External original\r\n' and body.read_bytes() == original
            print('PASS: hidden-path project matching, original bytes/main detection, duplicate/native-picker selection, cancellation and metadata/mismatch guards')
        finally:
            server.shutdown(); worker.join(); server.server_close(); server.build.cleanup()
