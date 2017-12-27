import sublime
import sublime_plugin
import subprocess
import threading
import traceback
import logging
import queue
import os
import json
import socket 
import sys 
import re

dlv_panel_layout = {}
dlv_panel_window = None
dlv_panel_view = None

dlv_input_view = None
dlv_command_history = []
dlv_command_history_pos = 0

dlv_mode = None
dlv_server_process = None
dlv_process = None

def normalize(filename):
    if filename is None:
        return None
    return os.path.abspath(os.path.normcase(filename))

def get_setting(key, default=None, view=None):
    if view is None:
        window = sublime.active_window()
        if window is not None:
            view = window.active_view()
    if view is not None:
        settings = view.settings()
        if settings.has("sublimedelve_%s" % key):
            return settings.get("sublimedelve_%s" % key)
    return sublime.load_settings("SublimeDelve.sublime-settings").get(key, default)

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
    global dlv_mode
    if dlv_mode is None:
        try:
            dlv_mode = get_setting("mode", "debug")
        except:
            traceback.print_exc()
    return dlv_mode in ["debug", "test"]

def run_cmd(cmd, timeout=10):
    if not is_running():
        message = "Delve session not found, need to start debugging"
        logger.debug(message)
        sublime.status_message(message)
        return

    if isinstance(cmd, list):
        for c in cmd:
            run_cmd(c, timeout)
        return
    message = "Run command: %s" % cmd
    dlv_session_view.add_line(message)
    logger.info(message)
    dlv_process.stdin.write(cmd + "\n")
    dlv_process.stdin.flush()
    return

class Logger(object):
    def __init__(self):
        self.log_queue = queue.Queue()
        self.enabled = False
        self.started = False
        self.initialized = False
        self.lock = threading.RLock()
        self.log = logging.getLogger('SublimeDelve')
        self.logging_level_switch = {
                'debug':    self.log.debug,
                'info':     self.log.info,
                'warning':  self.log.warning,
                'error':    self.log.error,
                'critical': self.log.critical }
    def is_started(self):
        return self.started

    def start(self):
        if not self.initialized:
            self.enabled = get_setting("debug", True)
            if self.enabled:
                logging_level = (logging.INFO if get_setting("debug_level", "debug") == "info" else logging.DEBUG)
                self.log.setLevel(logging_level)
                file = get_setting("debug_file", "stdout")
                if (file != "stdout"):
                    fh = logging.FileHandler(file);
                    fh.setLevel(logging_level)
                    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                    fh.setFormatter(formatter)
                    self.log.addHandler(fh)
            self.initialized = True
        if not self.is_started():
            self.started = True
            self.logging_level_switch["info"]("Start logging")
        else:
            self.logging_level_switch["debug"]("Logging already started!")

    def write_log(self, get, logging_level_switch):
        item = get()
        if item is None:
            logging_level_switch["info"]("Stop logging")
            return
        logging_level_switch[item["level"]](item["message"])

    def stop(self):
        if self.enabled and self.started:
            self.lock.acquire()
            self.log_queue.put(None)
            self.started = False
            self.lock.release()
            self.write_log(self.log_queue.get, self.logging_level_switch)

    def do_log(self, level, message):
        if self.enabled:
            self.log_queue.put({"level":"%s" % level, "message":"%s" % message})
            self.write_log(self.log_queue.get, self.logging_level_switch)

    def debug(self, message):
        if self.enabled:
            self.do_log("debug", message)

    def info(self, message):
        if self.enabled:
            self.do_log("info", message)

    def warning(self, message):
        if self.enabled:
            self.do_log("warning", message)

    def error(self, message):
        if self.enabled:
            self.do_log("error", message)

    def critical(self, message):
        if self.enabled:
            self.do_log("critical", message)

logger = Logger()

class DlvView(object):
    def __init__(self, name, scroll=True, group=None):
        self.group = group
        self.name = name
        self.scroll = scroll
        self.view = None
        self.counter = 0

    def open(self):
        if self.view is None or self.view.window() is None:
            if self.group is not None:
                sublime.active_window().focus_group(get_setting("%s_group" % self.group, 0))
            self.create_view()

    def close(self):
        if self.view is not None:
            if self.group is not None:
                sublime.active_window().focus_group(get_setting("%s_group" % self.group, 0))
            self.destroy_view()

    def clear(self):
        if self.view is not None:
            self.view.run_command("dlv_view_clear")
            self.counter = 0

    def create_view(self):
        self.view = sublime.active_window().new_file()
        self.view.set_name(self.name)
        self.view.set_scratch(True)
        self.view.set_read_only(True)
        self.view.settings().set('command_mode', False)

    def open_at_start(self):
        if self.group is not None:
            return get_setting("%s_open" % self.group, False)
        return False

    def is_open(self):
        return self.view is not None

    def is_closed(self):
        return self.view is None

    def get_view(self):
        return self.view

    def was_closed(self):
        self.view = None

    def destroy_view(self):
        sublime.active_window().focus_view(self.view)
        sublime.active_window().run_command("close")
        self.view = None
        self.counter = 0

    def add_line(self, line):
        if self.view is not None:
            self.counter += 1
            full_line = str(self.counter) + " - " + line + "\n"
            self.view.run_command("dlv_view_add_line", {"line": full_line, "scroll": self.scroll })

class DlvViewClear(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.set_read_only(False)
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.set_read_only(True)

class DlvViewAddLine(sublime_plugin.TextCommand):
    def run(self, edit, line, scroll):
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), line)
        self.view.set_read_only(True)
        if scroll:
            self.view.show(self.view.size())

class DlvBreakpoint(object):
    def __init__(self, filename, line, name):
        self.original_filename = normalize(filename)
        self.original_line = line
        self.name = name
        self.add()

    @property
    def line(self):
        return self.original_line

    @property
    def filename(self):
        return normalize(self.original_filename)

    def add(self):
        if is_running():
            logger.debug("TO-DO send json-rpc for add breakpoint")

    def remove(self):
        if is_running():
            logger.debug("TO-DO send json-rpc for remove breakpoint")

    def format(self):
        return "%s %s:%d" % (self.name, self.filename, self.line)

class DlvBreakpointView(DlvView):
    def __init__(self):
        super(DlvBreakpointView, self).__init__("Delve Breakpoints", scroll=False, group="breakpoints")
        self.breakpoints = []
        self.number = 0

    def open(self):
        super(DlvBreakpointView, self).open()
        if self.is_open():
            self.update_view()

    def update_marker(self, view):
        bps = []
        fn = view.file_name()
        if fn is None:
            return
        fn = normalize(fn)
        for bkpt in self.breakpoints:
            if bkpt.filename == fn:
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
        self.breakpoints.sort(key=lambda b: (b.filename, b.line))
        for bkpt in self.breakpoints:
            self.add_line(bkpt.format())

    def find_breakpoint(self, filename, line):
        filename = normalize(filename)
        for bkpt in self.breakpoints:
            if bkpt.filename == filename and bkpt.line == line:
                return bkpt
        return None

    def toggle_breakpoint(self, filename, line):
        bkpt = self.find_breakpoint(filename, line)
        if bkpt:
            bkpt.remove()
            self.breakpoints.remove(bkpt)
        else:
            self.number += 1
            self.breakpoints.append(DlvBreakpoint(filename, line, "b%d" % self.number))
        self.update_view()

    def sync_breakpoints(self):
        for bkpt in self.breakpoints:
            bkpt.add()

dlv_session_view = DlvView("Delve Session", group="session")
dlv_console_view = DlvView("Delve Console", group="console")
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
                dlv_bkpt_view.breakpoints[row].remove()
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

def session_started_status_message():
    sublime.status_message("Delve session started")

def session_ended_status_message():
    sublime.status_message("Delve session closed")

def dlv_output(pipe, cmd_session=None):
    global dlv_server_process
    global dlv_process
    global logger

    started_session = False
    # reaesc = re.compile(r'\x1b[^m]*m')
    reaesc = re.compile(r'\x1b\[[\d;]*m')

    if dlv_process is not None and pipe == dlv_process.stdout:
        sublime.set_timeout(session_started_status_message, 0)
        sublime.set_timeout(sync_breakpoints, 0)

    while True:
        try:
            line = pipe.readline()
            if len(line) == 0:
                if is_local_mode() and dlv_server_process is not None:
                    if pipe in [dlv_server_process.stdout, dlv_server_process.stderr]:
                        logger.error("Broken %s pipe of the Delve server" % \
                            ("stdout" if pipe == dlv_server_process.stdout else "stderr"))
                        break
                if dlv_process.stdout is not None:
                    logger.error("Broken %s pipe of the Delve session" % \
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
                    logger.info("Session stdout: " + line)
                elif pipe == dlv_process.stderr:
                    dlv_session_view.add_line(line)
                    logger.error("Session stderr: " + line)
            if dlv_server_process is not None:
                if pipe == dlv_server_process.stdout:
                    dlv_console_view.add_line(line)
                    logger.info("Server stdout: " + line)
                    if not started_session:
                        logger.debug("Delve server is working, try to start Delve Session")
                        lock = threading.RLock()
                        lock.acquire()
                        sublime.set_timeout(lambda: load_session_subprocess(cmd_session), 0)
                        started_session = True
                        lock.release()
                elif pipe == dlv_server_process.stderr:
                    dlv_console_view.add_line(line)
                    logger.error("Server stderr: " + line)
        except:
            traceback.print_exc()
    if dlv_process is not None and pipe == dlv_process.stdout:
        logger.info("Delve session closed")
        sublime.set_timeout(session_ended_status_message, 0)
        if is_local_mode():
            sublime.set_timeout(cleanup_server, 0)
    if dlv_server_process is not None and pipe == dlv_server_process.stdout:
        logger.info("Delve server closed")
    if (not is_local_mode() and dlv_process is not None and pipe == dlv_process.stdout) or \
                (is_local_mode() and dlv_server_process is not None and pipe == dlv_server_process.stdout):
        if logger.is_started():
            sublime.set_timeout(lambda: logger.stop(), 0)
        sublime.set_timeout(cleanup_session, 0)

def cleanup_session():
    global dlv_views
    
    for view in dlv_views:
        if view.is_open():
            view.close()
    if is_local_mode() and dlv_console_view.is_open():
        dlv_console_view.close()
    dlv_panel_window.set_layout(dlv_panel_layout)
    dlv_panel_window.focus_view(dlv_panel_view)
    logger.debug("Closed debugging views")

def cleanup_server():
    global dlv_server_process
    global logger
    
    if is_server_running():
        try:
            dlv_server_process.terminate()
        except:
            traceback.print_exc()
            dlv_server_process.kill()
            logger.error("Delve server killed after timeout")
    if dlv_console_view.is_open():
        dlv_console_view.close()

def load_session_subprocess(cmd_session):
    global dlv_process

    message = "Delve session started with command: %s" % " ".join(cmd_session)
    logger.info(message)
    dlv_session_view.add_line(message)
    dlv_process = subprocess.Popen(" ".join(cmd_session), shell=True, universal_newlines=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    t = threading.Thread(target=dlv_output, args=(dlv_process.stdout,))
    t.start()
    t = threading.Thread(target=dlv_output, args=(dlv_process.stderr,))
    t.start()

class DlvStart(sublime_plugin.WindowCommand):
    def create_cmd(self):
        global dlv_mode
        value = "dlv"
        cmd_server = []
        cmd_session = []
        cmd_server.append(value)
        cmd_session.append(value)
        if is_local_mode():
            cmd_server.append(dlv_mode)
        cmd_session.append("connect")
        cmd_server.append("--headless")
        cmd_server.append("--accept-multiclient")
        cmd_server.append("--api-version=2")
        if get_setting("log", "false") == "true":
            cmd_server.append("--log")
        value = get_setting("host", "localhost") + ":" + get_setting("port", "3456")
        cmd_server.append("--listen=%s" % value)
        cmd_session.append(value)
        value = get_setting("args", "")
        if value != "":
            cmd_server.append("--")
            cmd_server.append(value)
        return (cmd_session, cmd_server)

    def run(self):
        global dlv_session_view
        global dlv_server_process
        global dlv_process
        global logger
        global dlv_panel_window

        logger.start()
        cmd_session, cmd_server = self.create_cmd()        
        window = sublime.active_window()
        if window is not None:
            active_view = window.active_view()

        dlv_panel_window = sublime.active_window()
        dlv_panel_layout = dlv_panel_window.get_layout()
        dlv_panel_view = dlv_panel_window.active_view()
        dlv_panel_window.set_layout(
            {
                "cols": [0.0, 0.33, 0.66, 1.0],
                "rows": [0.0, 0.75, 1.0],
                "cells":
                [
                    [0, 0, 3, 1],
                    [0, 1, 1, 2],
                    [1, 1, 2, 2],
                    [2, 1, 3, 2] 
                ]
            }
        )
        for v in dlv_views:
            if v.is_closed():
                if v.open_at_start():
                    v.open()
            else:
                v.clear()
        if is_local_mode():
            if dlv_console_view.is_closed():
                if dlv_console_view.open_at_start():
                    dlv_console_view.open()
            else:
                dlv_console_view.clear()

        if is_local_mode():
            if dlv_console_view.is_closed():
                dlv_console_view.open()
            value = get_setting("cwd", "")
            cwd = None
            if value != "":
                cwd = value
            else:
                if active_view is not None:
                    file_name = active_view.file_name()
                    if file_name is not None:
                        cwd = os.path.dirname(file_name)
            message = "Delve server started with command: %s" % " ".join(cmd_server)
            logger.info(message)
            logger.debug("In directory: %s" % cwd)            
            dlv_console_view.add_line(message)
            dlv_server_process = subprocess.Popen(cmd_server, shell=False, cwd=cwd, universal_newlines=True,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            t = threading.Thread(target=dlv_output, args=(dlv_server_process.stdout, cmd_session))
            t.start()
            t = threading.Thread(target=dlv_output, args=(dlv_server_process.stderr,))
            t.start()
        else:
            load_session_subprocess(cmd_session)
        show_input()

    def is_enabled(self):
        return not is_running()

    def is_visible(self):
        return not is_running()

class DlvStop(sublime_plugin.WindowCommand):
    def run(self):
        global dlv_server_process
        global dlv_process
        global logger

        if is_running():
            try:
                run_cmd('exit')
                # if is_local_mode():
                #     cleanup_server()
            except:
                traceback.print_exc()
                dlv_process.kill()
                logger.error("Delve session killed after timeout")
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


class DlvTest(sublime_plugin.WindowCommand):
    def run(self):
        callmethod = {"method":"RPCServer.CreateBreakpoint","params":[{"Breakpoint":{"name":"bp1","file":"/home/dmitry/Projects/gotest/hello.go","line":16}}],"jsonrpc": "2.0","id":3}
        message = json.dumps(callmethod)
        message_bytes = message.encode('utf-8')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(get_setting("timeout", "10"))
        sock.connect(('localhost', 3456))
        sock.send(message_bytes)
        while True:
            data = sock.recv(1024)
            if not data or len(data) < 1024: break
            logger.debug(data.strip().decode(sys.getdefaultencoding()))
        sock.close()
