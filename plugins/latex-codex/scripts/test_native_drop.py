"""Run: python -B test_native_drop.py (no file manager interaction or TeX)."""
import json
from pathlib import Path
import tempfile
import threading
import sys
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from editor import make_server
from native_drop import NativeDrop, drop_rectangle, read_drop_handle


area = {'x': 40, 'y': 30, 'width': 400, 'height': 300, 'scale': 1.5}
assert drop_rectangle(area, (200, 150), (0, 0, 800, 600)) == (140, 105, 740, 555)
assert drop_rectangle(area, (-100, 50), (-400, 0, 0, 500)) == (-160, 5, 0, 455)
for invalid in (None, {}, {**area, 'x': -1}, {**area, 'y': 301}, {**area, 'scale': float('nan')},
                {**area, 'width': True}, {**area, 'width': 20000}, {**area, 'scale': 0}):
    try:
        drop_rectangle(invalid, (0, 0), (0, 0, 1000, 1000))
        raise AssertionError('Invalid native overlay geometry accepted.')
    except ValueError:
        pass

bridge = NativeDrop()
def fake_window(_):
    bridge._set('ready'); bridge.ready.set(); bridge.stop.wait(2)
with patch('native_drop.sys.platform', 'win32'), patch.object(bridge, '_window', side_effect=fake_window):
    assert bridge.arm('1'*32, area)['status'] == 'ready'
    assert bridge.read('2'*32, cancel=True)['status'] == 'cancelled' and not bridge.stop.is_set()
    assert bridge.arm('2'*32, area)['status'] == 'unavailable', 'A second page cannot replace an active gesture.'
    bridge._set('dropped', files=[r'C:\outside\paper.tex'])
    assert bridge.read('1'*32, cancel=True)['files'] == [r'C:\outside\paper.tex']
    bridge.close()
    assert not bridge.thread.is_alive()
with patch('native_drop.sys.platform', 'linux'):
    assert NativeDrop().arm('1'*32, area)['status'] == 'unavailable'
failed = NativeDrop()
with patch('native_drop.sys.platform', 'win32'), patch.object(failed, '_window', side_effect=OSError('Unavailable')):
    assert failed.arm('1'*32, area)['status'] == 'unavailable'
    failed.close()
print('PASS: DPI/clipped overlay geometry, input limits, gesture ownership, single receiver, result retention and cleanup/fallback')

if sys.platform == 'win32':
    import ctypes as c
    import struct
    kernel = c.WinDLL('kernel32')
    kernel.GlobalAlloc.argtypes, kernel.GlobalAlloc.restype = (c.c_uint, c.c_size_t), c.c_void_p
    kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = (c.c_void_p,), c.c_void_p
    kernel.GlobalUnlock.argtypes = kernel.GlobalFree.argtypes = (c.c_void_p,)
    kernel.GlobalFree.restype = c.c_void_p
    paths = [r'C:\外部项目\有空格 paper.tex', 'C:\\' + 'long-folder\\'*30 + 'notes.md']
    payload = struct.pack('<IiiII', 20, 0, 0, 0, 1) + ('\0'.join(paths)+'\0\0').encode('utf-16-le')
    handle = kernel.GlobalAlloc(0x42, len(payload))
    assert handle
    try:
        memory = kernel.GlobalLock(handle); assert memory
        c.memmove(memory, payload, len(payload)); kernel.GlobalUnlock(handle)
        assert read_drop_handle(handle) == paths
    finally:
        kernel.GlobalFree(handle)
    receiver = NativeDrop()
    try:
        assert receiver.arm('a'*32, {'x':10,'y':10,'width':30,'height':30,'scale':1})['status'] == 'ready', receiver.error
    finally:
        receiver.close()
    assert not receiver.thread.is_alive()
    print('PASS: real Windows CF_HDROP Unicode/long paths, transparent OLE registration and window destruction')

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    path = root / 'main.md'; path.write_text('# Native drop', encoding='utf-8')
    server = make_server(path, main_thread='', preferences_path=root / 'preferences.sqlite3')
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    base, opener = f'http://127.0.0.1:{server.server_port}', build_opener(ProxyHandler({}))
    def request(data, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = Request(base + '/native-drop', json.dumps(data).encode(), headers)
        try:
            with opener.open(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)
    try:
        for data in ({}, {'action':'arm','id':'bad','area':area}, {'action':'open','id':'1'*32},
                     {'action':'arm','id':'1'*32,'area':{}}):
            assert request(data)[0] == 400
        with patch.object(server.native_drop, 'arm', return_value={'status':'ready'}) as arm:
            assert request({'action':'arm','id':'1'*32,'area':area}) == (200, {'status':'ready'})
            arm.assert_called_once_with('1'*32, area)
            assert request({'action':'arm','id':'1'*32,'area':area}, 'https://example.com')[0] == 403
        with patch.object(server.native_drop, 'read', return_value={'status':'dropped','files':[str(path)]}) as read:
            assert request({'action':'cancel','id':'1'*32})[1]['files'] == [str(path)]
            read.assert_called_once_with('1'*32, cancel=True)
        assert path.read_text(encoding='utf-8') == '# Native drop'
    finally:
        server.shutdown(); worker.join(); server.server_close(); server.build.cleanup()
print('PASS: same-origin native handshake, validated IDs/actions/geometry, cancellation and no direct source mutation')
