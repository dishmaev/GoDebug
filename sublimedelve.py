import sublime
import sublime_plugin
import subprocess
import threading
import traceback
import logging
import queue
import time
import sys

dlv_input_view = None
dlv_server_process = None
dlv_process = None

def get_setting(key, default=None, view=None):
    if view is None:
        window = sublime.active_window()
        if window is not None:
            view = sublime.active_window().active_view()
    if view is not None:
        settings = view.settings()
        if settings.has("sublimedelve_%s" % key):
            return settings.get("sublimedelve_%s" % key)
    return sublime.load_settings("SublimeDelve.sublime-settings").get(key, default)

def show_input():
    global dlv_input_view
    dlv_input_view = sublime.active_window().show_input_panel("Delve", "", input_on_done, input_on_change, input_on_cancel)

def input_on_done(s):
    run_cmd(s)

def input_on_cancel():
    pass

def input_on_change(s):
    pass

def is_running():
    return (dlv_process is not None and dlv_process.poll() is None)

def run_cmd(cmd, timeout=10):
    if isinstance(cmd, list):
        for c in cmd:
            run_cmd(c, block, mimode, timeout)
        return
    logger.info("Run command: %s" % cmd)
    dlv_process.stdin.write(cmd + "\n")
    dlv_process.stdin.flush()
    return

class Logger(object):
    def __init__(self):
        self.log_queue = queue.Queue()
        self.enabled = False
        self.started = False
        self.initialized = False
        self.eventFinished = threading.Event()
        self.eventNeedStop = threading.Event()
        self.log = logging.getLogger('SublimeDelve')
        self.log.setLevel(logging.DEBUG)
        self.logging_level_switch = {
                'debug':    self.log.debug,
                'info':     self.log.info,
                'warning':  self.log.warning,
                'error':    self.log.error,
                'critical': self.log.critical }
    def is_started():
        return self.started

    def start(self):
        if not self.initialized:
            self.enabled = get_setting("debug", True)
            file = get_setting("debug_file", "stdout")
            if self.enabled:
                if (file != "stdout"):
                    fh = logging.FileHandler(file);
                    fh.setLevel(logging.DEBUG)
                    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                    fh.setFormatter(formatter)
                    self.log.addHandler(fh)
                self.started = True
                self.logging_level_switch["info"]("Start logging")
            self.initialized = True

    def write_log(self, get, logging_level_switch):
#        while True:
        item = get()
        if item is None:
            logging_level_switch["info"]("Stop logging")
            return
#            break
        logging_level_switch[item["level"]](item["message"])

    def stop(self):
        if self.enabled and self.started:
            lock = threading.Lock()
            lock.acquire()
            self.log_queue.put(None)
            self.started = False
            lock.release()
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
    def __init__(self, name):
        self.name = name
        self.closed = True
        self.view = None

    def open(self):
        if self.view is None or self.view.window() is None:
            self.create_view()

    def close(self):
        if self.view is not None:
            self.destroy_view()

    def clear(self):
        if self.view is not None:
            self.view.run_command("dlv_view_clear")  

    def create_view(self):
        self.view = sublime.active_window().new_file()
        self.view.set_name(self.name)
        self.view.set_read_only(True)
        self.closed = False

    def is_open(self):
        return not self.closed

    def get_view(self):
        return self.view

    def was_closed(self):
        self.view = None
        self.closed = True

    def destroy_view(self):
        sublime.active_window().focus_view(self.view)
        sublime.active_window().run_command("close")
        self.view = None
        self.closed = True

class DlvSessionView(DlvView):
    def __init__(self):
        super(DlvSessionView, self).__init__("Delve Session")

dlv_session_view = DlvSessionView()
dlv_views = [dlv_session_view]

class DlvViewClear(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.set_read_only(False)
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.set_read_only(True)

class DlvEventListener(sublime_plugin.EventListener):
    def on_query_context(self, view, key, operator, operand, match_all):
        if key == "dlv_running":
            return is_running() == operand
        elif key == "dlv_input_view":
            return dlv_input_view is not None and view.id() == dlv_input_view.id()

    def on_close(self, view):
        for v in dlv_views:
            if v.is_open() and view.id() == v.get_view().id():
                v.was_closed()
                break

def dlv_session_input(pipe):
    global dlv_process
    global logger

def session_ended_status_message():
    sublime.status_message("Delve session closed")

def dlv_session_output(pipe):
    global dlv_process
    global logger
    while True:
        try:
            line = pipe.readline()
            if len(line) == 0:
                logger.error("Broken %s pipe of the Delve session" % ("stdout" if pipe == dlv_process.stdout else "stderr"))
                break
            else:
                if line[len(line)-1] == '\n':
                    line = line[:-1]
            if line.startswith("(dlv)"):
                line = line.replace("(dlv) ", "").lstrip().rstrip()
                if len(line) == 0:
                    continue
            if pipe == dlv_process.stdout:
                logger.info(line)
            else:
                logger.error(line)
        except:
            traceback.print_exc()
    if pipe == dlv_process.stdout:
        logger.info("Delve session closed")
        sublime.set_timeout(session_ended_status_message, 0)
    sublime.set_timeout(cleanup, 0)

def cleanup():
    global dlv_views
    global dlv_session_view
    global logger
    
    for view in dlv_views:
        if view.is_open():
            view.close()
    logger.stop()

class DlvStart(sublime_plugin.WindowCommand):
    def run(self):
        global dlv_session_view
        global dlv_server_process
        global dlv_process
        global logger

        dlv_session_view.clear()

        logger.start()

        cwd = None
        value = "dlv"
        cmd_server = []
        cmd_session = []
        cmd_server.append(value)
        cmd_session.append(value)
        mode = get_setting("mode", "debug")
        if mode == "debug" or mode == "test":
            cmd_server.append(mode)
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
        value = get_setting("cwd", "")
        if value != "":
            cwd = value
        logger.info("Delve %s session started with command: %s" % (mode, " ".join(cmd_session)))
        dlv_process = subprocess.Popen(" ".join(cmd_session), shell=True, cwd=cwd, universal_newlines=True,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t = threading.Thread(target=dlv_session_output, args=(dlv_process.stdout,))
        t.start()
        t = threading.Thread(target=dlv_session_output, args=(dlv_process.stderr,))
        t.start()
        t = threading.Thread(target=dlv_session_input, args=(dlv_process.stdin,))
        t.start()
        show_input()
        sublime.status_message("Delve session started")

    def is_enabled(self):
        return not is_running()

    def is_visible(self):
        return not is_running()

class DlvStop(sublime_plugin.WindowCommand):
    def run(self):
        global dlv_server_process
        global dlv_process
        global logger

        if dlv_process is not None and dlv_process.poll() is None:
            try:
                run_cmd('exit')
                logger.debug("Normal exit")
            except:
                traceback.print_exc()
                dlv_process.kill()
                logger.error("Kill after timeout")
        if dlv_server_process is not None and dlv_server_process.poll() is None:
            try:
                dlv_server_process.terminate()
                logger.debug("Delve server normal exit")
            except:
                traceback.print_exc()
                dlv_server_process.kill()
                logger.error("Delve server kill after timeout")

        cleanup()

    def is_enabled(self):
        return is_running()

    def is_visible(self):
        return is_running()

class DlvInput(sublime_plugin.WindowCommand):
    def run(self):
        show_input()

class DlvOpenSessionView(sublime_plugin.WindowCommand):
    def run(self):
        if not dlv_session_view.is_open():
            dlv_session_view.open()

    def is_enabled(self):
        return not dlv_session_view.is_open()

    def is_visible(self):
        return not dlv_session_view.is_open()
