"""Run: python -B scripts/test_http_server.py (stdlib only; no TeX needed)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import tempfile
import threading
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from editor import make_server


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'main.tex'
    path.write_text('Original.', encoding='utf-8')
    with patch('editor.compiler', return_value=('xelatex', 'unused')), patch('editor.shutil.which', return_value='unused'):
        server = make_server(path, main_thread='', preferences_path=Path(directory) / 'settings.sqlite3')
    accepted = threading.Event()
    original_get_request = server.get_request

    def get_request():
        result = original_get_request()
        accepted.set()
        return result

    server.get_request = get_request
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'

    def request(route, data=None):
        req = Request(base + route, None if data is None else json.dumps(data).encode(),
                      {'Content-Type': 'application/json'})
        try:
            with build_opener(ProxyHandler({})).open(req, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    idle = socket.create_connection(server.server_address)
    started, release = threading.Event(), threading.Event()
    try:
        assert accepted.wait(2)
        assert request('/')[0] == 200, 'An idle browser preconnect must not block the page.'
        assert request('/vendor/latex-themes.mjs')[0] == 200
        assert request('/vendor/latex-native-annotations.mjs')[0] == 200
        assert request('/vendor/latex-word-hint.mjs')[0] == 200
        code, words = request('/vendor/english-words.mjs')
        assert code == 200 and b'export const englishWords' in words
        vocabulary_file = path.parent / 'unopened.tex'
        vocabulary_file.write_text(r'Quasiharmonic hyperconvergence \hiddenprojectcommand', encoding='utf-8')
        vocabulary = json.loads(request('/state')[1])['project_words']
        assert 'quasiharmonic' in vocabulary and 'hyperconvergence' in vocabulary
        assert 'hiddenprojectcommand' not in vocabulary
        assert 'original' not in vocabulary, 'The active draft supplies its own words without indexing the unfinished token twice.'
        active = json.loads(request('/state')[1])
        code, body = request('/source', {'path':str(vocabulary_file), 'version':active['version']})
        child_state = json.loads(body)
        assert code == 200 and 'original' in child_state['project_words'] and 'quasiharmonic' not in child_state['project_words']
        assert request('/source', {'path':str(path), 'version':child_state['version']})[0] == 200
        vocabulary_file.write_text('newlyobservedterm', encoding='utf-8')
        vocabulary = json.loads(request('/state')[1])['project_words']
        assert 'newlyobservedterm' in vocabulary and 'hyperconvergence' not in vocabulary
        vocabulary_file = vocabulary_file.rename(vocabulary_file.with_suffix('.md'))
        assert 'newlyobservedterm' in json.loads(request('/state')[1])['project_words'], 'Renamed Markdown sources remain indexed.'
        vocabulary_file.write_text('markdownprojectword', encoding='utf-8')
        assert 'markdownprojectword' in json.loads(request('/state')[1])['project_words']
        vocabulary_file.unlink()
        assert 'markdownprojectword' not in json.loads(request('/state')[1])['project_words']
        ignored_vocabulary = path.parent / 'ignored.txt'
        ignored_vocabulary.write_text('neverindextxtwords', encoding='utf-8')
        assert 'neverindextxtwords' not in json.loads(request('/state')[1])['project_words']
        ignored_vocabulary.unlink()
        print('PASS: project vocabulary includes unopened sources, excludes commands/non-sources and tracks external edits/deletions')
        candidate = path.parent / '中文 project' / 'entry.md'
        candidate.parent.mkdir()
        candidate.write_text('# New project', encoding='utf-8')
        with patch('editor.choose_file', return_value=str(candidate)) as choose:
            code, body = request('/project/choose-file', {})
            assert code == 200 and json.loads(body) == {'path': str(candidate)}
            choose.assert_called_once_with(path)
            assert json.loads(request('/state')[1])['path'] == str(path), 'Browsing must not switch projects.'
        with patch('editor.choose_file', return_value=''):
            assert json.loads(request('/project/choose-file', {})[1]) == {'cancelled': True}
        with patch('editor.choose_file', side_effect=OSError('Picker unavailable')):
            assert request('/project/choose-file', {})[0] == 500
        picker_started, picker_release = threading.Event(), threading.Event()
        def choose_slow(_path):
            picker_started.set()
            assert picker_release.wait(5)
            return str(candidate)
        with patch('editor.choose_file', side_effect=choose_slow), ThreadPoolExecutor(1) as pool:
            picking = pool.submit(request, '/project/choose-file', {})
            try:
                assert picker_started.wait(2)
                state = json.loads(request('/state')[1])
                assert request('/save', {'source': state['source'], 'version': state['version']})[0] == 200
            finally:
                picker_release.set()
            assert picking.result()[0] == 200
        with patch('editor.compiler', return_value=('xelatex', 'unused')):
            state = json.loads(request('/state')[1])
            for invalid in ('', None, 12):
                assert request('/project', {'main_file': invalid, 'version': state['version']})[0] == 400
            assert request('/project', {'main_file': str(candidate), 'version': 'stale'})[0] == 409
            code, body = request('/project', {'main_file': '中文 project/entry.md', 'version': state['version']})
            configured = json.loads(body)
            assert code == 200 and configured['main_file'] == str(candidate)
            assert configured['project_root'] == str(candidate.parent)
            assert 'original' not in configured['project_words'], 'A new project cannot inherit vocabulary from the previous root.'
            assert [file['name'] for file in configured['files']] == ['entry.md']
            code, body = request('/project', {'main_file': str(path), 'version': configured['version']})
            assert code == 200 and json.loads(body)['project_root'] == str(path.parent)
            with tempfile.TemporaryDirectory() as invalid_directory:
                invalid_source = Path(invalid_directory) / 'invalid.md'
                invalid_source.write_bytes(b'\xff')
                current = json.loads(body)
                assert request('/project', {'main_file': str(invalid_source), 'version': current['version']})[0] == 400
                assert json.loads(request('/state')[1])['main_file'] == str(path), 'Invalid source leaves the live project intact.'
        print('PASS: native project picker without mutations/blocked autosave, inferred main-file directory, relative/absolute paths and failed-apply protection')
        with patch('editor.compiler', side_effect=lambda source, choice='auto': (choice if choice != 'auto' else 'pdflatex', 'unused')), \
                patch('editor.compile_tex', return_value=(False, "MiKTeX\n! LaTeX Error: File `diagbox.sty' not found.", 'pdflatex')) as compile_job:
            current = json.loads(request('/state')[1])
            payload = {'source':current['source'], 'version':current['version'], 'engine_choice':'pdflatex', 'allow_installer':False}
            code, body = request('/compile', payload)
            result = json.loads(body)
            assert code == 200 and result['missing_package'] == 'diagbox.sty' and result['miktex']
            assert compile_job.call_args.kwargs['allow_installer'] is False
            assert json.loads(request('/state')[1])['engine_choice'] == 'pdflatex'
            build_before = server.build.name
            assert request('/compile', {**payload, 'engine_choice':'xelatex', 'allow_installer':True})[0] == 200
            assert server.build.name != build_before, 'Changing engines starts with clean temporary auxiliaries.'
            assert compile_job.call_args.kwargs['allow_installer'] is True
            assert json.loads(request('/state')[1])['allow_installer'] is True
            for invalid in ('yes', 1, None):
                assert request('/compile', {**payload, 'allow_installer':invalid})[0] == 400
            assert request('/compile', {**payload, 'engine_choice':'cmd.exe'})[0] == 400
            assert request('/compile', {**payload, 'engine_choice':'auto', 'allow_installer':False})[0] == 200
        print('PASS: per-main engine persistence, fresh build on engine changes, structured missing packages and explicit installer boolean')
        with patch('editor.open_folder') as open_folder, patch('editor.compile_tex') as no_compile, \
                patch('editor.compiler', return_value=('xelatex', 'unused')):
            assert request('/open-folder')[0] == 404, 'Opening a folder requires an explicit POST.'
            assert request('/open-folder', {})[0] == 409
            assert request('/open-folder', {'path': str(path.parent)})[0] == 409
            open_folder.assert_not_called()
            assert request('/open-folder', {'path': str(path)})[0] == 200
            open_folder.assert_called_once_with(path.parent)
            child = path.parent / '中文 subfolder' / 'notes.md'
            child.parent.mkdir()
            child.write_text('# Notes', encoding='utf-8')
            state = json.loads(request('/state')[1])
            assert request('/source', {'path': str(child), 'version': state['version']})[0] == 200
            open_folder.reset_mock()
            assert request('/open-folder', {'path': str(path)})[0] == 409
            open_folder.assert_not_called()
            assert request('/open-folder', {'path': str(child)})[0] == 200
            open_folder.assert_called_once_with(child.parent)
            with closing(HTTPConnection(*server.server_address, timeout=3)) as connection:
                connection.request('POST', '/open-folder', json.dumps({'path': str(child)}),
                                   {'Content-Type': 'application/json', 'Origin': 'https://example.com'})
                response = connection.getresponse()
                assert response.status == 403
                response.read()
            assert open_folder.call_count == 1, 'Cross-origin requests cannot launch the file manager.'
            open_folder.side_effect = OSError('File manager unavailable')
            code, body = request('/open-folder', {'path': str(child)})
            assert code == 500 and json.loads(body)['error'] == 'File manager unavailable'
            assert path.read_text(encoding='utf-8') == 'Original.' and child.read_text(encoding='utf-8') == '# Notes'
            state = json.loads(request('/state')[1])
            assert request('/source', {'path': str(path), 'version': state['version']})[0] == 200
            no_compile.assert_not_called()
        print('PASS: current source folder, subfile switches, stale/cross-origin rejection and failure reporting without saves/compiles')
        with closing(HTTPConnection(*server.server_address, timeout=3)) as connection:
            connection.request('GET', '/')
            response = connection.getresponse()
            assert response.status == 200 and response.version == 11
            response.read()
            reused_socket = connection.sock
            for route in ('/vendor/codemirror.js', '/state'):
                connection.request('GET', route)
                response = connection.getresponse()
                assert response.status == 200 and not response.will_close
                state_body = response.read()
                assert connection.sock is reused_socket, 'Page, assets and state must share one connection.'
            unchanged = json.loads(state_body)
            connection.request('POST', '/save', json.dumps({'source': unchanged['source'], 'version': unchanged['version']}),
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            assert response.status == 200 and not response.will_close
            response.read()
            assert connection.sock is reused_socket, 'Valid saves must preserve connection reuse.'
            connection.request('POST', '/save', 'unread body', {'Content-Type': 'text/plain'})
            response = connection.getresponse()
            assert response.status == 400 and response.will_close, 'Rejected bodies must not leak into the next request.'
            response.read()
        print('PASS: HTTP/1.1 connection reuse and rejected-request framing')
        for theme in ('eclipse', 'idea', 'neo', 'base16-light', 'solarized', 'material-darker', 'material-palenight', 'ayu-dark', 'gruvbox-dark'):
            code, css = request('/vendor/' + theme + '.css')
            assert code == 200 and b'.cm-s-' in css, 'Bundled themes must be available offline.'
        revision = json.loads(request('/state')[1])['version']
        server.pdf, server.pdf_revision, server.sync_version = b'%PDF-preserved', 'preserved', revision
        with patch('editor.compile_tex') as no_compile:
            assert request('/save', {'source': 'Original.', 'version': revision})[0] == 200
            assert server.sync_version == revision, 'Unchanged autosave preserves synchronization.'
            code, body = request('/save', {'source': 'Saved without compile.', 'version': revision})
            assert code == 200 and path.read_text(encoding='utf-8') == 'Saved without compile.'
            assert not server.sync_version and server.pdf == b'%PDF-preserved'
            assert request('/save', {'source': 'Stale.', 'version': revision})[0] == 409
            assert request('/save', {'source': 123, 'version': revision})[0] == 400
            no_compile.assert_not_called()
        revision = json.loads(body)['version']


        def compile_tex(*args, **kwargs):
            started.set()
            assert release.wait(5)
            return False, 'Test compilation result.', 'xelatex'

        with patch('editor.compiler', return_value=('xelatex', 'unused')), patch('editor.compile_tex', side_effect=compile_tex), ThreadPoolExecutor(2) as pool:
            first = pool.submit(request, '/compile', {'source': 'First edit.', 'version': revision})
            try:
                assert started.wait(2)
                second = pool.submit(request, '/compile', {'source': 'Stale edit.', 'version': revision})
                assert request('/')[0] == 200, 'The page shell stays available during compilation.'
                assert request('/vendor/latex-chat.mjs')[0] == 200
            finally:
                release.set()
            assert first.result()[0] == 200
            assert second.result()[0] == 409, 'Concurrent stale saves must never overwrite the first edit.'
        assert path.read_text(encoding='utf-8') == 'First edit.'
        assert json.loads(request('/state')[1])['source'] == 'First edit.'
        # A slow history comparison must not lock the live document after closing history.
        started.clear(); release.clear()
        def history_pdf_changes(*args, **kwargs):
            started.set()
            assert release.wait(5)
            return {'changes': []}
        latest = json.loads(request('/history?path=' + str(path))[1])['revisions'][0]['id']
        current = json.loads(request('/state')[1])
        with patch('editor.history_pdf_changes', side_effect=history_pdf_changes), ThreadPoolExecutor(1) as pool:
            preview = pool.submit(request, '/history/pdf', {'path':str(path), 'id':latest, 'compare':'previous'})
            try:
                assert started.wait(2)
                assert json.loads(request('/state')[1])['source'] == 'First edit.'
                assert request('/save', {'source':'First edit.', 'version':current['version']})[0] == 200
            finally:
                release.set()
            assert preview.result()[0] == 200
        print('PASS: state polling and saving remain responsive during history PDF generation')
        started.clear(); release.clear()
        def cancellable_pdf(*args, cancelled):
            started.set()
            while not release.wait(.01):
                if cancelled(): raise InterruptedError('History cancelled')
            return {'changes': []}
        with patch('editor.history_pdf_changes', side_effect=cancellable_pdf), ThreadPoolExecutor(1) as pool:
            preview = pool.submit(request, '/history/pdf', {'path':str(path), 'id':latest, 'compare':'previous', 'request_id':'1'*36})
            try:
                assert started.wait(2)
                assert request('/history/pdf-cancel', {'request_id':'2'*36})[0] == 200
                assert not preview.done(), 'A late cancellation must not cancel a newer comparison.'
                assert request('/history/pdf-cancel', {'request_id':'1'*36})[0] == 200
                assert preview.result(timeout=2)[0] == 409
                assert request('/state')[0] == 200
            finally:
                release.set()
        print('PASS: targeted history cancellation stops obsolete work without blocking state reads')
        batch = {'request_id': '1' * 32, 'source': 'First edit.', 'selection': 'First',
                 'messages': [{'role': 'user', 'content': 'Shorten this.'}],
                 'remember': True, 'memory_revision': 0,
                 'annotations': [{'id': 1, 'start': 0, 'end': 5, 'selection': 'First', 'request': 'Shorten this.'}]}
        job = Mock(result={'status': 'running'})
        job.start.return_value = job
        with patch('editor.ChatJob', return_value=job) as spawn:
            code, body = request('/chat', batch)
            assert code == 200 and json.loads(body)['id'] == batch['request_id']
            assert request('/chat', batch) == (code, body), 'A lost acknowledgement must not start a second model request.'
            job.result = {'status': 'done'}
            assert request('/chat', batch) == (code, body), 'Retries after completion must reuse the original task.'
            assert spawn.call_count == job.start.call_count == 1
            assert request('/chat', {**batch, 'selection': 'First edit.'})[0] == 409
            assert request('/chat', {**batch, 'request_id': 'invalid'})[0] == 400
        server.chat = None
        print('PASS: Send retries reuse one task and reject changed or invalid requests')
        # Branch requests use server-stored history and never accept source-edit annotations.
        from history import History
        memory = History(path)
        reply_id = memory.chat_append(0, 'Explain this.', {'reply':'An isolated discussion.', 'replacement':None}, str(path), '')
        main = memory.chat_read()
        fork = {'project':str(path.parent), 'parent':main['thread'], 'message_id':reply_id, 'quote':'isolated discussion'}
        assert request('/chat/branch',{**fork,'project':'another-project'})[0] == 409
        code, body = request('/chat/branch',fork)
        assert code == 200
        branch = json.loads(body)
        assert json.loads(request('/chat/history?thread=' + branch['thread'])[1]) == branch
        assert request('/chat/history?thread=missing')[0] == 400
        rename = {'project':str(path.parent),'thread':branch['thread'],'title':'Renamed branch'}
        assert request('/chat/rename',{**rename,'project':'another-project'})[0] == 409
        assert request('/chat/rename',{**rename,'title':''})[0] == 400
        assert request('/chat/rename',{**rename,'thread':'missing'})[0] == 400
        assert json.loads(request('/chat/rename',rename)[1]) == {'thread':branch['thread'],'title':'Renamed branch'}
        renamed = json.loads(request('/chat/history?thread=' + branch['thread'])[1])
        assert renamed['revision'] == branch['revision'] and renamed['messages'] == branch['messages']
        assert next(node for node in renamed['threads'] if node['id'] == branch['thread'])['title'] == 'Renamed branch'
        branch_request = {**batch,'request_id':'2'*32,'thread':branch['thread'],'memory_revision':0,
                          'selection':'','messages':[{'role':'user','content':'Continue.'}]}
        branch_request.pop('annotations')
        with patch('editor.ChatJob',return_value=job) as spawn:
            assert request('/chat',branch_request)[0] == 200
            context = spawn.call_args.args[0]
            assert context['memory_thread'] == branch['thread'] and context['selection'] == ''
            assert context['branch_context']['quote'] == fork['quote']
            assert not context['main_conversation']['available']
        server.chat = None
        assert request('/chat',{**branch_request,'request_id':'3'*32,'selection':'First','annotations':batch['annotations']})[0] == 400
        assert json.loads(request('/chat/history')[1])['revision'] == reply_id
        removal = {'project':str(path.parent),'thread':branch['thread'],'descendants':[branch['thread']]}
        assert request('/chat/delete',{**removal,'project':'another-project'})[0] == 409
        assert request('/chat/delete',{**removal,'descendants':[]})[0] == 400
        assert request('/chat/delete',{**removal,'thread':main['thread'],'descendants':[main['thread']]})[0] == 400
        job.result = {'status':'running'};server.chat = job
        assert request('/chat/delete',removal)[0] == 409
        server.chat = None;job.result = {'status':'done'}
        assert json.loads(request('/chat/delete',removal)[1])['deleted'] == [branch['thread']]
        assert request('/chat/history?thread=' + branch['thread'])[0] == 400
        assert json.loads(request('/chat/history')[1])['revision'] == reply_id
        print('PASS: branch HTTP creation, project/message validation, frozen context and discussion-only job routing')
        build = Path(server.build.name)
        (build / 'main.pdf').write_bytes(b'%PDF-test')
        (build / 'main.aux').write_text('\n'.join([
            r'\newlabel{eq:regularity}{{3.13}{17}{}{equation*.114}{}}',
            r'\newlabel{custom}{{\dangerous{data}}{1}}',
            r'\bibcite{BankXu2003b}{9}', r'\bibcite{BankXuZheng2007}{10}',
            r'\bibcite{BankNguyen2011}{{6}{2011}{{Bank and Nguyen}}{{}}}',
            r'\bibcite{eq:regularity}{7}', r'\bibcite{custom}{\dangerous{data}}',
            r'\bibcite{author-year}{{Doe(2026)}{2026}{{Doe}}{{}}}',
        ]), encoding='utf-8')
        with patch('editor.compiler', return_value=('xelatex', 'unused')), patch('editor.compile_tex', return_value=(True, 'ok', 'xelatex')):
            revision = json.loads(request('/state')[1])['version']
            code, body = request('/compile', {'source': 'First edit.', 'version': revision})
            assert code == 200 and json.loads(body)['labels'] == {'eq:regularity': '3.13'}
            assert json.loads(body)['citations'] == {'BankXu2003b': '9', 'BankXuZheng2007': '10', 'BankNguyen2011': '6', 'eq:regularity': '7'}
        with patch('editor.compiler', return_value=('xelatex', 'unused')), patch('editor.compile_tex', return_value=(False, 'error', 'xelatex')):
            revision = json.loads(request('/state')[1])['version']
            code, body = request('/compile', {'source': 'First edit.', 'version': revision})
            assert code == 200 and json.loads(body)['labels'] == json.loads(body)['citations'] == {} and not json.loads(body)['sync']
        print('PASS: compiled reference/citation metadata and failed-build isolation')
        print('PASS: idle browser connections, page/assets during compilation, serialized saves and conflict protection')
    finally:
        release.set()
        idle.close()
        server.shutdown()
        worker.join()
        server.server_close()
        server.build.cleanup()
