"""Run: python -B test_proofread_navigation.py (local TeX/SyncTeX and Node; temporary files only)."""
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from unittest.mock import patch

from editor import make_server, snapshot
from references import source_digest


def suggestion(source, before, after, identifier):
    start = source.index(before)
    return {'id':identifier, 'start':start, 'end':start+len(before), 'selection':before, 'replacement':after}


with tempfile.TemporaryDirectory(prefix='latex proofread navigation ') as directory:
    root = Path(directory)
    main, child, other = (root / name for name in ('main.tex', 'changed.tex', 'untouched.tex'))
    source = '% !TeX program = pdflatex\n\\documentclass{article}\n\\begin{document}\nStartMain untouched.\n\nMainOld sentence.\n\n\\input{changed}\n\nTailMain unchanged.\n\n\\input{untouched}\n\\end{document}\n'
    child_source = 'StartChild untouched.\n\nChildOld sentence.\n\nTailChild unchanged.\n\n'
    other_source = '\\newpage\nOtherChild untouched.\n\\[x^2+y=1\\]\n'
    for file, text in ((main,source),(child,child_source),(other,other_source)):
        file.write_bytes(text.replace('\n','\r\n').encode())
    originals = {file:file.read_bytes() for file in (main,child,other)}
    server = make_server(main, main_thread='', preferences_path=root/'preferences.sqlite3')
    server.pdf, server.pdf_revision = b'previous formal PDF', 'formal-revision'
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    direct = build_opener(ProxyHandler({}))

    def request(route, data=None):
        req = Request(base+route, None if data is None else json.dumps(data).encode(), {'Content-Type':'application/json'})
        try:
            with direct.open(req,timeout=55) as response:
                raw = response.read()
                return response.status, json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def history_rows():
        with sqlite3.connect(root/'.latex-codex/history.sqlite3') as db:
            return db.execute('SELECT id,digest,kind FROM revisions').fetchall()

    def analyze(preview):
        run = subprocess.run(['node',str(Path(__file__).with_name('pdf_analysis_test.mjs'))],
            input=json.dumps({'url':base+preview['pdf_url']+'?v='+preview['pdf_revision']}),
            text=True,encoding='utf-8',capture_output=True,timeout=30)
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)

    try:
        current = request('/state')[1]
        payload = {'path':str(main),'version':current['version'],'source':source,
            'items':[suggestion(source,'MainOld sentence.','MainNew paragraph.\n\nAddedMain sentence.',1)],
            'files':[{'path':str(child),'version':snapshot(child)['version'],'source':child_source,
                'items':[suggestion(child_source,'ChildOld sentence.','ChildNew paragraph.\n\nAddedChild sentence.',2)]}]}
        history = history_rows()
        code, preview = request('/proofread',payload)
        assert code == 200, preview
        assert preview['proofread_version'] and not preview['sync']
        cached = server.proofread_sync[preview['pdf_revision']]
        assert not Path(cached['query_root']).exists(), 'Preview sources and build directories are still cleaned immediately.'
        analysis = analyze(preview)

        def locate(word, *, preview=preview, analysis=analysis, source=source, extra=None):
            match = next(item for item in analysis['words'] if item[2].rstrip('.') == word)
            page, (left,bottom,right,top), _ = match
            body = {'direction':'backward','path':str(main),'version':current['version'],
                'source_digest':source_digest(source),'pdf_revision':preview['pdf_revision'],
                'proofread_version':preview['proofread_version'],'boxes':analysis['boxes'],
                'page':page,'x':(left+right)/2,'y':(bottom+top)/2,**(extra or {})}
            return request('/synctex',body), body

        for word, file, original in (
                ('StartMain',main,'StartMain'),('MainOld',main,'MainOld'),('MainNew',main,'MainOld'),
                ('AddedMain',main,'MainOld'),('TailMain',main,'TailMain'),
                ('ChildOld',child,'ChildOld'),('ChildNew',child,'ChildOld'),('AddedChild',child,'ChildOld'),
                ('TailChild',child,'TailChild'),('OtherChild',other,'OtherChild')):
            (code, result), body = locate(word)
            assert code == 200, (word,result)
            expected = {main:source,child:child_source,other:other_source}[file]
            assert result['path'] == str(file), (word,result)
            assert result['line'] == expected[:expected.index(original)].count('\n')+1, (word,result)
            assert result['source_digest'] == source_digest(expected) and result['version'] == snapshot(file)['version']
        (code,result),body = locate('ChildNew',extra={'annotation_id':2})
        assert code == 200 and result['path'] == str(child) and result['line'] == 3
        for changed in ({'proofread_version':'stale'},{'pdf_revision':'missing'},{'source_digest':'stale'},
                        {'version':'stale'},{'direction':'forward'}):
            assert request('/synctex',{**body,**changed})[0] == 409
        for changed in ({'page':0},{'x':float('inf')},{'annotation_id':999},{'pdf_revision':[]}):
            assert request('/synctex',{**body,**changed})[0] == 400
        with patch('editor.synctex_records',return_value=[{'Input':str(root.parent/'outside.tex'),'Line':'1'}]):
            assert request('/synctex',{key:value for key,value in body.items() if key!='annotation_id'})[0] == 400
        def changed_during_query(*_):
            other.write_bytes(originals[other]+b'% changed during navigation\n')
            return [{'Input':next(iter(cached['inputs'])),'Line':'4'}]
        with patch('editor.synctex_records',side_effect=changed_during_query):
            assert request('/synctex',{key:value for key,value in body.items() if key!='annotation_id'})[0] == 409
        other.write_bytes(originals[other])
        other.write_bytes(originals[other]+b'% external change\n')
        assert request('/synctex',body)[0] == 409
        other.write_bytes(originals[other])

        draft = source.replace('\\end{document}',r'Before $\frac12$ after.'+'\n\\end{document}')
        item = suggestion(draft,'1','3',9)
        code, plain = request('/proofread',{'path':str(main),'version':current['version'],'source':draft,'items':[item]})
        assert code == 200 and plain['plain_preview'], plain
        plain_analysis = analyze(plain)
        (code,result),_ = locate('after',preview=plain,analysis=plain_analysis,source=draft)
        assert code == 200 and result['path'] == str(main) and result['line'] == draft[:item['start']].count('\n')+1, result

        changed_payload = {**payload,'items':[{**payload['items'][0],'id':3}],
            'files':[{**payload['files'][0],'items':[{**payload['files'][0]['items'][0],'id':4}]}]}
        code, latest = request('/proofread',changed_payload)
        assert code == 200, latest
        assert len(server.proofread_sync) == len(server.proofread_pdfs) == 2
        assert preview['pdf_revision'] not in server.proofread_sync
        assert request('/synctex',body)[0] == 409, 'Evicted preview coordinates cannot locate another PDF.'
        assert all(file.read_bytes() == raw for file,raw in originals.items())
        assert server.pdf == b'previous formal PDF' and server.pdf_revision == 'formal-revision'
        assert history_rows() == history and not server.history_pdfs
        print('PASS: proofread inverse navigation for original/replacement/unchanged text, child files, line shifts, plain math drafts, version/context guards and cache eviction; no source/history/formal-PDF writes')
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
        server.build.cleanup()
