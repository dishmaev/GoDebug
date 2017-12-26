import sublime
import sublime_plugin
import subprocess
import threading
import traceback
import logging
import queue
import os

dlv_input_view = None
dlv_mode = ""
dlv_server_process = None
dlv_process = None

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

def show_input():
    global dlv_input_view
    dlv_input_view = sublime.active_window().show_input_panel("Delve", "", input_on_done, input_on_change, input_on_cancel)

def input_on_done(s):
    if s.strip() != "quit" and s.strip() != "exit" and s.strip() != "q":
        show_input()
    run_cmd(s)

def input_on_cancel():
    pass

def input_on_change(s):
    pass

def is_running():
    return (dlv_process is not None and dlv_process.poll() is None)

def is_server_running():
    return (dlv_server_process is not None and dlv_server_process.poll() is None)

def is_gosource(s):
    if s is None:
        return False
    ext = os.path.splitext(os.path.basename(s))[1]
    if ext is not None  and ext == ".go":
        return True
    else:
        return False

def is_local_mode():
    return dlv_mode in ["debug", "test"]

def run_cmd(cmd, timeout=10):
    if not is_running():
        message = "Delve session not found, need to start debugging"
        logger.debug(message)
        sublime.status_message(message)
        return

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
#        while True:
        item = get()
        if item is None:
            logging_level_switch["info"]("Stop logging")
            return
#            break
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

    def add_line(self, line, now=True):
        if self.is_open():
            pass

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

# def dlv_session_input(pipe):
#     global dlv_process
#     global logger

def session_ended_status_message():
    sublime.status_message("Delve session closed")

def dlv_output(pipe, cmd_session=None):
    global dlv_server_process
    global dlv_process
    global logger

    started_session = False

    while True:
        try:
            line = pipe.readline()
            if len(line) == 0:
                if is_local_mode() and dlv_server_process is not None:
                    if pipe in [dlv_server_process.stdout, dlv_server_process.stderr]:
                        logger.error("Broken %s pipe of the Delve server" % ("stdout" if pipe == dlv_server_process.stdout else "stderr"))
                        break
                if dlv_process.stdout is not None:
                    logger.error("Broken %s pipe of the Delve session" % ("stdout" if pipe == dlv_process.stdout else "stderr"))
                break
            else:
                if line[len(line)-1] == '\n':
                    line = line[:-1]
            if line.startswith("(dlv)"):
                line = line.replace("(dlv)", "").lstrip().rstrip()
                if len(line) == 0:
                    continue
            if dlv_process is not None:
                if pipe == dlv_process.stdout:
                    logger.info(line)
                elif pipe == dlv_process.stderr:
                    logger.error(line)
            if dlv_server_process is not None:
                if pipe == dlv_server_process.stdout:
                    logger.info(line)
                    if not started_session:
                        logger.debug("Delve server is working, try to start Delve Session")
                        lock = threading.RLock()
                        lock.acquire()
                        sublime.set_timeout(lambda: load_session_subprocess(cmd_session), 0)
                        started_session = True
                        lock.release()
                elif pipe == dlv_server_process.stderr:
                    logger.error(line)
        except:
            traceback.print_exc()
    if dlv_process is not None and pipe == dlv_process.stdout:
        logger.info("Delve session closed")
        sublime.set_timeout(session_ended_status_message, 0)
        if is_local_mode():
            sublime.set_timeout(cleanup_server, 0)
    if (not is_local_mode() and dlv_process is not None and pipe == dlv_process.stdout) or \
                (is_local_mode() and dlv_server_process is not None and pipe == dlv_server_process.stdout):
        if logger.is_started():
            logger.stop()
        sublime.set_timeout(cleanup_session, 0)

def cleanup_session():
    global dlv_views
    
    for view in dlv_views:
        if view.is_open():
            view.close()
    logger.debug("Closed debugging views")

def cleanup_server():
    global dlv_server_process
    global logger
    
    if is_server_running():
        try:
            dlv_server_process.terminate()
            logger.info("Delve server closed")
        except:
            traceback.print_exc()
            dlv_server_process.kill()
            logger.info("Delve server killed after timeout")

def load_session_subprocess(cmd_session):
    global dlv_process

    logger.info("Delve session started with command: %s" % " ".join(cmd_session))
    dlv_process = subprocess.Popen(" ".join(cmd_session), shell=True, universal_newlines=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    t = threading.Thread(target=dlv_output, args=(dlv_process.stdout,))
    t.start()
    t = threading.Thread(target=dlv_output, args=(dlv_process.stderr,))
    t.start()
    # t = threading.Thread(target=dlv_session_input, args=(dlv_process.stdin,))
    # t.start()

class DlvStart(sublime_plugin.WindowCommand):
    def run(self):
        global dlv_session_view
        global dlv_mode
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
        dlv_mode = get_setting("mode", "debug")
        if is_local_mode():
            cmd_server.append(dlv_mode)
        cmd_session.append("connect")
        cmd_server.append("--headless")
        # cmd_server.append("--accept-multiclient")
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
        if is_local_mode():
            value = get_setting("cwd", "")
            if value != "":
                cwd = value
            else:
                window = sublime.active_window()
                if window is not None:
                    view = window.active_view()
                    if view is not None:
                        cwd = os.path.dirname(view.file_name())
            logger.info("Delve server started with command: %s" % " ".join(cmd_server))
            logger.debug("In directory: %s" % cwd)            
            dlv_server_process = subprocess.Popen(cmd_server, shell=False, cwd=cwd, universal_newlines=True,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            t = threading.Thread(target=dlv_output, args=(dlv_server_process.stdout, cmd_session))
            t.start()
            t = threading.Thread(target=dlv_output, args=(dlv_server_process.stderr,))
            t.start()
        else:
            load_session_subprocess(cmd_session)
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

        if is_running():
            try:
                run_cmd('exit')
                logger.debug("Normal exit")
            except:
                traceback.print_exc()
                dlv_process.kill()
                logger.error("Kill after timeout")
                cleanup_session()

        # if is_local_mode():
        #     cleanup_server()

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
