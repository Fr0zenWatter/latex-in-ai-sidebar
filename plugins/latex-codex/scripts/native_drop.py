"""A short-lived Windows OLE drop target, armed only by a file drag in the editor."""
import math
import sys
import threading
import time


def read_drop_handle(handle):
    import ctypes as c
    from ctypes import wintypes as w
    query = c.WinDLL('shell32').DragQueryFileW
    query.restype, query.argtypes = w.UINT, (c.c_void_p, w.UINT, w.LPWSTR, w.UINT)
    count = query(handle, 0xffffffff, None, 0)
    if not 0 < count <= 100:
        return []
    paths = []
    for index in range(count):
        size = query(handle, index, None, 0)
        if not 0 < size <= 32767:
            return []
        value = c.create_unicode_buffer(size + 1)
        query(handle, index, value, size + 1)
        paths.append(value.value)
    return paths


def drop_rectangle(area, cursor, bounds):
    names = ('x', 'y', 'width', 'height', 'scale')
    if not isinstance(area, dict) or any(type(area.get(key)) not in (int, float)
            or not math.isfinite(area[key]) for key in names):
        raise ValueError('Invalid native drop area.')
    x, y, width, height, scale = (area[key] for key in names)
    if not (0 < width <= 16384 and 0 < height <= 16384 and 0 <= x <= width
            and 0 <= y <= height and .25 <= scale <= 8):
        raise ValueError('Invalid native drop area.')
    left, top = round(cursor[0] - x * scale), round(cursor[1] - y * scale)
    rect = (max(bounds[0], left), max(bounds[1], top),
            min(bounds[2], left + round(width * scale)), min(bounds[3], top + round(height * scale)))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ValueError('Native drop area is outside its window.')
    return rect


class NativeDrop:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.id = None
        self.result = {'status': 'unavailable'}
        self.error = ''
        self.stop = threading.Event()
        self.ready = threading.Event()

    def arm(self, request_id, area):
        # Validate even when Windows native reception is unavailable.
        drop_rectangle(area, (0, 0), (-200000, -200000, 200000, 200000))
        if sys.platform != 'win32':
            return {'status': 'unavailable'}
        with self.lock:
            if self.thread and self.thread.is_alive():
                return {'status': 'unavailable'}
            self.id, self.result = request_id, {'status': 'pending'}
            self.error = ''
            self.stop, self.ready = threading.Event(), threading.Event()
            self.thread = threading.Thread(target=self._run, args=(area,), daemon=True)
            self.thread.start()
        if not self.ready.wait(.4):
            self.stop.set()
            return {'status': 'unavailable'}
        return self.read(request_id)

    def read(self, request_id, cancel=False):
        with self.lock:
            if request_id != self.id:
                return {'status': 'cancelled'}
            if cancel:
                self.stop.set()
            thread = self.thread
            if not cancel:
                return dict(self.result)
        if thread:
            thread.join(.2)
        with self.lock:
            return dict(self.result) if request_id == self.id else {'status': 'cancelled'}

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(.75)

    def _set(self, status, **values):
        with self.lock:
            self.result = {'status': status, **values}

    def _run(self, area):
        try:
            self._window(area)
        except Exception as error:
            self.error = str(error)
            self._set('unavailable')
        finally:
            with self.lock:
                if self.result['status'] in ('pending', 'ready'):
                    self.result = {'status': 'cancelled'}
            self.ready.set()

    def _window(self, area):
        import ctypes as c
        from ctypes import wintypes as w

        user = c.WinDLL('user32', use_last_error=True)
        ole, kernel = c.WinDLL('ole32'), c.WinDLL('kernel32')
        ptr, result = c.c_void_p, c.c_long
        proc = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WindowClass(c.Structure):
            _fields_ = [('style', w.UINT), ('proc', proc), ('class_extra', c.c_int), ('window_extra', c.c_int),
                        ('instance', w.HINSTANCE), ('icon', w.HANDLE), ('cursor', w.HANDLE),
                        ('brush', w.HANDLE), ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]

        class Format(c.Structure):
            _fields_ = [('format', w.WORD), ('target', ptr), ('aspect', w.DWORD), ('index', c.c_long), ('medium', w.DWORD)]

        class Medium(c.Structure):
            _fields_ = [('type', w.DWORD), ('handle', ptr), ('release', ptr)]

        def api(library, name, returns, *arguments):
            function = getattr(library, name)
            function.restype, function.argtypes = returns, arguments
            return function

        default = api(user, 'DefWindowProcW', c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        destroy = api(user, 'DestroyWindow', w.BOOL, w.HWND)
        post = api(user, 'PostMessageW', w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        key = api(user, 'GetAsyncKeyState', c.c_short, c.c_int)
        release = api(ole, 'ReleaseStgMedium', None, c.POINTER(Medium))
        register_drop = api(ole, 'RegisterDragDrop', result, w.HWND, ptr)
        revoke_drop = api(ole, 'RevokeDragDrop', result, w.HWND)
        instance = api(kernel, 'GetModuleHandleW', w.HINSTANCE, w.LPCWSTR)(None)
        hwnd, registered, initialized, previous_dpi = None, False, False, None
        class_name = 'LaTeXSidebarDrop-' + self.id
        started, released = time.monotonic(), None
        accepted, references = False, 1
        format_ = Format(15, None, 1, -1, 1)  # CF_HDROP / DVASPECT_CONTENT / TYMED_HGLOBAL

        def data_method(data, index, signature):
            address = c.cast(data, c.POINTER(c.POINTER(ptr)))[0][index]
            return c.WINFUNCTYPE(result, ptr, *signature)(address)

        def files_from(data):
            medium = Medium()
            get_data = data_method(data, 3, (c.POINTER(Format), c.POINTER(Medium)))
            if get_data(data, c.byref(format_), c.byref(medium)) != 0:
                return []
            try:
                if medium.type != 1 or not medium.handle:
                    return []
                return read_drop_handle(medium.handle)
            finally:
                release(c.byref(medium))

        query_interface = c.WINFUNCTYPE(result, ptr, ptr, c.POINTER(ptr))
        reference = c.WINFUNCTYPE(w.ULONG, ptr)
        enter = c.WINFUNCTYPE(result, ptr, ptr, w.DWORD, w.POINT, c.POINTER(w.DWORD))
        over = c.WINFUNCTYPE(result, ptr, w.DWORD, w.POINT, c.POINTER(w.DWORD))
        leave = c.WINFUNCTYPE(result, ptr)

        @query_interface
        def interface(this, iid, output):
            if c.string_at(iid, 16) in (bytes.fromhex('0000000000000000c000000000000046'),
                                       bytes.fromhex('2201000000000000c000000000000046')):
                output[0] = this
                add_ref(this)
                return 0
            output[0] = None
            return -2147467262  # E_NOINTERFACE

        @reference
        def add_ref(_):
            nonlocal references
            references += 1
            return references

        @reference
        def release_ref(_):
            nonlocal references
            references = max(0, references - 1)
            return references

        @enter
        def drag_enter(_, data, keys, point, effect):
            nonlocal accepted
            try:
                accepted = data_method(data, 5, (c.POINTER(Format),))(data, c.byref(format_)) == 0
            except Exception:
                accepted = False
            effect[0] = effect[0] & 1 if accepted else 0
            return 0

        @over
        def drag_over(_, keys, point, effect):
            effect[0] = effect[0] & 1 if accepted else 0
            return 0

        @leave
        def drag_leave(_):
            post(hwnd, 0x10, 0, 0)
            return 0

        @enter
        def drop(_, data, keys, point, effect):
            try:
                files = files_from(data)
                effect[0] = effect[0] & 1 if files else 0
                self._set('dropped' if files else 'cancelled', files=files)
            except Exception:
                effect[0] = 0
                self._set('unavailable')
            post(hwnd, 0x10, 0, 0)
            return 0

        class VTable(c.Structure):
            _fields_ = [('interface', query_interface), ('add', reference), ('release', reference),
                        ('enter', enter), ('over', over), ('leave', leave), ('drop', enter)]

        class Target(c.Structure):
            _fields_ = [('table', c.POINTER(VTable))]

        table = VTable(interface, add_ref, release_ref, drag_enter, drag_over, drag_leave, drop)
        target = Target(c.pointer(table))

        @proc
        def window_proc(window, message, wp, lp):
            nonlocal released, registered
            if message == 0x113:  # WM_TIMER: bounded lifetime, release, Escape, client cancellation.
                if not key(1) & 0x8000:
                    released = released or time.monotonic()
                if (self.stop.is_set() or key(27) & 0x8000 or time.monotonic() - started > 15
                        or (released is not None and time.monotonic() - released > .2)):
                    if not self.stop.is_set() and not key(27) & 0x8000 and released is not None and self.result['status'] != 'dropped':
                        self._set('cancelled', reason='released')
                    post(window, 0x10, 0, 0)
                return 0
            if message == 0x10:
                if registered:
                    revoke_drop(window)
                    registered = False
                destroy(window)
                return 0
            if message == 2:
                user.PostQuitMessage(0)
                return 0
            return default(window, message, wp, lp)

        try:
            # Only this receiver thread uses physical pixels; global DPI settings stay unchanged.
            if hasattr(user, 'SetThreadDpiAwarenessContext'):
                previous_dpi = api(user, 'SetThreadDpiAwarenessContext', ptr, ptr)(ptr(-4))
            if api(ole, 'OleInitialize', result, ptr)(None) not in (0, 1):
                raise OSError('OLE initialization failed.')
            initialized = True
            cursor, bounds = w.POINT(), w.RECT()
            api(user, 'GetCursorPos', w.BOOL, c.POINTER(w.POINT))(c.byref(cursor))
            owner = api(user, 'WindowFromPoint', w.HWND, w.POINT)(cursor)
            owner = api(user, 'GetAncestor', w.HWND, w.HWND, w.UINT)(owner, 2)
            if not api(user, 'GetWindowRect', w.BOOL, w.HWND, c.POINTER(w.RECT))(owner, c.byref(bounds)):
                raise OSError('Drop owner is unavailable.')
            left, top, right, bottom = drop_rectangle(area, (cursor.x, cursor.y), (bounds.left, bounds.top, bounds.right, bounds.bottom))
            brush = api(c.WinDLL('gdi32'), 'GetStockObject', w.HANDLE, c.c_int)(4)
            window_class = WindowClass(0, window_proc, 0, 0, instance, None, None, brush, None, class_name)
            if not api(user, 'RegisterClassW', w.WORD, c.POINTER(WindowClass))(c.byref(window_class)):
                raise c.WinError(c.get_last_error())
            create = api(user, 'CreateWindowExW', w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                         c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, ptr)
            # Layered alpha 1 remains a real drop target; alpha 0/click-through would miss drops.
            hwnd = create(0x08080088, class_name, '', 0x80000000, left, top, right-left, bottom-top, owner, None, instance, None)
            if not hwnd:
                raise c.WinError(c.get_last_error())
            if register_drop(hwnd, c.byref(target)) != 0:
                raise OSError('Native drop registration failed.')
            registered = True
            if not api(user, 'SetLayeredWindowAttributes', w.BOOL, w.HWND, w.DWORD, c.c_ubyte, w.DWORD)(hwnd, 0, 1, 2):
                raise c.WinError(c.get_last_error())
            if not api(user, 'SetTimer', c.c_size_t, w.HWND, c.c_size_t, w.UINT, ptr)(hwnd, 1, 50, None):
                raise c.WinError(c.get_last_error())
            if self.stop.is_set():
                return
            if not api(user, 'SetWindowPos', w.BOOL, w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)(
                    hwnd, w.HWND(-1), left, top, right-left, bottom-top, 0x0050):
                raise c.WinError(c.get_last_error())
            self._set('ready')
            self.ready.set()
            message = w.MSG()
            get_message = api(user, 'GetMessageW', w.BOOL, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
            dispatch = api(user, 'DispatchMessageW', c.c_ssize_t, c.POINTER(w.MSG))
            while get_message(c.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(c.byref(message))
                dispatch(c.byref(message))
        finally:
            if registered:
                revoke_drop(hwnd)
            if hwnd:
                destroy(hwnd)
            api(user, 'UnregisterClassW', w.BOOL, w.LPCWSTR, w.HINSTANCE)(class_name, instance)
            if initialized:
                ole.OleUninitialize()
            if previous_dpi:
                user.SetThreadDpiAwarenessContext(previous_dpi)
