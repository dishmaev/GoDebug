import sublime
import sublime_plugin
import subprocess
import tempfile
import threading
import time
import traceback
import os
import sys
import re
import json
import shlex
import socket 

try:
    import Queue
    from resultparser import parse_result_line

    def sencode(s):
        return s.encode("utf-8")

    def sdecode(s):
        return s

    def bencode(s):
        return s
    def bdecode(s):
        return s
except:
    def sencode(s):
        return s

    def sdecode(s):
        return s

    def bencode(s):
        return s.encode("utf-8")

    def bdecode(s):
        return s.decode("utf-8")

    import queue as Queue
    from SublimeDelve.resultparser import parse_result_line

exec_settings = {}

def get_setting(key, default=None, view=None):
    try:
        if view is None:
            view = sublime.active_window().active_view()
        s = view.settings()

        # Then try user settings first
        if s.has("sublimedelve_%s" % key):
            return s.get("sublimedelve_%s" % key)
        # Try executable specific settings
        if exec_settings and key in exec_settings:
            return exec_settings[key]
    except:
        pass

    # Default settings
    return sublime.load_settings("SublimeDelve.sublime-settings").get(key, default)

def expand_path(value, window):
    if window is None:
        # Views can apparently be window less, in most instances getting
        # the active_window will be the right choice (for example when
        # previewing a file), but the one instance this is incorrect
        # is during Sublime Text 2 session restore. Apparently it's
        # possible for views to be windowless then too and since it's
        # possible that multiple windows are to be restored, the
        # "wrong" one for this view might be the active one and thus
        # ${project_path} will not be expanded correctly.
        #
        # This will have to remain a known documented issue unless
        # someone can think of something that should be done plugin
        # side to fix this.
        window = sublime.active_window()

    get_existing_files = \
        lambda m: [ path \
            for f in window.folders() \
            for path in [os.path.join(f, m.group('file'))] \
            if os.path.exists(path) \
        ]
    view = window.active_view()
    file_name = view.file_name();
    # replace variable with values
    if file_name:
        value = re.sub(r'\${file}', lambda m: file_name, value)
        value = re.sub(r'\${file_base_name}', lambda m: os.path.splitext(os.path.basename(file_name))[0], value)
    if os.getenv("HOME"):
        value = re.sub(r'\${home}', re.escape(os.getenv('HOME')), value)
    value = re.sub(r'\${env:(?P<variable>.*)}', lambda m: os.getenv(m.group('variable')), value)
    # search in projekt for path and get folder from path
    value = re.sub(r'\${project_path:(?P<file>[^}]+)}', lambda m: len(get_existing_files(m)) > 0 and get_existing_files(m)[0] or m.group('file'), value)
    value = re.sub(r'\${folder:(?P<file>.*)}', lambda m: os.path.dirname(m.group('file')), value)
    value = value.replace('\\', os.sep)
    value = value.replace('/', os.sep)

    return value

DEBUG = None
DEBUG_FILE = None
__debug_file_handle = None

dlv_lastline = ""
dlv_lastresult = ""
dlv_cursor = ""
dlv_cursor_position = 0
dlv_last_cursor_view = None

dlv_bkp_layout = {}
dlv_bkp_window = None
dlv_bkp_view = None
dlv_shutting_down = False
dlv_process = None
dlv_run_status = None
dlv_input_view = None
dlv_command_history = []
dlv_command_history_pos = 0
dlv_stack_frame = None
dlv_stack_index = 0

result_regex = re.compile("(?<=\^)[^,\"]*")


def normalize(filename):
    if filename is None:
        return None
    return os.path.abspath(os.path.normcase(filename))

def log_debug(line):
    global __debug_file_handle
    global DEBUG
    if DEBUG:
        try:
            if __debug_file_handle is None:
                if DEBUG_FILE == "stdout":
                    __debug_file_handle = sys.stdout
                else:
                    __debug_file_handle = open(DEBUG_FILE, 'a')
            __debug_file_handle.write(line)
        except:
            sublime.error_message("Couldn't write to the debug file. Debug writes will be disabled for this session.\n\nDebug file name used:\n%s\n\nError message\n:%s" % (DEBUG_FILE, traceback.format_exc()))
            DEBUG = False

class DlvView(object):
    def __init__(self, name, s=True, settingsprefix=None):
        self.queue = Queue.Queue()
        self.name = name
        self.closed = True
        self.doScroll = s
        self.view = None
        self.settingsprefix = settingsprefix
        self.timer = None
        self.lines = ""
        self.lock = threading.RLock()

    def is_open(self):
        return not self.closed

    def open_at_start(self):
        if self.settingsprefix is not None:
            return get_setting("%s_open" % self.settingsprefix, False)
        return False

    def open(self):
        if self.view is None or self.view.window() is None:
            if self.settingsprefix is not None:
                sublime.active_window().focus_group(get_setting("%s_group" % self.settingsprefix, 0))
            self.create_view()

    def close(self):
        if self.view is not None:
            if self.settingsprefix is not None:
                sublime.active_window().focus_group(get_setting("%s_group" % self.settingsprefix, 0))
            self.destroy_view()

    def should_update(self):
        return self.is_open() and is_running() and dlv_run_status == "stopped"

    def set_syntax(self, syntax):
        if self.is_open():
            self.get_view().set_syntax_file(syntax)


    def timed_add(self):
        try:
            self.lock.acquire()
            lines = self.lines
            self.lines = ""
            self.timer = None
            self.queue.put((self.do_add_line, lines))
            sublime.set_timeout(self.update, 0)
        finally:
            self.lock.release()


    def add_line(self, line, now=True):
        if self.is_open():
            try:
                self.lock.acquire()
                self.lines += line
                if self.timer:
                    self.timer.cancel()
                if self.lines.count("\n") > 10 or now:
                    self.timed_add()
                else:
                    self.timer = threading.Timer(0.1, self.timed_add)
                    self.timer.start()
            finally:
                self.lock.release()

    def scroll(self, line):
        if self.is_open():
            self.queue.put((self.do_scroll, line))
            sublime.set_timeout(self.update, 0)

    def set_viewport_position(self, pos):
        if self.is_open():
            self.queue.put((self.do_set_viewport_position, pos))
            sublime.set_timeout(self.update, 0)

    def clear(self, now=False):
        if self.is_open():
            if not now:
                self.queue.put((self.do_clear, None))
                sublime.set_timeout(self.update, 0)
            else:
                self.do_clear(None)

    def create_view(self):
        self.view = sublime.active_window().new_file()
        self.view.set_name(self.name)
        self.view.set_scratch(True)
        self.view.set_read_only(True)
        # Setting command_mode to false so that vintage
        # does not eat the "enter" keybinding
        self.view.settings().set('command_mode', False)
        self.closed = False

    def destroy_view(self):
        sublime.active_window().focus_view(self.view)
        sublime.active_window().run_command("close")
        self.view = None
        self.closed = True

    def is_closed(self):
        return self.closed

    def was_closed(self):
        self.closed = True

    def fold_all(self):
        if self.is_open():
            self.queue.put((self.do_fold_all, None))

    def get_view(self):
        return self.view

    def do_add_line(self, line):
        self.view.run_command("dlv_view_add_line", {"line": line, "doScroll": self.doScroll})

    def do_fold_all(self, data):
        self.view.run_command("fold_all")

    def do_clear(self, data):
        self.view.run_command("dlv_view_clear")

    def do_scroll(self, data):
        self.view.run_command("goto_line", {"line": data + 1})

    def do_set_viewport_position(self, data):
        # Shouldn't have to call viewport_extent, but it
        # seems to flush whatever value is stale so that
        # the following set_viewport_position works.
        # Keeping it around as a WAR until it's fixed
        # in Sublime Text 2.
        self.view.viewport_extent()
        self.view.set_viewport_position(data, False)

    def update(self):
        if not self.is_open():
            return
        try:
            while not self.queue.empty():
                cmd, data = self.queue.get()
                try:
                    cmd(data)
                finally:
                    self.queue.task_done()
        except:
            traceback.print_exc()

    def on_session_ended(self):
        if get_setting("%s_clear_on_end" % self.settingsprefix, True):
            self.clear()

class DlvViewClear(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.set_read_only(False)
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.set_read_only(True)

class DlvViewAddLine(sublime_plugin.TextCommand):
    def run(self, edit, line, doScroll):
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), line)
        self.view.set_read_only(True)
        if doScroll:
            self.view.show(self.view.size())

class DlvBreakpoint(object):
    def __init__(self, filename="", line=0, addr=""):
        self.original_filename = normalize(filename)
        self.original_line = line
        self.addr = addr
        self.clear()
        self.add()

    @property
    def line(self):
        if self.number != -1:
            return self.resolved_line
        return self.original_line

    @property
    def filename(self):
        if self.number != -1:
            return normalize(self.resolved_filename)
        return normalize(self.original_filename)

    def clear(self):
        self.resolved_filename = ""
        self.resolved_line = 0
        self.number = -1

    def breakpoint_added(self, res):
        if "bkpt" not in res:
            return
        bp = res["bkpt"]
        if "fullname" in bp:
            self.resolved_filename = bp["fullname"]
        elif "file" in bp:
            self.resolved_filename = bp["file"]
        elif "original-location" in bp and self.addr == 0:
            self.resolved_filename = bp["original-location"].split(":", 1)[0]
            self.resolved_line = int(bp["original-location"].split(":", 1)[1])

        if self.resolved_line == 0 and "line" in bp:
            self.resolved_line = int(bp["line"])

        if not "/" in self.resolved_filename and not "\\" in self.resolved_filename:
            self.resolved_filename = self.original_filename
        self.number = int(bp["number"])

    def insert(self):
        # TODO: does removing the unicode-escape break things? what's the proper way to handle this in python3?
        # cmd = "-break-insert \"\\\"%s\\\":%d\"" % (self.original_filename.encode("unicode-escape"), self.original_line)
        break_cmd = "-break-insert"
        if self.addr != "":
            cmd = "%s *%s" % (break_cmd, self.addr)
        else:
            cmd = "%s \"\\\"%s\\\":%d\"" % (break_cmd, self.original_filename.replace("\\", "/"), self.original_line)
#        out = run_cmd(cmd, True)
        if get_result(out) == "error":
            return
        res = parse_result_line(out)
        if "bkpt" not in res and "matches" in res:
            for match in res["matches"]["b"]:
                cmd = "%s *%s" % (break_cmd, match["addr"])
#                out = run_cmd(cmd, True)
                if get_result(out) == "error":
                    return
                res = parse_result_line(out)
                self.breakpoint_added(res)
        else:
            self.breakpoint_added(res)

    def add(self):
        if is_running():
            res = wait_until_stopped()
            self.insert()
            if res:
                resume()

    def remove(self):
        if is_running():
            res = wait_until_stopped()
#            run_cmd("-break-delete %s" % self.number)
            if res:
                resume()

    def format(self):
        return "%d - %s:%d\n" % (self.number, self.filename, self.line)

class DlvWatch(DlvBreakpoint):
    def __init__(self, exp):
        self.exp = exp
        super(DlvWatch, self).__init__(None, -1)

    def insert(self):
#        out = run_cmd("-break-watch %s" % self.exp, True)
        res = parse_result_line(out)
        if get_result(out) == "error":
            return

        self.number = int(res["wpt"]["number"])

    def format(self):
        return "%d - watch: %s\n" % (self.number, self.exp)

breakpoints = []

class DlvBreakpointView(DlvView):
    def __init__(self):
        super(DlvBreakpointView, self).__init__("Delve Breakpoints", s=False, settingsprefix="breakpoints")
        self.breakpoints = []

    def open(self):
        super(DlvBreakpointView, self).open()
        #self.set_syntax("Packages/SublimeDelve/dlv_disasm.tmLanguage")
        self.get_view().settings().set("word_wrap", False)
        if self.is_open():
            self.update_view()

    def on_session_ended(self):
        # Intentionally not calling super
        for bkpt in self.breakpoints:
            bkpt.clear()

    def update_marker(self, view):
        bps = []
        fn = view.file_name()
        if fn is None:
            return
        fn = normalize(fn)
        for bkpt in self.breakpoints:
            if bkpt.filename == fn and not (bkpt.line == dlv_cursor_position and fn == dlv_cursor):
                bps.append(view.full_line(view.text_point(bkpt.line - 1, 0)))

        view.add_regions("sublimedelve.breakpoints", bps,
                            get_setting("breakpoint_scope", "keyword.dlv"),
                            get_setting("breakpoint_icon", "circle"),
                            sublime.HIDDEN)

    def find_breakpoint(self, filename, line):
        filename = normalize(filename)
        for bkpt in self.breakpoints:
            if bkpt.filename == filename and bkpt.line == line:
                return bkpt
        return None

    def find_breakpoint_addr(self, addr):
        for bkpt in self.breakpoints:
            if bkpt.addr == addr:
                return bkpt
        return None

    def toggle_watch(self, exp):
        add = True
        for bkpt in self.breakpoints:
            if isinstance(bkpt, DlvWatch) and bkpt.exp == exp:
                add = False
                bkpt.remove()
                self.breakpoints.remove(bkpt)
                break

        if add:
            self.breakpoints.append(DlvWatch(exp))
        self.update_view()

    def toggle_breakpoint_addr(self, addr):
        bkpt = self.find_breakpoint_addr(addr)
        if bkpt:
            bkpt.remove()
            self.breakpoints.remove(bkpt)
        else:
            self.breakpoints.append(DlvBreakpoint(addr=addr))
        self.update_view()

    def toggle_breakpoint(self, filename, line):
        bkpt = self.find_breakpoint(filename, line)
        if bkpt:
            bkpt.remove()
            self.breakpoints.remove(bkpt)
        else:
            self.breakpoints.append(DlvBreakpoint(filename, line))
        self.update_view()

    def sync_breakpoints(self):
        global breakpoints
        for bkpt in self.breakpoints:
            bkpt.add()
        update_view_markers()
        self.update_view()

    def update_view(self):
        if not self.is_open():
            return
        pos = self.get_view().viewport_position()
        self.clear()
        self.breakpoints.sort(key=lambda b: (b.number, b.filename, b.line))
        for bkpt in self.breakpoints:
            self.add_line(bkpt.format())
        self.set_viewport_position(pos)
        self.update()

class DlvSessionView(DlvView):
    def __init__(self):
        super(DlvSessionView, self).__init__("Delve Session", s=False, settingsprefix="session")

    def open(self):
        super(DlvSessionView, self).open()
        self.set_syntax("Packages/SublimeDelve/dlv_session.tmLanguage")

dlv_session_view = DlvSessionView()
dlv_console_view = DlvView("Delve Console", settingsprefix="console")
dlv_breakpoint_view = DlvBreakpointView()
dlv_views = [dlv_session_view, dlv_console_view, dlv_breakpoint_view]

def update_view_markers(view=None):
    if view is None:
        view = sublime.active_window().active_view()

    fn = view.file_name()
    if fn is not None:
        fn = normalize(fn)
    pos_scope = get_setting("position_scope", "entity.name.class")
    pos_icon = get_setting("position_icon", "bookmark")

    cursor = []
    if fn == dlv_cursor and dlv_cursor_position != 0:
        cursor.append(view.full_line(view.text_point(dlv_cursor_position - 1, 0)))
    global dlv_last_cursor_view
    if dlv_last_cursor_view is not None:
        dlv_last_cursor_view.erase_regions("sublimedelve.position")
    dlv_last_cursor_view = view
    view.add_regions("sublimedelve.position", cursor, pos_scope, pos_icon, sublime.HIDDEN)

#    gdb_callstack_view.update_marker(pos_scope, pos_icon)
#    gdb_threads_view.update_marker(pos_scope, pos_icon)
    dlv_breakpoint_view.update_marker(view)

def get_result(line):
    res = result_regex.search(line).group(0)
    if res == "error" and not get_setting("i_know_how_to_use_dlv_thank_you_very_much", False):
        sublime.error_message("%s\n\n%s" % (line, "\n".join(traceback.format_stack())))
    return res

count = 0

def run_cmd(cmd, block=False, mimode=False, timeout=10):
    global count
    if not is_running():
        return "0^error,msg=\"no session running\""

    timeoutcount = timeout/0.001

    ### handle a list of commands by recursively calling run_cmd
    if isinstance(cmd, list):
        for c in cmd:
            run_cmd(c, block, mimode, timeout)
        return count

    if mimode:
        count = count + 1
        cmd = "%d%s\n" % (count, cmd)
    else:
        cmd = "%s\n\n" % cmd
    log_debug(cmd)
    if dlv_session_view is not None:
        dlv_session_view.add_line(cmd, False)
    dlv_process.stdin.write(cmd.encode(sys.getdefaultencoding()))
    dlv_process.stdin.flush()
    # if block:
    #     countstr = "%d^" % count
    #     i = 0
    #     while not dlv_lastresult.startswith(countstr) and i < timeoutcount:
    #         i += 1
    #         time.sleep(0.001)
    #     if i >= timeoutcount:
    #         raise ValueError("Command \"%s\" took longer than %d seconds to perform?" % (cmd, timeout))
    #     return dlv_lastresult
    return count

def resume():
    global dlv_run_status
    dlv_run_status = "running"
#    run_cmd("-exec-continue", True)

def wait_until_stopped():
    global dlv_run_status
    if dlv_run_status == "running":
        dlv_run_status = "stopped"
        return True
        # result = run_cmd("-exec-interrupt --all", True)
        # if "^done" in result:
        #     i = 0
        #     while not "stopped" in dlv_run_status and i < 100:
        #         i = i + 1
        #         time.sleep(0.1)
        #     if i >= 100:
        #         print("I'm confused... I think status is %s, but it seems it wasn't..." % dlv_run_status)
        #         return False
        #     return True
    return False

class DlvToggleBreakpoint(sublime_plugin.TextCommand):
    def run(self, edit):
        fn = self.view.file_name()
        if dlv_breakpoint_view.is_open() and self.view.id() == dlv_breakpoint_view.get_view().id():
            row = self.view.rowcol(self.view.sel()[0].begin())[0]
            if row < len(dlv_breakpoint_view.breakpoints):
                dlv_breakpoint_view.breakpoints[row].remove()
                dlv_breakpoint_view.breakpoints.pop(row)
                dlv_breakpoint_view.update_view()
#        elif gdb_variables_view.is_open() and self.view.id() == gdb_variables_view.get_view().id():
#            var = gdb_variables_view.get_variable_at_line(self.view.rowcol(self.view.sel()[0].begin())[0])
#            if var is not None:
#                dlv_breakpoint_view.toggle_watch(var.get_expression())
#        elif gdb_disassembly_view.is_open() and self.view.id() == gdb_disassembly_view.get_view().id():
#           for sel in self.view.sel():
#                line = self.view.substr(self.view.line(sel))
#                addr = re.match(r"^[^:]+", line)
#                if addr:
#                   dlv_breakpoint_view.toggle_breakpoint_addr(addr.group(0))
        elif fn is not None:
            for sel in self.view.sel():
                line, col = self.view.rowcol(sel.a)
                dlv_breakpoint_view.toggle_breakpoint(fn, line + 1)
        print("test1")
        update_view_markers(self.view)

    def is_enabled(self):
        return is_gosource(sublime.active_window().active_view().file_name())

    def is_visible(self):
        return is_gosource(sublime.active_window().active_view().file_name())

def session_ended_status_message():
    sublime.status_message("Delve session ended")

def dlvoutput(pipe):
    global dlv_process
    global dlv_lastresult
    global dlv_lastline
    global dlv_stack_frame
    global dlv_run_status
    global dlv_stack_index
    command_result_regex = re.compile("^\d+\^")
    run_status_regex = re.compile("(^\d*\*)([^,]+)")

    while True:
        try:
            line = pipe.readline()
            if len(line) == 0:
                log_debug("dlv_%s: broken pipe\n" % ("stdout" if pipe == dlv_process.stdout else "stderr"))
                break
            line = line.strip().decode(sys.getdefaultencoding())
            log_debug("dlv_%s: %s\n" % ("stdout" if pipe == dlv_process.stdout else "stderr", line))
            dlv_session_view.add_line("%s\n" % line, False)

            if pipe != dlv_process.stdout:
                continue

            run_status = run_status_regex.match(line)
            if run_status is not None:
                dlv_run_status = run_status.group(2)
                reason = re.search("(?<=reason=\")[a-zA-Z0-9\-]+(?=\")", line)
                # if reason is not None and reason.group(0).startswith("exited"):
                #     log_debug("gdb: exiting %s" % line)
                #     run_cmd("-gdb-exit")
                # elif not "running" in dlv_run_status and not dlv_shutting_down:
                #     thread_id = re.search('thread-id="(\d+)"', line)
                #     if thread_id is not None:
                #         gdb_threads_view.select_thread(int(thread_id.group(1)))
                #     sublime.set_timeout(update_cursor, 0)
            if not line.startswith("(dlv)"):
                dlv_lastline = line
            if command_result_regex.match(line) is not None:
                dlv_lastresult = line

            if line.startswith("~"):
                dlv_console_view.add_line(
                    line[2:-1].replace("\\n", "\n").replace("\\\"", "\"").replace("\\t", "\t"), False)

        except:
            traceback.print_exc()

    if pipe == dlv_process.stdout:
        log_debug("Delve session ended\n")
        dlv_session_view.add_line("Delve session ended\n")
        sublime.set_timeout(session_ended_status_message, 0)
        dlv_stack_frame = None
    global dlv_cursor_position
    dlv_stack_index = -1
    dlv_cursor_position = 0
    dlv_run_status = None
    sublime.set_timeout(update_view_markers, 0)

    for view in dlv_views:
        sublime.set_timeout(view.on_session_ended, 0)
    sublime.set_timeout(cleanup, 0)

def cleanup():
    global __debug_file_handle
    if get_setting("close_views", True):
        for view in dlv_views:
            view.close()
    if get_setting("push_pop_layout", True):
        dlv_bkp_window.set_layout(dlv_bkp_layout)
        dlv_bkp_window.focus_view(dlv_bkp_view)
    if __debug_file_handle is not None:
        if __debug_file_handle != sys.stdout:
            __debug_file_handle.close()
            __debug_file_handle = None

def programio(pty, tty):
    global dlv_process
    exception_count = 0
    class MyFD(object):
        def __init__(self, pty, tty):
            self.pty = pty
            self.tty = tty
            self.off = 0
            self.queue = Queue.Queue()

        def on_done(self, s):
            log_debug("programinput: %s\n" % s)
            log_debug("Wrote: %d bytes\n" % os.write(self.pty, bencode("%s\n" % s)))
            os.fsync(self.pty)
            self.queue.put(None)

        def get_input(self):
            sublime.active_window().show_input_panel("stdin input expected: ", "input", self.on_done, None, lambda: self.queue.put(None))

        def readline(self):
            ret = ""
            while True:
                if not os.isatty(self.pty):
                    s = os.fstat(self.pty)
                    if self.off >= s.st_size and len(ret) == 0:
                        return ret
                else:
                    import select
                    r, w, x = select.select([self.pty], [self.pty], [], 5.0)
                    if len(r) == 0 and len(w) != 0:
                        log_debug("Ready for input\n")
                        sublime.set_timeout(self.get_input, 0)
                        self.queue.get()
                        continue
                    elif len(r) == 0:
                        log_debug("timed out\n")
                        break
                read = os.read(self.pty, 1)
                self.off += len(read)
                ret += bdecode(read)
                if len(read) == 0 or ret.endswith("\n"):
                    break
            return ret

        def close(self):
            os.close(self.pty)
            if self.tty:
                os.close(self.tty)

    pipe = MyFD(pty, tty)

    while exception_count < 100:
        try:
            line = pipe.readline()
            if len(line) > 0:
                log_debug("programoutput: %s" % line)
                dlv_console_view.add_line(line, False)
            else:
                if dlv_process.poll() is not None:
                    break
                time.sleep(0.1)
        except:
            traceback.print_exc()
            exception_count = exception_count + 1
    if pipe is not None:
        pipe.close()

def set_input(edit, text):
    dlv_input_view.erase(edit, sublime.Region(0, dlv_input_view.size()))
    dlv_input_view.insert(edit, 0, text)

class DlvPrevCmd(sublime_plugin.TextCommand):
    def run(self, edit):
        global dlv_command_history_pos
        if dlv_command_history_pos > 0:
            dlv_command_history_pos -= 1
        if dlv_command_history_pos < len(dlv_command_history):
            set_input(edit, dlv_command_history[dlv_command_history_pos])

class DlvNextCmd(sublime_plugin.TextCommand):
    def run(self, edit):
        global dlv_command_history_pos
        if dlv_command_history_pos < len(dlv_command_history):
            dlv_command_history_pos += 1
        if dlv_command_history_pos < len(dlv_command_history):
            set_input(edit, dlv_command_history[dlv_command_history_pos])
        else:
            set_input(edit, "")

def show_input():
    global dlv_input_view
    global dlv_command_history_pos
    dlv_command_history_pos = len(dlv_command_history)
    dlv_input_view = sublime.active_window().show_input_panel("Delve", "", input_on_done, input_on_change, input_on_cancel)

def input_on_done(s):
    if s.strip() != "quit" and s.strip() != "exit" and s.strip() != "q":
        show_input()
        dlv_command_history.append(s)
    run_cmd(s)

def input_on_cancel():
    pass


def input_on_change(s):
    pass

def is_running():
    return dlv_process is not None and dlv_process.poll() is None

def is_gosource(s):
    if s is None:
        return False
    ext = os.path.splitext(os.path.basename(s))[1]
    if ext is not None  and ext == ".go":
        return True
    else:
#        log_debug("It is not Go source file")
        return False

class DlvInput(sublime_plugin.WindowCommand):
    def run(self):
        show_input()

class DlvLaunchDebug(sublime_plugin.WindowCommand):
    def run(self):
        dlv_launch = DlvLaunch(self.window, "debug")
        dlv_launch.start()

    def is_enabled(self):
        return not is_running()

    def is_visible(self):
        return not is_running()

class DlvLaunchTest(sublime_plugin.WindowCommand):
    def run(self):
        dlv_launch = DlvLaunch(self.window, "test")
        dlv_launch.start()

    def is_enabled(self):
        return not is_running()

    def is_visible(self):
        return not is_running()

class DlvLaunch():
    def __init__(self, window, configuration):
        self.window = window
        self.configuration = configuration

    def start(self):
        global exec_settings
        s = self.window.active_view().settings()
        exec_choices = s.get("sublimedelve_executables")

        if exec_choices is None or type(exec_choices) != dict:
            # No executable specific settings, go ahead and launch
            exec_settings = {}
            self.launch()
            return

        def on_choose(index):
            global exec_settings
            if index == -1:
                # User cancelled the panel, abort launch
                return
            exec_name = list(exec_choices)[index]
            exec_settings = exec_choices[exec_name]
            self.launch()

        self.window.show_quick_panel(list(exec_choices), on_choose)

    def launch(self):
        global dlv_process
        global dlv_run_status
        global dlv_bkp_window
        global dlv_bkp_view
        global dlv_bkp_layout
        global dlv_shutting_down
        global DEBUG
        global DEBUG_FILE
        cmd = []
        view = self.window.active_view()
        DEBUG = get_setting("debug", True, view)
        DEBUG_FILE = expand_path(get_setting("debug_file", "stdout", view), self.window)
        if DEBUG:
            print("Will write debug info to file: %s" % DEBUG_FILE)
        if dlv_process is None or dlv_process.poll() is not None:
            workingdir = expand_path(get_setting("workingdir", "${folder:${file}}", view), self.window)
            if workingdir == "" or not os.path.exists(workingdir):
                print("The directory given does not exist: %s" % workingdir)
                sublime.error_message("You have not configured the plugin correctly, the default configuration file and your user configuration file will open in a new window")
                sublime.run_command("new_window")
                wnd = sublime.active_window()
                wnd.set_layout({
                    "cols": [0.0, 0.5, 1.0],
                    "rows": [0, 1.0],
                    "cells": [[0,0,1,1], [1,0,2,1]],
                })
                v = wnd.open_file("%s/SublimeDelve/SublimeDelve.sublime-settings" % sublime.packages_path())
                v2 = wnd.open_file("%s/User/SublimeDelve.sublime-settings" % sublime.packages_path())
                wnd.set_view_index(v2, 1, 0)
                return
            cmd.append(get_setting("dlv_cmd", "dlv", view))
            if self.configuration == "debug":
                cmd.append(get_setting("dlv_debug_config", "debug", view))
            else:
                cmd.append(get_setting("dlv_test_config", "test", view))
            cmd.append("--headless")
            cmd.append("--accept-multiclient")
            cmd.append("--api-version=2")
            if get_setting("dlv_log", "false", view) == "true":
                cmd.append("--log")
            cmd.append("--listen=%s:%s" % (get_setting("dlv_host", "localhost", view), get_setting("dlv_port", "3456", view)))
            args = get_setting("args", "", view)
            if args is not None and args != "":
                cmd.append("--")
                cmd.append(args)
            log_debug("Running: %s\n" % " ".join(cmd))
            log_debug("In directory: %s\n" % workingdir)            
            dlv_process = subprocess.Popen(cmd, shell=False, cwd=workingdir,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            log_debug("Process: %s\n" % dlv_process)
            dlv_bkp_window = sublime.active_window()
            #back up current layout before opening the debug one
            #it will be restored when debug is finished
            dlv_bkp_layout = dlv_bkp_window.get_layout()
            dlv_bkp_view = dlv_bkp_window.active_view()
            dlv_bkp_window.set_layout(
                get_setting("layout",
                    {
                        "cols": [0.0, 0.5, 1.0],
                        "rows": [0.0, 0.75, 1.0],
                        "cells": [[0, 0, 2, 1], [0, 1, 1, 2], [1, 1, 2, 2]]
                    }
                )
            )
            for view in dlv_views:
                if view.is_closed() and view.open_at_start():
                    view.open()
                view.clear()
            dlv_shutting_down = False

            t = threading.Thread(target=dlvoutput, args=(dlv_process.stdout,))
            t.start()
            t = threading.Thread(target=dlvoutput, args=(dlv_process.stderr,))
            t.start()
            
            try:
                raise Exception("Nope")
                pty, tty = os.openpty()
                name = os.ttyname(tty)
            except:
                pipe, name = tempfile.mkstemp()
                pty, tty = pipe, None
            log_debug("pty: %s, tty: %s, name: %s" % (pty, tty, name))

            t = threading.Thread(target=programio, args=(pty,tty))
            t.start()

            dlv_breakpoint_view.sync_breakpoints()
            if(get_setting("run_after_init", True)):
                dlv_run_status = "running"
#                run_cmd(get_setting("exec_cmd", "-exec-run"), True)
            else:
                dlv_run_status = "stopped"
            show_input()
        else:
            sublime.status_message("Delve is already running!")

class DlvExit(sublime_plugin.WindowCommand):
    def run(self):
        global dlv_shutting_down
        dlv_shutting_down = True
        wait_until_stopped()
        try:
#            outs, errs = dlv_process.communicate("exit".encode(sys.getdefaultencoding()), timeout=get_setting("dlv_timeout", 20))
            dlv_process.terminate()
            log_debug("dlv_stdout: Delve normal exit\n")
        except:
            traceback.print_exc()
            dlv_process.kill()
            log_debug("dlv_stderr: Delve kill after timeout\n")

    def is_enabled(self):
        return is_running()

    def is_visible(self):
        return is_running()

class DlvEventListener(sublime_plugin.EventListener):
    def on_query_context(self, view, key, operator, operand, match_all):
        if key == "dlv_running":
            return is_running() == operand
        elif key == "dlv_input_view":
            return dlv_input_view is not None and view.id() == dlv_input_view.id()
        # elif key.startswith("dlv_"):
        #     v = gdb_variables_view
        #     if key.startswith("dlv_register_view"):
        #         v = gdb_register_view
        #     elif key.startswith("dlv_disassembly_view"):
        #         v = gdb_disassembly_view
        #     if key.endswith("open"):
        #         return v.is_open() == operand
        #     else:
        #         if v.get_view() is None:
        #             return False == operand
        #         return (view.id() == v.get_view().id()) == operand
        return None

    def on_activated(self, view):
        if view.file_name() is not None:
            update_view_markers(view)

    def on_load(self, view):
        if view.file_name() is not None:
            update_view_markers(view)

    def on_close(self, view):
        for v in dlv_views:
            if v.is_open() and view.id() == v.get_view().id():
                v.was_closed()
                break

class DlvOpenSessionView(sublime_plugin.WindowCommand):
    def run(self):
        dlv_session_view.open()

    def is_enabled(self):
        return not dlv_session_view.is_open()

    def is_visible(self):
        return not dlv_session_view.is_open()

class DlvOpenConsoleView(sublime_plugin.WindowCommand):
    def run(self):
        dlv_console_view.open()

    def is_enabled(self):
        return not dlv_console_view.is_open()

    def is_visible(self):
        return not dlv_console_view.is_open()

class DlvOpenBreakpointView(sublime_plugin.WindowCommand):
    def run(self):
        dlv_breakpoint_view.open()

    def is_enabled(self):
        return not dlv_breakpoint_view.is_open()

    def is_visible(self):
        return not dlv_breakpoint_view.is_open()

class DlvTest(sublime_plugin.WindowCommand):
    def run(self):
        thing=""
        callmethod = {"method":"RPCServer.CreateBreakpoint","params":[{"Breakpoint":{"file":"/home/dmitry/Projects/gotest/hello.go","line":11}}],"jsonrpc": "2.0","id":3}
        message = json.dumps(callmethod)
        message_bytes = message.encode('utf-8')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(get_setting("dlv_timeout", "20"))
        sock.connect(('localhost', 3456))
        sock.send(message_bytes)
        # response_bytes = []
        # while True:
        #     try:
        #         data = sock.recv(4096)
        #     except socket.timeout:
        #         break
        #     if not data: 
        #         break
        #     response_bytes.append(data)
        #     if len(data) < 4096:
        #         break
        sock.close()
#        response = ''.join(response_bytes)
#        response = join(str(response))
#        print(response)

#         headers = {'content-type': 'application/json'}
#         callmethod = {"method":"RPCServer.CreateBreakpoint","params":[{"Breakpoint":{"addr":4199019}}],"jsonrpc": "2.0","id":3}
#         data = json.dumps(callmethod)
#         data = urllib.parse.urlencode(callmethod)
#         data = data.encode('ascii')
# #        url = "http://localhost:3456?%s" % data
#         req = urllib.request.Request('http://localhost:3456', data)
#         req.add_header('content-type', 'application/json')
#         try:
#             with urllib.request.urlopen(req) as response:
#                 info = response.read().decode('utf-8')
# #            thing = json.loads(str(info))
#         except:
#             traceback.print_exc()
# #        log_debug(thing)