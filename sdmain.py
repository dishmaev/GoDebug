import sublime
import sublime_plugin
import subprocess
import threading
import traceback
import os
import json
import socket 
import sys 
import re

from SublimeDelve.sdconst import dlv_const
from SublimeDelve.sdlogger import dlv_logger
from SublimeDelve.jsonrpctcp_client import dlv_connect

from SublimeDelve.sdview import DlvView
from SublimeDelve.sdobjecttype import DlvObjectType

dlv_panel_layout = {}
dlv_panel_window = None
dlv_panel_view = None

dlv_input_view = None
dlv_command_history = []
dlv_command_history_pos = 0

dlv_server_process = None
dlv_process = None

def normalize(file):
    if file is None:
        return None
    return os.path.abspath(os.path.normcase(file))

def set_input(edit, text):
    dlv_input_view.erase(edit, sublime.Region(0, dlv_input_view.size()))
    dlv_input_view.insert(edit, 0, text)

def show_input():
    global dlv_input_view
    global dlv_command_history_pos
   
    dlv_command_history_pos = len(dlv_command_history)
    dlv_input_view = sublime.active_window().show_input_panel("Delve", "", input_on_done, input_on_change, input_on_cancel)

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

def input_on_done(s):
    if s.strip() != "quit" and s.strip() != "exit" and s.strip() != "q":
        dlv_command_history.append(s)
        show_input()
    run_cmd(s)

def input_on_cancel():
    pass

def input_on_change(s):
    pass

def is_running():
    return dlv_process is not None and dlv_process.poll() is None

def is_server_running():
    return dlv_server_process is not None and dlv_server_process.poll() is None

def is_gosource(s):
    if s is None:
        return False
    ext = os.path.splitext(os.path.basename(s))[1]
    if ext is not None  and ext == ".go":
        return True
    else:
        return False

def is_local_mode():
    global dlv_const

    return dlv_const.MODE in [dlv_const.MODE_DEBUG, dlv_const.MODE_TEST]

def run_cmd(cmd):
    global dlv_logger

    if not is_running():
        message = "Delve session not found, need to start debugging"
        dlv_logger.debug(message)
        sublime.status_message(message)
        return

    if isinstance(cmd, list):
        for c in cmd:
            run_cmd(c)
        return
    message = "Input command: %s" % cmd
    dlv_session_view.add_line(message)
    dlv_logger.info(message)
    dlv_process.stdin.write(cmd + "\n")
    dlv_process.stdin.flush()
    return

class DlvBreakpointType(DlvObjectType):
    def __init__(self, file, line, name = None, **kwargs):
        super(DlvBreakpointType, self).__init__("Breakpoint", **kwargs)
        self.__file = file
        self.__line = line
        self.__name = name
        self._add()

    def __getattr__(self, attr):
        if attr == "name" and self.__name is not None:
            return self.__name
        return super(DlvBreakpointType, self).__getattr__(attr)

    @property
    def _as_parm(self):
        response = super(DlvBreakpointType, self)._as_parm
        response[self._object_name]['file'] = self.__file
        response[self._object_name]['line'] = self.__line
        if self.__name is not None:
            response[self._object_name]['name'] = self.__name
        return response

    @property
    def line(self):
        return self.__line

    @property
    def file(self):
        return normalize(self.__file)

    def _add(self):
        global dlv_logger

        if is_running():
            try:
                response = dlv_connect.RPCServer.CreateBreakpoint(self._as_parm)
                self._update(response)
            except:
                traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
                dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())

    def _remove(self):
        global dlv_logger

        if is_running():
            try:
                result = dlv_connect.RPCServer.ClearBreakpoint({"id": self.id, "name": self.name})
            except:
                traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
                dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())

    def _format(self):
        if self.__name is not None:
            return "%s %s:%d" % (self.name, self.file, self.line)
        else:
            return "%s:%d" % (self.file, self.line)

class DlvBreakpointView(DlvView):
    def __init__(self):
        global dlv_const

        super(DlvBreakpointView, self).__init__(dlv_const.BREAKPOINTS_VIEW, "Delve Breakpoints", scroll=False)
        self.breakpoints = []

    def open(self):
        super(DlvBreakpointView, self).open()
        if self.is_open():
            self.update_view()

    def update_marker(self, view):
        bps = []
        file = view.file_name()
        if file is None:
            return
        file = normalize(file)
        for bkpt in self.breakpoints:
            if bkpt.file == file:
                bps.append(view.full_line(view.text_point(bkpt.line - 1, 0)))

        view.add_regions("sublimedelve.breakpoints", bps, "keyword.dlv", "circle", sublime.HIDDEN)

    def clear(self):
        self.update_view()
                            
    def update_view(self):
        if not self.is_open():
            return
        #two line below - part of base.clear, for prevent recursion if call self.clear()
        self.view.run_command("dlv_view_clear")
        self.counter = 0
        pos = self.get_view().viewport_position()
        self.breakpoints.sort(key=lambda b: (b.file, b.line))
        for bkpt in self.breakpoints:
            self.add_line(bkpt._format())

    def find_breakpoint(self, file, line):
        file = normalize(file)
        for bkpt in self.breakpoints:
            if bkpt.file == file and bkpt.line == line:
                return bkpt
        return None

    def toggle_breakpoint(self, file, line):
        bkpt = self.find_breakpoint(file, line)
        if bkpt:
            bkpt._remove()
            self.breakpoints.remove(bkpt)
        else:
            self.breakpoints.append(DlvBreakpointType(file, line))
        self.update_view()

    def sync_breakpoints(self):
        for bkpt in self.breakpoints:
            bkpt._add()

dlv_session_view = DlvView(dlv_const.SESSION_VIEW, "Delve Session")
dlv_console_view = DlvView(dlv_const.CONSOLE_VIEW, "Delve Console")
dlv_bkpt_view = DlvBreakpointView()
dlv_views = [dlv_session_view, dlv_bkpt_view]

def update_view_markers(view):
    dlv_bkpt_view.update_marker(view)

def sync_breakpoints():
    dlv_bkpt_view.sync_breakpoints()

class DlvToggleBreakpoint(sublime_plugin.TextCommand):
    def run(self, edit):
        fn = self.view.file_name()
        if dlv_bkpt_view.is_open() and self.view.id() == dlv_bkpt_view.get_view().id():
            row = self.view.rowcol(self.view.sel()[0].begin())[0]
            if row < len(dlv_bkpt_view.breakpoints):
                dlv_bkpt_view.breakpoints[row]._remove()
                dlv_bkpt_view.breakpoints.pop(row)
                dlv_bkpt_view.update_view()
        elif fn is not None:
            for sel in self.view.sel():
                line, col = self.view.rowcol(sel.a)
                dlv_bkpt_view.toggle_breakpoint(fn, line + 1)
        update_view_markers(self.view)

    def is_enabled(self):
        view = sublime.active_window().active_view()
        return is_gosource(view.file_name()) or dlv_bkpt_view.is_open() and view.id() == dlv_bkpt_view.get_view().id()

    def is_visible(self):
        view = sublime.active_window().active_view()
        return is_gosource(view.file_name()) or dlv_bkpt_view.is_open() and view.id() == dlv_bkpt_view.get_view().id()

class DlvEventListener(sublime_plugin.EventListener):
    def on_query_context(self, view, key, operator, operand, match_all):
        if key == "dlv_running":
            return is_running() == operand
        elif key == "dlv_input_view":
            return dlv_input_view is not None and view.id() == dlv_input_view.id()

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
        if dlv_console_view.is_open() and view.id() == dlv_console_view.get_view().id():
            dlv_console_view.was_closed()

def set_status_message(message):
    sublime.status_message(message)

def dlv_output(pipe, cmd_session=None):
    global dlv_server_process
    global dlv_process
    global dlv_logger

    started_session = False
    # reaesc = re.compile(r'\x1b[^m]*m')
    reaesc = re.compile(r'\x1b\[[\d;]*m')

    if dlv_process is not None and pipe == dlv_process.stdout:
        sublime.set_timeout(sync_breakpoints, 0)
        sublime.set_timeout(show_input, 0)
        dlv_logger.debug("Input field is ready")
        sublime.set_timeout(lambda: set_status_message("Delve session started"), 0)

    while True:
        try:
            line = pipe.readline()
            if len(line) == 0:
                if is_local_mode() and dlv_server_process is not None:
                    if pipe in [dlv_server_process.stdout, dlv_server_process.stderr]:
                        dlv_logger.error("Broken %s pipe of the Delve server" % \
                            ("stdout" if pipe == dlv_server_process.stdout else "stderr"))
                        break
                if dlv_process is not None and dlv_process.stdout is not None:
                    dlv_logger.error("Broken %s pipe of the Delve session" % \
                        ("stdout" if pipe == dlv_process.stdout else "stderr"))
                break
            else:
                line = reaesc.sub('', line)
                line = line.replace("\\n", "\n").replace("\\\"", "\"").replace("\\t", "\t")
#                line = line.replace('\n', '') #alternative of line above
                if line.startswith("(dlv)"):
                    line = line.replace("(dlv)", "")
                line = line.strip()
                if len(line) == 0:
                    continue
            if dlv_process is not None:
                if pipe == dlv_process.stdout:
                    dlv_session_view.add_line(line)
                    dlv_logger.info("Session stdout: " + line)
                elif pipe == dlv_process.stderr:
                    dlv_session_view.add_line(line)
                    dlv_logger.error("Session stderr: " + line)
            if dlv_server_process is not None:
                if pipe == dlv_server_process.stdout:
                    dlv_console_view.add_line(line)
                    dlv_logger.info("Server stdout: " + line)
                    if not started_session:
                        dlv_logger.debug("Delve server is working, try to start Delve Session")
                        lock = threading.RLock()
                        lock.acquire()
                        sublime.set_timeout(lambda: load_session_subprocess(cmd_session), 0)
                        started_session = True
                        lock.release()
                elif pipe == dlv_server_process.stderr:
                    dlv_console_view.add_line(line)
                    dlv_logger.error("Server stderr: " + line)
        except:
            traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
            dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())

    if dlv_process is not None and pipe == dlv_process.stdout:
        message = "Delve session closed"
        sublime.set_timeout(lambda: set_status_message(message), 0)
        dlv_logger.info(message)
        if is_local_mode():
            sublime.set_timeout(cleanup_server, 0)
    if dlv_server_process is not None and pipe == dlv_server_process.stdout:
        dlv_logger.info("Delve server closed")
        sublime.set_timeout(terminate_session, 0)
    if (not is_local_mode() and dlv_process is not None and pipe == dlv_process.stdout) or \
                (is_local_mode() and dlv_server_process is not None and pipe == dlv_server_process.stdout):
        sublime.set_timeout(cleanup_session, 0)

def terminate_session():
    if is_running():
        try:
            dlv_process.terminate()
        except:
            traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
            dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())

def cleanup_session():
    global dlv_logger
    global dlv_views
    
    for view in dlv_views:
        if view.is_open():
            view.close()
    if is_local_mode() and dlv_console_view.is_open():
        dlv_console_view.close()
    dlv_panel_window.set_layout(dlv_panel_layout)
    dlv_panel_window.focus_view(dlv_panel_view)
    dlv_logger.debug("Closed debugging views")
    if dlv_const.is_project_executable():
        dlv_const.clear_project_executable()
        dlv_logger.debug("Cleared project executable settings")
    if dlv_connect._is_open():
        dlv_connect._close()
    dlv_logger.stop()

def cleanup_server():
    global dlv_logger
    global dlv_server_process
    
    if is_server_running():
        try:
            dlv_server_process.terminate()
        except:
            traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
            dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())
            dlv_server_process.kill()
            dlv_logger.error("Delve server killed after timeout")
    if dlv_console_view.is_open():
        dlv_console_view.close()
        dlv_logger.debug("Closed console view")

def load_session_subprocess(cmd_session):
    global dlv_logger
    global dlv_process

    try:
        dlv_connect._open(dlv_const.HOST, dlv_const.PORT)
        message = "Delve session started with command: %s" % " ".join(cmd_session)
        dlv_logger.info(message)
        dlv_session_view.add_line(message)
        dlv_process = subprocess.Popen(cmd_session, shell=False, universal_newlines=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except:
        traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
        message = "Exception thrown, details in file: %s" % dlv_logger.get_file()
        dlv_logger.error(message)
        set_status_message(message)
        if not is_local_mode():
            cleanup_session()
        else:
            cleanup_server()
        return             
    t = threading.Thread(target=dlv_output, args=(dlv_process.stdout,))
    t.start()
    t = threading.Thread(target=dlv_output, args=(dlv_process.stderr,))
    t.start()

class DlvStart(sublime_plugin.WindowCommand):
    def create_cmd(self):
        global dlv_const

        value = "dlv"
        cmd_server = []
        cmd_session = []
        cmd_server.append(value)
        cmd_session.append(value)
        if is_local_mode():
            cmd_server.append(dlv_const.MODE)
        cmd_session.append("connect")
        cmd_server.append("--headless")
        cmd_server.append("--accept-multiclient")
        cmd_server.append("--api-version=2")
        if dlv_const.LOG:
            cmd_server.append("--log")
        value = "%s:%d" % (dlv_const.HOST, dlv_const.PORT)
        cmd_server.append("--listen=%s" % value)
        cmd_session.append(value)
        if dlv_const.ARGS != "":
            cmd_server.append("--")
            cmd_server.append(dlv_const.ARGS)
        return (cmd_session, cmd_server)

    def run(self):
        global dlv_const

        if dlv_const.is_project_executable():
            dlv_const.clear_project_executable()
            dlv_logger.debug("Cleared project executable settings")
        exec_choices = dlv_const.get_project_executables()
        if exec_choices is None:
            self.launch()
            return

        def on_choose(index):
            if index == -1:
                # User cancelled the panel, abort launch
                return
            exec_name = list(exec_choices)[index]
            dlv_const.set_project_executable(exec_name)
            self.launch()

        self.window.show_quick_panel(list(exec_choices), on_choose)

    def launch(self):
        global dlv_const
        global dlv_session_view
        global dlv_server_process
        global dlv_process
        global dlv_logger
        global dlv_panel_window

        dlv_logger.start(dlv_const.DEBUG_FILE)
        if dlv_const.is_project_executable():
            dlv_logger.debug("Set project executable settings: %s" % dlv_const.get_project_executable_name())

        active_view = None
        window = sublime.active_window()
        if window is not None:
            active_view = window.active_view()

        dlv_panel_window = sublime.active_window()
        dlv_panel_layout = dlv_panel_window.get_layout()
        dlv_panel_view = dlv_panel_window.active_view()
        dlv_panel_window.set_layout(dlv_const.PANEL_LAYOUT)

        for v in dlv_views:
            if v.is_closed():
                if v.is_open_at_start():
                    v.open()
            else:
                if v.is_open_at_start():
                    v.clear()
                else:
                    v.close()
        dlv_logger.debug("Debugging views is ready")

        cmd_session, cmd_server = self.create_cmd()        
        if is_local_mode():
            if dlv_console_view.is_closed():
                if dlv_console_view.is_open_at_start():
                    dlv_console_view.open()
            else:
                if dlv_console_view.is_open_at_start():
                    dlv_console_view.clear()
                else:
                    dlv_console_view.close()
            if dlv_console_view.is_open():
                dlv_logger.debug("Console view is ready")
            value = dlv_const.CWD
            cwd = None
            if value != "":
                cwd = value
            else:
                if active_view is not None:
                    file_name = active_view.file_name()
                    if file_name is not None:
                        cwd = os.path.dirname(file_name)
            set_status_message("Starts Delve server, wait...")
            message = "Delve server started with command: %s" % " ".join(cmd_server)
            dlv_logger.info(message)
            dlv_logger.debug("In directory: %s" % cwd)            
            dlv_console_view.add_line(message)
            try:
                dlv_server_process = subprocess.Popen(cmd_server, shell=False, cwd=cwd, universal_newlines=True,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except:
                traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
                message = "Exception thrown, details in file: %s" % dlv_logger.get_file()
                dlv_logger.error(message)
                set_status_message(message)
                cleanup_server()
                cleanup_session()
                return             
            t = threading.Thread(target=dlv_output, args=(dlv_server_process.stdout, cmd_session))
            t.start()
            t = threading.Thread(target=dlv_output, args=(dlv_server_process.stderr,))
            t.start()
        else:
            load_session_subprocess(cmd_session)

    def is_enabled(self):
        return not is_running()

    def is_visible(self):
        return not is_running()

class DlvStop(sublime_plugin.WindowCommand):
    def run(self):
        global dlv_server_process
        global dlv_process
        global dlv_logger

        if is_running():
            try:
                run_cmd('exit')
            except:
                traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
                dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())
                dlv_process.kill()
                dlv_logger.error("Delve session killed after timeout")
                cleanup_session()
                if is_local_mode():
                    cleanup_server()

    def is_enabled(self):
        return is_running()

    def is_visible(self):
        return is_running()

class DlvInput(sublime_plugin.WindowCommand):
    def run(self):
        show_input()

class DlvOpenSessionView(sublime_plugin.WindowCommand):
    def run(self):
        if dlv_session_view.is_closed():
            dlv_session_view.open()

    def is_enabled(self):
        return dlv_session_view.is_closed()

    def is_visible(self):
        return dlv_session_view.is_closed()

class DlvOpenConsoleView(sublime_plugin.WindowCommand):
    def run(self):
        if dlv_console_view.is_closed():
            dlv_console_view.open()

    def is_enabled(self):
        return is_local_mode() and dlv_console_view.is_closed()

    def is_visible(self):
        return is_local_mode() and dlv_console_view.is_closed()

class DlvOpenBreakpointView(sublime_plugin.WindowCommand):
    def run(self):
        if dlv_bkpt_view.is_closed():
            dlv_bkpt_view.open()

    def is_enabled(self):
        return dlv_bkpt_view.is_closed()

    def is_visible(self):
        return dlv_bkpt_view.is_closed()

def breakpoint_object_decoder(obj):
        return Breakpoint(obj['name'], obj['file'], obj['addr'])

class DlvTest(sublime_plugin.WindowCommand):
    def run(self):
        global dlv_const
        global dlv_logger
        global dlv_connect

        breakpointin = DlvBreakpointType("/home/dmitry/Projects/gotest/hello.go", 16)

        try:
            dlv_connect._open(dlv_const.HOST, dlv_const.PORT)
            # print(breakpointin.name)
            result = dlv_connect.RPCServer.CreateBreakpoint(breakpointin._as_parm)
            # result = dlv_connect.RPCServer.CreateBreakpoint({"Breakpoint":{"name":"bp1","file":"/home/dmitry/Projects/gotest/hello.go","line":16}})
            breakpointin._update(result)
            breakpointout = DlvBreakpointType(**result['Breakpoint'])
            print(breakpointout.addr)
        finally:
            dlv_connect._close()

        # # result = conn.add(1, 2)
        # callmethod = {"method":"RPCServer.CreateBreakpoint","params":[{"Breakpoint":{"name":"bp1","file":"/home/dmitry/Projects/gotest/hello.go","line":16}}],"jsonrpc": "2.0","id":3}
        # result = conn.RPCServer.CreateBreakpoint({"Breakpoint":{"name":"bp1","file":"/home/dmitry/Projects/gotest/hello.go","line":16}})
        # print(result)
        # value = 'Testing!'
        # result = conn.echo(value)
        # assert result == value
        # print('Single test completed')

        # callmethod = {"method":"RPCServer.CreateBreakpoint","params":[{"Breakpoint":{"name":"bp1","file":"/home/dmitry/Projects/gotest/hello.go","line":16}}],"jsonrpc": "2.0","id":3}
        # message = json.dumps(callmethod)
        # message_bytes = message.encode('utf-8')
        # sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # sock.settimeout(dlv_const.TIMEOUT)
        # sock.connect(dlv_const.HOST, dlv_const.PORT)
        # sock.send(message_bytes)
        # while True:
        #     try:
        #         data = sock.recv(1024)
        #     except:
        #         traceback.print_exc()
        #         break
        #     if not data or len(data) < 1024: break
        #     dlv_logger.debug(data.strip().decode(sys.getdefaultencoding()))
        # sock.close()
