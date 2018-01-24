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
import signal
import uuid

from SublimeDelve.sdconst import DlvConst
from SublimeDelve.sdlogger import DlvLogger
from SublimeDelve.sdworker import DlvWorker

from SublimeDelve.sdview import DlvView
from SublimeDelve.sdobjecttype import *

dlv_project = {}

class DlvProject(object):
    def __init__(self, window):
        self.window = window
        self.const = DlvConst(self.window)
        self.logger = DlvLogger(self.window, self.const)

        self.cursor = ''
        self.cursor_position = 0
        self.last_cursor_view = None

        self.panel_layout = {}
        self.panel_window = None
        self.panel_view = None

        self.input_view = None
        self.command_history = []
        self.command_history_pos = 0

        self.next_in_progress = False

        self.__session_proc = None
        self.__session_send_signal = False
        self.__server_proc = None

        self.session_view = self.__initialize_view(self.const.SESSION_VIEW)
        self.console_view = self.__initialize_view(self.const.CONSOLE_VIEW)
        self.stacktrace_view = self.__initialize_view(self.const.STACKTRACE_VIEW)
        self.goroutine_view = self.__initialize_view(self.const.GOROUTINE_VIEW)
        self.variable_view = self.__initialize_view(self.const.VARIABLE_VIEW)
        self.watch_view = self.__initialize_view(self.const.WATCH_VIEW)
        self.bkpt_view = self.__initialize_view(self.const.BREAKPOINT_VIEW)

        self.worker = DlvWorker(self, worker_callback)

    def get_views(self):
        return [self.session_view, self.variable_view, self.watch_view, self.stacktrace_view, self.bkpt_view, self.goroutine_view]

    def get_new_view(self, name, view):
        if name == self.const.SESSION_VIEW:
            return DlvSessionView(self, view)
        elif name == self.const.CONSOLE_VIEW:
            return DlvConsoleView(self, view)
        elif name == self.const.STACKTRACE_VIEW:
            return DlvStacktraceView(self, view)
        elif name == self.const.GOROUTINE_VIEW:
            return DlvGoroutineView(self, view)
        elif name == self.const.VARIABLE_VIEW:
            return DlvVariableView(name, self, view)
        elif name == self.const.WATCH_VIEW:
            return DlvVariableView(name, self, view)
        elif name == self.const.BREAKPOINT_VIEW:
            return DlvBreakpointView(self, view)
        return None

    def __initialize_view(self, name):
        view = None
        for v in self.window.views():
            if v.name() == self.const.get_view_setting(name, self.const.TITLE):
                view = v
        return self.get_new_view(name, view)

    def reset_cursor(self):
        self.cursor = ''
        self.cursor_position = 0
        self.next_in_progress = False

    def panel_on_start(self):
        self.panel_window = self.window
        self.panel_layout = self.panel_window.get_layout()
        self.panel_view = self.panel_window.active_view()
        self.panel_window.set_layout(self.const.PANEL_LAYOUT)

    def panel_on_stop(self):
        self.panel_window.set_layout(self.panel_layout)
        self.panel_window.focus_view(self.panel_view)

    def check_input_view(self, view):
        return self.input_view is not None and view.id() == self.input_view.id()

    def set_input(self, edit, text):
        self.input_view.erase(edit, sublime.Region(0, self.input_view.size()))
        self.input_view.insert(edit, 0, text)

    def show_input(self):
        self.command_history_pos = len(self.command_history)
        self.input_view = self.window.show_input_panel("Delve command", "", self.input_on_done, self.input_on_change, self.input_on_cancel)

    def input_on_done(self, s):
        if not self.is_running():
            message = "Delve session not found, need to start debugging"
            self.logger.debug(message)
            set_status_message(message)
            return

        if s.strip() != "quit" and s.strip() != "exit" and s.strip() != "q":
            self.command_history.append(s)
            self.show_input()
        
        self.run_input_cmd(s)

    def input_on_cancel(self):
        pass

    def input_on_change(self, s):
        pass

    def run_input_cmd(self, cmd):
        if isinstance(cmd, list):
            for c in cmd:
                self.run_input_cmd(c)
            return
        elif cmd.strip() == "":
            return
        message = "Input command: %s" % cmd
        self.session_view.add_line(message)
        self.logger.info(message)
        try:
            self.__session_proc.stdin.write(cmd + '\n')
            self.__session_proc.stdin.flush()
        except:
            traceback.print_exc(file=(sys.stdout if self.logger.get_file() == self.const.STDOUT else open(self.logger.get_file(),"a")))
            self.logger.error("Exception thrown, details in file: %s" % self.logger.get_file())
        requests = []
        requests.append({"cmd": self.const.STATE_COMMAND, "parms": None})
        self.add_breakpoint_request(requests)
        self.add_goroutine_request(requests)
        self.worker.do_batch(requests)

    def is_running(self):
        return self.__session_proc is not None and self.__session_proc.poll() is None

    def is_server_running(self):
        return self.__server_proc is not None and self.__server_proc.poll() is None

    def terminate_session(self, send_sigint=False):
        if self.is_running():
            try:
                if send_sigint:
                    self.logger.debug('Send to session subprocess SIGINT signal')
                    self.__session_proc.send_signal(signal.SIGINT)
                else:
                    self.logger.debug('Send to session subprocess SIGTERM signal')
                    self.__session_proc.send_signal(signal.SIGTERM)
            except:
                traceback.print_exc(file=(sys.stdout if self.logger.get_file() == self.const.STDOUT else open(self.logger.get_file(),"a")))
                self.logger.error("Exception thrown (terminate_session), details in file: %s" % self.logger.get_file())
        if self.is_server_running():
            try:
                if send_sigint:
                    self.logger.debug('Send to server subprocess SIGINT signal')
                    self.__server_proc.send_signal(signal.SIGINT)
                else:
                    self.logger.debug('Send to server subprocess SIGTERM signal')
                    self.__server_proc.send_signal(signal.SIGTERM)
            except:
                traceback.print_exc(file=(sys.stdout if self.logger.get_file() == self.const.STDOUT else open(self.logger.get_file(),"a")))
                self.logger.error("Exception thrown (terminate_session), details in file: %s" % self.logger.get_file())

    def terminate_server(self):
        if self.is_server_running():
            try:
                self.logger.debug('Send to server subprocess SIGINT signal')
                self.__server_proc.send_signal(signal.SIGINT)
            except:
                traceback.print_exc(file=(sys.stdout if self.logger.get_file() == self.const.STDOUT else open(self.logger.get_file(),"a")))
                self.logger.error("Exception thrown (terminate_server), details in file: %s" % self.logger.get_file())
                self.logger.debug('Send to server subprocess SIGKILL signal')
                self.__server_proc.kill()
                self.logger.error("Delve server killed after timeout")
        v = self.console_view
        if v.is_open():
            if v.is_close_at_stop():
                v.close()
                self.logger.debug("Closed console view")
            else:
                v.clear(True)

        if self.console_view.is_open():
            self.console_view.close()

    def cleanup_session(self):
        v = self.console_view
        if v.is_open():
            if v.is_close_at_stop():
                v.close()
            else:
                v.clear(True)
        for v in self.get_views():
            if v.is_open():
                if v.is_close_at_stop():
                    v.close()
                else:
                    v.clear(True)
        self.panel_on_stop()
        self.logger.debug("Closed required debugging views")
        if self.const.is_project_executable():
            self.const.clear_project_executable()
            self.logger.debug("Cleared project executable settings")
        self.worker.stop()
        self.logger.stop()
        self.clear_position()
        self.reset_cursor()

    def __open_subprocess(self, cmd, cwd=None):
        return subprocess.Popen(cmd, shell=False, cwd=cwd, universal_newlines=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def load_session_subprocess(self, cmd_session):
        message = "Delve session started with command: %s" % " ".join(cmd_session)
        self.logger.info(message)
        self.session_view.add_line(message)
        try:
            self.__session_proc = self.__open_subprocess(cmd_session)
        except:
            traceback.print_exc(file=(sys.stdout if self.logger.get_file() == self.const.STDOUT else open(self.logger.get_file(),"a")))
            self.logger.error("Exception thrown, details in file: %s" % self.logger.get_file())
            self.logger.error(message)
            set_status_message(message)
            if self.is_local_mode():
                self.terminate_server()
            else:
                self.cleanup_session()
            return             
        self.reset_cursor()
        t = threading.Thread(target=self.dlv_output, args=(self.__session_proc.stdout,))
        t.start()
        t = threading.Thread(target=self.dlv_output, args=(self.__session_proc.stderr,))
        t.start()

    def load_server_subprocess(self, cmd_server, cmd_session, cwd):
        set_status_message("Starts Delve server, wait...")
        message = "Delve server started with command: %s" % " ".join(cmd_server)
        self.logger.info(message)
        self.logger.debug("In directory: %s" % cwd)            
        self.console_view.add_line(message)
        try:
            self.__server_proc = self.__open_subprocess(cmd_server, cwd)
        except:
            traceback.print_exc(file=(sys.stdout if self.logger.get_file() == self.const.STDOUT else open(self.logger.get_file(),"a")))
            message = "Exception thrown, details in file: %s" % self.logger.get_file()
            self.logger.error(message)
            set_status_message(message)
            self.terminate_server()
            self.cleanup_session()
            return             
        t = threading.Thread(target=self.dlv_output, args=(self.__server_proc.stdout, cmd_session))
        t.start()
        t = threading.Thread(target=self.dlv_output, args=(self.__server_proc.stderr,))
        t.start()

    def dlv_output(self, pipe, cmd_session=None):
        started_session = False
        # reaesc = re.compile(r'\x1b[^m]*m')
        reaesc = re.compile(r'\x1b\[[\d;]*m')

        if self.__session_proc is not None and pipe == self.__session_proc.stdout:
            sublime.set_timeout(self.show_input, 0)
            self.logger.debug("Input field is ready")
            sublime.set_timeout(self.bkpt_view.sync_breakpoints, 0)
            sublime.set_timeout(set_status_message("Delve session started"), 0)

        while True:
            try:
                line = pipe.readline()
                if len(line) == 0:
                    if self.is_local_mode() and self.__server_proc is not None:
                        if pipe in [self.__server_proc.stdout, self.__server_proc.stderr]:
                            self.logger.error("Broken %s pipe of the Delve server" % \
                                ("stdout" if pipe == self.__server_proc.stdout else "stderr"))
                            break
                    if self.__session_proc is not None and self.__session_proc.stdout is not None:
                        self.logger.error("Broken %s pipe of the Delve session" % \
                            ("stdout" if pipe == self.__session_proc.stdout else "stderr"))
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
                if self.__session_proc is not None:
                    if pipe == self.__session_proc.stdout:
                        self.session_view.add_line(line)
                        self.logger.info("Session stdout: " + line)
                    elif pipe == self.__session_proc.stderr:
                        self.session_view.add_line(line)
                        self.logger.error("Session stderr: " + line)
                if self.__server_proc is not None:
                    if pipe == self.__server_proc.stdout:
                        self.console_view.add_line(line)
                        self.logger.info("Server stdout: " + line)
                        if not started_session:
                            self.logger.debug("Delve server is working, try to start Delve Session")
                            lock = threading.RLock()
                            lock.acquire()
                            sublime.set_timeout(self.load_session_subprocess(cmd_session), 0)
                            started_session = True
                            lock.release()
                    elif pipe == self.__server_proc.stderr:
                        self.console_view.add_line(line)
                        self.logger.error("Server stderr: " + line)
            except:
                traceback.print_exc(file=(sys.stdout if self.logger.get_file() == self.const.STDOUT else open(self.logger.get_file(),"a")))
                self.logger.error("Exception thrown, details in file: %s" % self.logger.get_file())

        if self.__session_proc is not None and pipe == self.__session_proc.stdout:
            message = "Delve session closed"
            sublime.set_timeout(set_status_message(message), 0)
            self.logger.info(message)
            # sublime.set_timeout(self.terminate_server, 0)
        if self.__server_proc is not None and pipe == self.__server_proc.stdout:
            self.logger.info("Delve server closed")
            sublime.set_timeout(self.terminate_session, 0)
        if (not self.is_local_mode() and self.__session_proc is not None and pipe == self.__session_proc.stdout) or \
                    (self.is_local_mode() and self.__server_proc is not None and pipe == self.__server_proc.stdout):
            sublime.set_timeout(self.cleanup_session, 0)

    def clear_position(self):
        if self.last_cursor_view is not None:
            region = self.last_cursor_view.get_regions("dlv.suspend_pos")
            if region is None or len(region) == 0:
                self.last_cursor_view = None
                return
            assert (len(region) == 1)
            row, col = self.last_cursor_view.rowcol(region[0].a)
            bkpt = self.bkpt_view.find_breakpoint(self.last_cursor_view.file_name(), row + 1)
            if self.last_cursor_view is not None:
                self.last_cursor_view.erase_regions("dlv.suspend_pos")
            if bkpt is not None:
                bkpt._show(self.is_running(), self.last_cursor_view)
            self.last_cursor_view = None

    def update_position(self, view):
        self.clear_position()
        if self.is_running() and self.cursor == view.file_name() and self.cursor_position != 0:
            bkpt = self.bkpt_view.find_breakpoint(self.cursor, self.cursor_position)
            if bkpt is not None:
                bkpt._hide(view)
            view.add_regions("dlv.suspend_pos", [view.line(view.text_point(self.cursor_position - 1, 0))], \
                "entity.name.class", "bookmark", sublime.HIDDEN)
            self.last_cursor_view = view

    def add_breakpoint_request(self, requests):
        assert (self.is_running())
        requests.append({"cmd": self.const.BREAKPOINT_COMMAND, "parms": None})

    def add_goroutine_request(self, requests):
        assert (self.is_running())
        requests.append({"cmd": self.const.GOROUTINE_COMMAND, "parms": None})

    def add_watch_request(self, requests):
        assert (self.is_running())
        if self.watch_view.is_watches_exist():
            goroutine_id = self.goroutine_view.get_selected_goroutine_id()
            frame = self.stacktrace_view.get_selected_frame()
            parms = {"watches": self.watch_view.get_watches_as_parm()}
            if goroutine_id > 0:
                parms['goroutine_id'] = goroutine_id
                parms['frame'] = frame
            requests.append({"cmd": self.const.WATCH_COMMAND, "parms": parms})

    def add_variable_request(self, requests, parms):
        assert (self.is_running())
        requests.append({"cmd": self.const.VARIABLE_COMMAND, "parms": parms})

    def is_local_mode(self):
        return self.const.MODE in [self.const.DEBUG_MODE, self.const.TEST_MODE]

    def is_next_enabled(self):
        assert (self.is_running())
        return not self.next_in_progress

def is_project_file_exists(window):
    return window.project_file_name() is not None

def is_equal(first, second):
    return first.id() == second.id()

def set_status_message(message):
    sublime.status_message(message)

def normalize(file):
    if file is None:
        return None
    return os.path.abspath(os.path.normcase(file))

def is_gosource(s):
    if s is None:
        return False
    ext = os.path.splitext(os.path.basename(s))[1]
    if ext is not None  and ext == ".go":
        return True
    else:
        return False

def is_plugin_enable():
    window = sublime.active_window()
    if is_project_file_exists(window) and 'settings' in window.project_data():
        settings = window.project_data()['settings']
        if 'delve_enable' in settings and settings['delve_enable']:
            key = window.id()
            if not key in dlv_project:
                dlv_project[key] = DlvProject(window)
            return True, dlv_project[key]
    return False, None

def worker_callback(prj, responses):
    const = prj.const
    state = None
    update_views = []
    update_marker_views = False
    update_position_view = None
    bkpts_add = [] 
    bkpts_del = []
    commonResult = True

    for response in responses:
        cmd = response['cmd']
        result = response['result']
        error_code = None
        error_message = None
        if not result:
            commonResult = False
            if 'error_code' in response:
                error_code = response['error_code']
                error_message = response['error_message']
        if cmd == const.CREATE_BREAKPOINT_COMMAND:
            new_bkpt = DlvBreakpointType()
            view = prj.bkpt_view
            if result:
                new_bkpt._update(response['response'])
                find_bkpt = view.find_breakpoint(new_bkpt.file, new_bkpt.line)
                if find_bkpt is not None:
                    find_bkpt._update(response['response'])
                    find_bkpt._reset_error_message()
                else:
                    bkpts_add.append(new_bkpt)
            else:
                new_bkpt._update(response['parms'])
                find_bkpt = view.find_breakpoint(new_bkpt.file, new_bkpt.line)
                if find_bkpt is None:
                    bkpts_add.append(new_bkpt)
                    find_bkpt = new_bkpt
                find_bkpt._set_error_message(error_message)
            if view not in update_views:
                update_views.append(view)
        elif cmd == const.CLEAR_BREAKPOINT_COMMAND:
            if result:
                view = prj.bkpt_view
                new_bkpt = DlvBreakpointType()
                new_bkpt._update(response['response'])
                bkpts_del.append(new_bkpt)
                if view not in update_views:
                    update_views.append(view)
        elif cmd == const.BREAKPOINT_COMMAND:
            if result:
                view = prj.bkpt_view
                view.load_data(response['response'])
                update_marker_views = True
                if view not in update_views:
                    update_views.append(view)
        elif cmd == const.GOROUTINE_COMMAND:
            if result:
                view = prj.goroutine_view
                view.load_data(response['response'], response['current_goroutine_id'])
                if view not in update_views:
                    update_views.append(view)
        elif cmd == const.STACKTRACE_COMMAND:
            if result:
                view = prj.stacktrace_view
                view.load_data(response['response'])
                if view not in update_views:
                    update_views.append(view)
        elif cmd == const.VARIABLE_COMMAND:
            if result:
                view = prj.variable_view
                view.load_variable(response['response'])
                if view not in update_views:
                    update_views.append(view)
        elif cmd == const.WATCH_COMMAND:
            if result:
                view = prj.watch_view
                view.load_watch(response['response'])
                if view not in update_views:
                    update_views.append(view)
        elif cmd == const.STATE_COMMAND:
            if not result and error_code != -32803:
                prj.terminate_session()
                return
        if not result and error_code == -32803:
            prj.terminate_session(prj.is_local_mode())
            return
        if result and type(response['response']) is dict and 'State' in response['response']:
            state = DlvStateType()
            state._update(response['response'])
            prj.next_in_progress = state.NextInProgress
            thread = state._get_thread('currentThread')
            if state.exited or thread.goroutineID == 0:
                prj.logger.debug("Process exit with status: %d" % state.exitStatus)
                prj.terminate_session()
                return

    if state is not None:
        thread = state._get_thread('currentThread')
        if thread is not None:
            window = prj.window
            view = window.find_open_file(thread.file)
            if view is None:
                window.focus_group(0)
            update_position_view = window.open_file("%s:%d" % (thread.file, thread.line), sublime.ENCODED_POSITION)
            prj.cursor = thread.file
            prj.cursor_position = thread.line

    prj.bkpt_view.upgrade_breakpoints(bkpts_add, bkpts_del)

    for view in update_views:
        view.update_view()

    if update_marker_views:
        prj.bkpt_view.update_markers()

    if update_position_view is not None:
        prj.update_position(update_position_view)

    if not commonResult:
        set_status_message("Errors occured, details in file: %s" % prj.logger.get_file())

class DlvBreakpointType(DlvObjectType):
    def __init__(self, file=None, line=None, **kwargs):
        super(DlvBreakpointType, self).__init__("Breakpoint", **kwargs)
        self.__file = file
        self.__line = line
        self.__original_line = line
        self.__showed = False
        self.__show_running = False
        self.__uuid = None
        self.__error_message = None

    def __getattr__(self, attr):
        if attr == "file" and self.__file is not None:
            return self.__file
        if attr == "line" and self.__line is not None:
            return self.__line
        return super(DlvBreakpointType, self).__getattr__(attr)

    @property
    def _as_parm(self):
        response = super(DlvBreakpointType, self)._as_parm
        if self.__file is not None:
            response[self._object_name]['file'] = self.__file
        if self.__line is not None:
            response[self._object_name]['line'] = self.__line
        return response

    @property
    def _key(self):
        if self.__original_line is None:
            self.__original_line = self.line
        return "dlv.bkpt%s" % self.__original_line

    def _set_error_message(self, error_message=None):
        self.__error_message = error_message if error_message is not None else '<not available>'

    def _reset_error_message(self):
        self.__error_message = None  

    def _is_error(self):
        return (self.__error_message != None)

    def _set_uuid(self, uuid):
        self.__uuid = uuid

    def _get_uuid(self):
        return self.__uuid

    def _update_line(self, line):
        assert (self.__original_line is not None)
        self.__line = line

    def _show(self, running, view):
        assert (view is not None)
        if not self.__showed or running != self.__show_running:
            icon_file = "Packages/SublimeDelve/%s" % ('bkpt_active.png' if running and not self._is_error() else 'bkpt_inactive.png')
            assert (view.text_point(self.line - 1, 0) != 0)
            view.add_regions(self._key, [view.line(view.text_point(self.line - 1, 0))], "keyword.dlv", icon_file, sublime.HIDDEN)
            self.__showed = True
            self.__show_running = running

    def _hide(self, view):
        assert (view is not None)
        if self.__showed:
            view.erase_regions(self._key)
            self.__showed = False
            self.__show_running = False

    def _was_hided(self):
        self.__showed = False
        self.__show_running = False

    def _is_loaded(self):
        return hasattr(self, 'id')

    def _format(self, running):
        output = "\"%s:%d\"" % (os.path.basename(self.file), self.line)
        if running:
            if not self._is_error():
                if self._is_loaded():
                    output +=  " %d" % self.id
            else:
                output +=  " \"%s\"" % self.__error_message
        return output

class DlvStateType(DlvObjectType):
    def __init__(self, **kwargs):
        super(DlvStateType, self).__init__("State", **kwargs)

    def _get_thread(self, name=None):
        thread = DlvThreadType()
        if name is None:
            name = thread._object_name
        value = self._kwargs.get(name, None)
        if value is not None:
            obj_value = {}
            obj_value[thread._object_name] = value
            thread._update(obj_value)
            return thread
        else:
            return None

class DlvLocationType(DlvObjectType):
    def __init__(self, **kwargs):
        super(DlvLocationType, self).__init__("Location", **kwargs)

    def _get_variables(self):
        variables = []
        for element in self.Locals:
            var = DlvtVariableType()
            var._update({"Variable": element})
            variables.append(var)
        for element in self.Arguments:
            var = DlvtVariableType()
            var._update({"Variable": element})
            variables.append(var)
        return variables

    def _format(self):
        return "%s \"%s:%d\"" % (os.path.basename(self.function['name']), os.path.basename(self.file), self.line)

class DlvThreadType(DlvObjectType):
    def __init__(self, **kwargs):
        super(DlvThreadType, self).__init__("Thread", **kwargs)

    def _get_breakpoint(self, name=None):
        breakpoint = DlvBreakpointType(self.file, self.line)
        if name is None:
            name = breakpoint._object_name
        value = self._kwargs.get(name, None)
        if value is not None:
            obj_value = {}
            obj_value[breakpoint._object_name] = value
            breakpoint._update(obj_value)
            return breakpoint
        else:
            return None

    def _format(self):
        return "%d\t%s" % (self.id, self.function['name'])

class DlvGoroutineType(DlvObjectType):
    def __init__(self, **kwargs):
        super(DlvGoroutineType, self).__init__("Goroutine", **kwargs)

    @property
    def _current_file(self):
        return self.currentLoc['file']

    @property
    def _current_line(self):
        return self.currentLoc['line']

    def _format(self):
        return "%s \"%s:%d\" %d" % (os.path.basename(self.currentLoc['function']['name']), os.path.basename(self.currentLoc['file']), self.currentLoc['line'], self.id)

class DlvSessionView(DlvView):
    def __init__(self, prj, view):
        super(DlvSessionView, self).__init__(prj.const.SESSION_VIEW, prj.window, prj.const, view, True)
        self.__prj = prj

class DlvConsoleView(DlvView):
    def __init__(self, prj, view):
        super(DlvConsoleView, self).__init__(prj.const.CONSOLE_VIEW, prj.window, prj.const, view, True)
        self.__prj = prj

class DlvBreakpointView(DlvView):
    def __init__(self, prj, view):
        super(DlvBreakpointView, self).__init__(prj.const.BREAKPOINT_VIEW, prj.window, prj.const, view)
        self.__prj = prj
        self.__breakpoints = []
        if self.const.SAVE_BREAKPOINT:
            data = self.const.load_breakpoints()
            bkpts_add = [] 
            for element in data:
                bkpts_add.append(DlvBreakpointType(element['file'], element['line']))
            self.upgrade_breakpoints(bkpts_add)

    def open(self, reset=False):
        super(DlvBreakpointView, self).open(reset)
        if self.is_open():
            self.set_syntax("Packages/SublimeDelve/SublimeDelve.tmLanguage")
            if not self.__prj.is_running():
                self.update_breakpoint_lines()
            self.update_view()

    def hide_view_breakpoints(self, view):
        for bkpt in self.__breakpoints:
            if bkpt.file == view.file_name():
                bkpt._was_hided()

    def select_breakpoint(self, view):
        row, col = view.rowcol(view.sel()[0].a)
        if len(self.__breakpoints) > 0:
            bkpt = self.__breakpoints[row]
            find_view = self.window.find_open_file(bkpt.file)
            if find_view is None:
                self.window.focus_group(0)
            self.window.open_file("%s:%d" % (bkpt.file, bkpt.line), sublime.ENCODED_POSITION)

    def upgrade_breakpoints(self, bkpts_add=[], bkpts_del=[]):
        need_update = False
        for bkpt in bkpts_add:
            cur_bkpt = self.find_breakpoint(bkpt.file, bkpt.line)
            assert (cur_bkpt is None)
            cur_bkpt = bkpt
            self.__breakpoints.append(cur_bkpt)
            update_view = self.window.find_open_file(cur_bkpt.file)
            if update_view is not None:
                running = self.__prj.is_running()
                if not running or running and not \
                            (self.__prj.cursor_position == cur_bkpt.line and self.__prj.cursor == cur_bkpt.file):
                    cur_bkpt._show(running, update_view)
            need_update = True
        for bkpt in bkpts_del:
            cur_bkpt = self.find_breakpoint(bkpt.file, bkpt.line)
            if cur_bkpt is None:
                self.__prj.logger.debug("Breakpoint %s:%d not found, skip update" % (bkpt.file, bkpt.line))
                continue
            update_view = self.window.find_open_file(cur_bkpt.file)
            if update_view is not None:
                cur_bkpt._hide(update_view)
            self.__breakpoints.remove(cur_bkpt)
            need_update = True
        return need_update

    def __get_marker_views(self):
        views = []
        for bkpt in self.__breakpoints:
            view = self.window.find_open_file(bkpt.file)
            if view is None:
                continue
            if view not in views:
                views.append(view)
        return views

    def update_markers(self, views=None):
        if views is None:
            views = self.__get_marker_views()
        for view in views:
            file = view.file_name()
            assert (file is not None)
            for bkpt in self.__breakpoints:
                if bkpt.file == file:
                    running = self.__prj.is_running()
                    if not running or running and not \
                                (self.__prj.cursor_position == bkpt.line and self.__prj.cursor == bkpt.file):
                        bkpt._show(running, view)

    def clear_markers(self):
        for bkpt in self.__breakpoints:
            view = self.window.find_open_file(bkpt.file)
            if view is None:
                continue
            bkpt._hide(view)
                            
    def update_view(self):
        super(DlvBreakpointView, self).update_view()
        if not self.is_open():
            return
        self.__breakpoints.sort(key=lambda b: (b.file, b.line))
        running = self.__prj.is_running()
        for bkpt in self.__breakpoints:
            self.add_line(bkpt._format(running))

    def find_breakpoint_by_idx(self, idx):
        if idx >= 0 and idx < len(self.__breakpoints):
            return self.__breakpoints[idx]
        return None

    def find_breakpoint(self, file, line=None):
        for bkpt in self.__breakpoints:
            if bkpt.file == file and (line is None or line is not None and bkpt.line == line):
                return bkpt
        return None

    def load_data(self, data):
        bkpts_add = [] 
        bkpts_del = []
        bkpt_uuid = uuid.uuid4()        
        for element in data['Breakpoints']:
            cur_bkpt = DlvBreakpointType()
            cur_bkpt._update({"Breakpoint": element})
            if cur_bkpt.id <= 0:
                continue
            else:
                cur_bkpt._set_uuid(bkpt_uuid)
            bkpt = self.find_breakpoint(cur_bkpt.file, cur_bkpt.line)
            if bkpt is None:
                bkpts_add.append(cur_bkpt)
            else:
                bkpt._update({"Breakpoint": element})
                bkpt._set_uuid(bkpt_uuid)
        for bkpt in self.__breakpoints:
            if bkpt._get_uuid() != bkpt_uuid and not bkpt._is_error():
                bkpts_del.append(bkpt)
        self.upgrade_breakpoints(bkpts_add, bkpts_del)

    def toggle_breakpoint(self, elements):
        assert (len(elements) > 0)
        requests = []
        bkpts_add = [] 
        bkpts_del = []
        bkpts_error_del = []
        for element in elements:
            bkpt = self.find_breakpoint(element['file'], element['line'])
            if bkpt is not None:
                if self.__prj.is_running():
                    if not bkpt._is_error():
                        requests.append({"cmd": self.const.CLEAR_BREAKPOINT_COMMAND, "parms": {"bkpt_id": bkpt.id, "bkpt_name": bkpt.name}})
                    else:
                        bkpts_error_del.append(bkpt)
                else:
                    bkpts_del.append(bkpt)
            else:
                value = element['value']
                if not value.startswith('//') and not value.startswith('/*') and not value.endswith('*/'):
                    bkpt = DlvBreakpointType(element['file'], element['line'])
                    requests.append({"cmd": self.const.CREATE_BREAKPOINT_COMMAND, "parms": bkpt._as_parm})
                    bkpts_add.append(bkpt)
                else:
                    self.__prj.logger.debug("Source line %s:%d is commented, skip add breakpoint" % (element['file'], element['line']))
        if self.__prj.is_running():
            if len(requests) > 0:
                self.__prj.worker.do_batch(requests)
            if len(bkpts_error_del) > 0 and self.upgrade_breakpoints([], bkpts_error_del):
                self.update_view()
        else:
            if self.upgrade_breakpoints(bkpts_add, bkpts_del):
                self.update_view()
     
    def sync_breakpoints(self):
        requests = []
        bkpts = []
        for bkpt in self.__breakpoints:
            requests.append({"cmd": self.const.CREATE_BREAKPOINT_COMMAND, "parms": bkpt._as_parm})
            bkpts.append({"file": bkpt.file, "line": bkpt.line})
        requests.append({"cmd": self.const.CONTINUE_COMMAND, "parms": None})
        self.__prj.add_goroutine_request(requests)
        if len(requests) > 0:
            self.__prj.add_breakpoint_request(requests)
        self.__prj.worker.do_batch(requests)
        if self.const.SAVE_BREAKPOINT:
            self.const.save_breakpoints(bkpts)
        if self.const.SAVE_WATCH:
            self.__prj.watch_view.save_watches()

    def update_breakpoint_lines(self, view=None):
        got_changes = False
        for bkpt in self.__breakpoints:
            cur_view = view
            if view is None:
                cur_view = self.window.find_open_file(bkpt.file)
                if cur_view is None:
                    continue
            else:
                if bkpt.file != view.file_name():
                    continue
                else:
                    cur_view = view
            region = cur_view.get_regions(bkpt._key)
            assert (len(region) == 1)
            row, col = cur_view.rowcol(region[0].a)
            row += 1
            if bkpt.line != row:
                bkpt._update_line(row)
                got_changes = True
        return got_changes

class DlvStacktraceView(DlvView):
    def __init__(self, prj, view):
        super(DlvStacktraceView, self).__init__(prj.const.STACKTRACE_VIEW, prj.window, prj.const, view)
        self.__prj = prj
        self.__reset()

    def __reset(self):
        self.__locations = []
        self.__cursor_position = 0

    def open(self, reset=False):
        super(DlvStacktraceView, self).open(reset)
        if self.is_open():
            self.set_syntax("Packages/SublimeDelve/SublimeDelve.tmLanguage")
            if reset:
                self.__reset()
            self.update_view()

    def clear(self, reset=False):
        if reset:
            self.__reset()
        super(DlvStacktraceView, self).clear(reset)

    def get_selected_frame(self):
        return self.__cursor_position

    def select_location(self, view=None):
        if len(self.__locations) == 0:
            self.view.erase_regions("dlv.location_pos")
            return
        loc = None
        if view is not None:
            new_row, new_col = self.view.rowcol(view.sel()[0].a)
            if new_row == self.__cursor_position or new_row >= len(self.__locations):
                return
            else:
                self.view.erase_regions("dlv.location_pos")
                self.__cursor_position = new_row
                loc = self.__locations[new_row]
                find_view = self.window.find_open_file(loc.file)
                if find_view is None:
                    self.window.focus_group(0)
                self.window.open_file("%s:%d" % (loc.file, loc.line), sublime.ENCODED_POSITION)
        if loc is None:
            loc = self.__locations[self.__cursor_position]
        self.view.add_regions("dlv.location_pos", [self.view.line(self.view.text_point(self.__cursor_position, 0))], \
            "entity.name.class", "bookmark" if self.__prj.goroutine_view.is_current_goroutine_selected() and \
                        self.__cursor_position == 0 else "dot", sublime.HIDDEN)
        goroutine_id = self.__prj.goroutine_view.get_selected_goroutine_id()
        assert (goroutine_id > 0)
        requests = []
        self.__prj.add_variable_request(requests, {"goroutine_id": goroutine_id, "frame": self.__cursor_position})
        self.__prj.add_watch_request(requests)
        self.__prj.worker.do_batch(requests)

    def load_data(self, data):
        self.__reset()
        if not self.__prj.is_running():
            return
        for element in data['Locations']:
            loc = DlvLocationType()
            loc._update({"Location": element})
            self.__locations.append(loc)

    def update_view(self):
        super(DlvStacktraceView, self).update_view()
        if not self.is_open():
            return
        for loc in self.__locations:
            self.add_line(loc._format(), '')
        self.select_location()

class DlvGoroutineView(DlvView):
    def __init__(self, prj, view):
        super(DlvGoroutineView, self).__init__(prj.const.GOROUTINE_VIEW, prj.window, prj.const, view)
        self.__prj = prj
        self.__reset()

    def __reset(self):
        self.__goroutines = []                
        self.__cursor_position = 0
        self.__current_goroutine_position = -1
        self.__selected_goroutine_id = 0

    def open(self, reset=False):
        super(DlvGoroutineView, self).open(reset)
        if self.is_open():
            self.set_syntax("Packages/SublimeDelve/SublimeDelve.tmLanguage")
            if reset:
                self.__reset()
            self.update_view()

    def clear(self, reset=False):
        if reset:
            self.__reset()
        super(DlvGoroutineView, self).clear(reset)

    def is_current_goroutine_selected(self):
        return self.__cursor_position == self.__current_goroutine_position

    def get_selected_goroutine_id(self):
        return self.__selected_goroutine_id

    def select_goroutine(self, view=None):
        if len(self.__goroutines) == 0:
            self.view.erase_regions("dlv.goroutine_pos")
            return
        gr = None
        if view is not None:
            new_row, new_col = self.view.rowcol(view.sel()[0].a)
            if new_row == self.__cursor_position or new_row >= len(self.__goroutines):
                return
            else:
                self.view.erase_regions("dlv.goroutine_pos")
                self.__cursor_position = new_row
                gr = self.__goroutines[new_row]
                find_view = self.window.find_open_file(gr._current_file)
                if find_view is None:
                    self.window.focus_group(0)
                self.window.open_file("%s:%d" % (gr._current_file, gr._current_line), sublime.ENCODED_POSITION)
        if gr is None:
            gr = self.__goroutines[self.__cursor_position]
        self.__selected_goroutine_id = gr.id
        self.view.add_regions("dlv.goroutine_pos", [self.view.line(self.view.text_point(self.__cursor_position, 0))], \
            "entity.name.class", "dot" if not self.is_current_goroutine_selected() else "bookmark", sublime.HIDDEN)
        self.__prj.worker.do(self.const.STACKTRACE_COMMAND, {"goroutine_id": self.__selected_goroutine_id})

    def load_data(self, data, current_goroutine_id):
        self.__reset()
        if not self.__prj.is_running():
            return
        idx = 0
        for element in data['Goroutines']:
            gr = DlvGoroutineType()
            gr._update({"Goroutine": element})
            self.__goroutines.append(gr)
            if gr.id == current_goroutine_id:
                self.__cursor_position = idx
                self.__current_goroutine_position = idx
                self.__selected_goroutine_id = current_goroutine_id
            idx += 1

    def update_view(self):
        super(DlvGoroutineView, self).update_view()
        if not self.is_open():
            return
        for gr in self.__goroutines:
            self.add_line(gr._format(), '')
        self.select_goroutine()

class DlvtVariableType(DlvObjectType):
    def __init__(self, parent=None, name=None, **kwargs):
        super(DlvtVariableType, self).__init__("Variable", **kwargs)
        self.__parent = parent
        self.__name = name
        self.__children = []
        self.__expanded = False
        self.__line = 0
        self.__map_element = False
        self.__uuid = None
        self.__error_message = None

    def __getattr__(self, attr):
        if attr == "name" and self.__name is not None:
            return self.__name
        return super(DlvtVariableType, self).__getattr__(attr)

    @property
    def _children(self):
        return self.__children

    @property
    def _line(self):
        return self.__line

    def _set_name(self, name):
        assert (self.__name is None)
        self.__name = name

    def _set_error_message(self, error_message=None):
        self.__error_message = error_message if error_message is not None else '<not available>'

    def _reset_error_message(self):
        self.__error_message = None        

    def _set_map_key(self, key):
        assert (self.__name is None)
        self.__name = key
        self.__map_element = True

    @property
    def _uuid(self):
        if self.__uuid is None:
            self.__uuid = uuid.uuid4()
        return self.__uuid

    def _is_loaded(self):
        return hasattr(self, 'addr')

    def _is_error(self):
        return (self.__error_message != None)

    def _format(self, running, indent="", output="", line=0):
        self.__line = line
        line += 1
        icon = " "
        if self._is_error() or not self._is_loaded() or not running:
            return ("%s%s = \"%s\"" % (icon, self.name, self.__error_message if running and self._is_error() else '<not available>'), line)
        if self._has_children():
            if self.__expanded:
                icon = "-"
            else:
                icon = "+"

        length = self.len
        capacity = self.cap
        if self._is_pointer():
            length = self._dereference()['len']
            capacity = self._dereference()['cap']
        suffix_len_cap = ""
        suffix_len = str(length) if length > 0 or (length >= 0 and self._is_slice()) else ""
        suffix_cap = str(capacity) if capacity > 0 or (capacity >= 0 and self._is_slice()) else ""

        if suffix_len != "" and suffix_cap != "":
            suffix_len_cap = "(len: %s, cap: %s)" % (suffix_len, suffix_cap)
        elif suffix_len != "":
            suffix_len_cap = "(len: %s)" % suffix_len
        elif suffix_cap != "":
            suffix_len_cap = "(cap: %s)" % suffix_cap

        suffix_val = ""
        chldn_len = len(self.children)
        if chldn_len == 0 and not self._is_slice() and not self._is_map():
            val = str(self.value)
            if self._is_string():
                val = '"%s"' % val
            if not self.__map_element:
                suffix_val = " = "
            suffix_val += val

        if output != "":
            output += "\n"
        if not self._is_map_element():
            output += "%s%s%s %s%s%s" % (indent, icon, self.name, self.type, suffix_len_cap, suffix_val)
        elif self._is_slice() or self._is_map():
            output += "%s%s%s: %s%s" % (indent, icon, self.name, self.type, suffix_len_cap)
        else:
            output += "%s%s%s: %s" % (indent, icon, self.name, suffix_val)

        indent += "    "
        if self.__expanded:
            for chld_var in self.__children:
                output, line = chld_var._format(running, indent, output, line)
        return (output, line)

    def _is_expanded(self):
        return self.__expanded

    def _is_expanded(self):
        return self.__expanded

    def _is_string(self): 
        return (self.type == 'string')

    def _is_slice(self): 
        return self.type.startswith('[')

    def _is_map(self):
        return self.type.startswith('map[')

    def _is_map_element(self):
        return self.__map_element

    def _is_pointer(self): 
        return self.type.startswith('*')

    def _dereference(self):
        assert (self._is_pointer() and len(self.children) == 1)
        return self.children[0]

    def _expand(self):
        self.__expanded = True
        if len(self.children) > 0 and len(self.__children) == 0:
            self.__add_children()

    def _collapse(self):
        self.__expanded = False

    def __add_children(self):
        counter = 0
        map_element_key = None
        children = self.children
        if self._is_pointer():
            children = self._dereference()['children']
        for child in children:
            chld_var = DlvtVariableType(self)
            chld_var._update({"Variable": child})
            if chld_var.name == "" and self._is_slice():
                chld_var._set_name(counter)
                counter += 1
            if self._is_map():
                if map_element_key is None:
                    val = chld_var.value
                    if chld_var._is_string():
                        val = '"%s"' % val
                    map_element_key = val
                    continue
                else:
                    chld_var._set_map_key(map_element_key)
                    map_element_key = None
            self.__children.append(chld_var)
    
    def _has_children(self):
        return len(self.children) > 0

class DlvVariableView(DlvView):
    def __init__(self, name, prj, view=None):
        super(DlvVariableView, self).__init__(name, prj.window, prj.const, view)
        self.__prj = prj
        self.__reset()
        if self.name == self.const.WATCH_VIEW and self.const.SAVE_BREAKPOINT:
            data = self.const.load_watches()
            for element in data:
                self.__variables.append(DlvtVariableType(name=element))
    
    def __reset(self):
        self.__variables = []

    def open(self, reset=False):
        super(DlvVariableView, self).open(reset)
        if self.is_open():
            self.set_syntax("Packages/SublimeDelve/SublimeDelve.tmLanguage")
            if reset and self.name == self.const.VARIABLE_VIEW:
                self.__reset()
            self.update_view()

    def clear(self, reset=False):
        if reset and self.name == self.const.VARIABLE_VIEW:
            self.__reset()
        super(DlvVariableView, self).clear(reset)

    def find_watch_by_uuid(self, uuid):
        assert (self.name == self.const.WATCH_VIEW)
        for var in self.__variables:
            if var._uuid == uuid:
                return var
        return None

    def load_variable(self, data):
        assert (self.name == self.const.VARIABLE_VIEW)
        self.__reset()
        if not self.__prj.is_running():
            return
        for element in data['Locals']:
            var = DlvtVariableType()
            var._update({"Variable": element})
            self.__variables.append(var)
        for element in data['Arguments']:
            var = DlvtVariableType()
            var._update({"Variable": element})
            self.__variables.append(var)

    def load_watch(self, data):
        assert (self.name == self.const.WATCH_VIEW)
        for element in data:
            if element['result']:
                var = self.find_watch_by_uuid(element['watch_id'])
                if var is None:
                    self.__prj.logger.debug("Watch with uuid %s not found, skip update" % element['watch_id'])
                    continue
                cur_var = DlvtVariableType()
                var._update(element['eval'])
                var._reset_error_message()
            else:
                var = self.find_watch_by_uuid(element['parms']['watch_id'])
                if var is None:
                    self.__prj.logger.debug("Watch with uuid %s not found, skip update" % element['watch_id'])
                    continue
                if 'error_message' in element:
                    var._set_error_message(element['error_message'])   
                else:
                    var._set_error_message()   

    def update_view(self):
        super(DlvVariableView, self).update_view()
        if not self.is_open():
            return
        line = 0
        running = self.__prj.is_running()
        for var in self.__variables:
            output, line = var._format(running, line=line)
            self.add_line(output, ' ')

    def get_variable_at_line(self, line, var_list=None):
        if var_list is None:
            var_list = self.__variables
        if len(var_list) == 0:
            return None

        for i in range(len(var_list)):
            if var_list[i]._line == line:
                return var_list[i]
            elif var_list[i]._line > line:
                return self.get_variable_at_line(line, var_list[i-1]._children)
        return self.get_variable_at_line(line, var_list[len(var_list)-1]._children)

    def expand_collapse_variable(self, view, expand=True, toggle=False):
        row, col = view.rowcol(view.sel()[0].a)
        if self.is_open() and view.id() == self.id():
            var = self.get_variable_at_line(row)
            if var is not None and not var._is_error() and var._has_children():
                if toggle:
                    if var._is_expanded():
                        var._collapse()
                    else:
                        var._expand()
                elif expand:
                    var._expand()
                else:
                    var._collapse()
                self.update_view()
                self.view.show_at_center(self.view.text_point(row,0))

    def exist(self, expr):
        assert (self.name == self.const.WATCH_VIEW)
        found = False
        for var in self.__variables:
            if var.name == expr:
                found = True
                break
        return found

    def __edit_on_done(self, expr):
        assert (self.name == self.const.WATCH_VIEW)
        if expr.strip() == "" or self.exist(expr.strip()):
            return
        var = DlvtVariableType(name=expr.strip())
        self.__variables.append(var)
        if self.__prj.is_running():
            goroutine_id = self.__prj.goroutine_view.get_selected_goroutine_id()
            frame = self.__prj.stacktrace_view.get_selected_frame()
            parms = {"watches": [{"watch_id": var._uuid, "expr": var.name}]}
            if goroutine_id > 0:
                parms['goroutine_id'] = goroutine_id
                parms['frame'] = frame
            self.__prj.worker.do(self.const.WATCH_COMMAND, parms)
        else:
            self.update_view()

    def add_watch(self, view):
        assert (self.name == self.const.WATCH_VIEW)
        value = view.substr(view.sel()[0])
        self.window.show_input_panel('Delve add watch =', value, self.__edit_on_done, None, None)

    def remove_watch(self, view):
        assert (self.name == self.const.WATCH_VIEW)
        row, col = view.rowcol(view.sel()[0].a)
        if row < len(self.__variables):
            var = self.__variables[row]
            self.__variables.remove(var)
            self.update_view()

    def get_watches_as_parm(self):
        assert (self.name == self.const.WATCH_VIEW)
        response = []
        for var in self.__variables:
            response.append({"watch_id": var._uuid, "expr": var.name})
        return response

    def is_watches_exist(self):
        assert (self.name == self.const.WATCH_VIEW)
        return (len(self.__variables) > 0)

    def save_watches(self):
        assert (self.name == self.const.WATCH_VIEW)
        watches = []
        for watch in self.__variables:
            watches.append(watch.name)
        self.const.save_watches(watches)

class DlvToggleBreakpoint(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        elements = []
        update_view = self.view
        file = self.view.file_name()
        if prj.bkpt_view.is_open() and is_equal(self.view, prj.bkpt_view):
            line = self.view.rowcol(self.view.sel()[0].begin())[0]
            bkpt = prj.bkpt_view.find_breakpoint_by_idx(line)
            if bkpt is not None:
                elements.append({"file": bkpt.file, "line": bkpt.line})
            else:
                prj.logger.debug("Breakpoint by idx %d not found, skip toggle" % line)
        elif file is not None: # code source file
            if not prj.is_running():
                prj.bkpt_view.update_breakpoint_lines(self.view)
            for sel in self.view.sel():
                line, col = self.view.rowcol(sel.a)
                value = ''.join(self.view.substr(self.view.line(self.view.text_point(line, 0))).split())
                if len(value) > 0:
                    elements.append({"file": file, "line": line + 1, "value": value})
                else:
                    prj.logger.debug("Source line %d is empty, skip toggle" % line + 1)
        if len(elements) > 0:
            prj.bkpt_view.toggle_breakpoint(elements)    

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return False
        view = prj.window.active_view()
        return (view.file_name() is not None or prj.bkpt_view.is_open() and is_equal(view, prj.bkpt_view))

    def is_visible(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return False
        view = prj.window.active_view()
        return (view.file_name() is not None or prj.bkpt_view.is_open() and is_equal(view, prj.bkpt_view))

class DlvClick(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        if not ok or ok and not prj.is_running():
            return
        if prj.variable_view.is_open() and is_equal(self.view, prj.variable_view):
            prj.variable_view.expand_collapse_variable(self.view, toggle=True)
        elif prj.watch_view.is_open() and is_equal(self.view, prj.watch_view):
            prj.watch_view.expand_collapse_variable(self.view, toggle=True)
        elif prj.goroutine_view.is_open() and is_equal(self.view, prj.goroutine_view):
            prj.goroutine_view.select_goroutine(self.view)
        elif prj.stacktrace_view.is_open() and is_equal(self.view, prj.stacktrace_view):
            prj.stacktrace_view.select_location(self.view)

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return ok

class DlvDoubleClick(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        if ok and prj.bkpt_view.is_open() and is_equal(self.view, prj.bkpt_view):
            prj.bkpt_view.select_breakpoint(self.view)
 
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return ok

class DlvCollapseVariable(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        if ok and is_equal(self.view, prj.variable_view):
            prj.variable_view.expand_collapse_variable(self.view, expand=False)
        elif ok and is_equal(self.view, prj.watch_view):
            prj.watch_view.expand_collapse_variable(self.view, expand=False)

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        if not ok or ok and not prj.is_running():
            return False
        if prj.variable_view.is_open() and is_equal(self.view, prj.variable_view):
            return True
        elif prj.watch_view.is_open() and is_equal(self.view, prj.watch_view):
            return True
        return False

class DlvExpandVariable(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        if ok and is_equal(self.view, prj.variable_view):
            prj.variable_view.expand_collapse_variable(self.view)
        elif ok and is_equal(self.view, prj.watch_view):
            prj.watch_view.expand_collapse_variable(self.view)

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        if not ok or ok and not prj.is_running():
            return False
        if prj.variable_view.is_open() and is_equal(self.view, prj.variable_view):
            return True
        elif prj.watch_view.is_open() and is_equal(self.view, prj.watch_view):
            return True
        return False

class DlvAddWatch(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        prj.watch_view.add_watch(self.view)

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return ok

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return ok

class DlvRemoveWatch(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        prj.watch_view.remove_watch(self.view)

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.watch_view.is_open() and is_equal(self.view, prj.watch_view))

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.watch_view.is_open() and is_equal(self.view, prj.watch_view))

class DlvEventListener(sublime_plugin.EventListener):
    def on_query_context(self, view, key, operator, operand, match_all):
        ok, prj = is_plugin_enable()
        if not ok:
            return None
        if key == "dlv_running":
            return prj.is_running() == operand
        elif key == "dlv_next_enable":
            return prj.is_next_enabled() == operand
        elif key == "dlv_input_view":
            return prj.check_input_view(view)
        elif key.startswith("dlv_"):
            v = prj.variable_view if is_equal(prj.variable_view, view) else None
            if v is None:
                v = prj.watch_view if is_equal(prj.watch_view, view) else None
            return None if v is None else True == operand
        return None

    def on_activated(self, view):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        if view.is_loading():
            return
        if view.file_name() is not None:
            prj.bkpt_view.update_markers([view])
            prj.update_position(view)

    def on_load(self, view):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        for v in prj.get_views():
            if view.name() is not None and view.name() == prj.const.get_view_setting(v.name, prj.const.TITLE) and not is_equal(v, view):
                prj.logger.debug("Closed orphan debugger view '%s'" % view.name())
                new_view = DlvView(None, prj.window, prj.const, view)
                new_view.close()
                return
            if v.is_dirty():
                v.reset_dirty()
                v.close()
        v = prj.console_view
        if view.name() is not None and view.name() == prj.const.get_view_setting(v.name, prj.const.TITLE) and not is_equal(v, view):
            prj.logger.debug("Closed orphan debugger view '%s'" % view.name())
            new_view = DlvView(None, prj.window, prj.const, view)
            new_view.close()
            return
        if v.is_dirty():
            v.reset_dirty()
            v.close()
        if view.file_name() is not None:
            prj.bkpt_view.update_markers([view])
            prj.update_position(view)

    def on_modified(self, view):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        v = prj.bkpt_view
        if not prj.is_running() and v.is_open():
            if v.update_breakpoint_lines(view):
                v.update_view()

    def on_pre_close(self, view):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        if not prj.is_running():
            if prj.bkpt_view.update_breakpoint_lines(view):
                prj.bkpt_view.update_view()
        elif is_equal(view, prj.session_view):
            prj.terminate_session(prj.is_local_mode())

    def on_close(self, view):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        for v in prj.get_views():
            if v.is_open() and is_equal(view, v):
                v.was_closed()
                break
        if prj.console_view.is_open() and is_equal(view, prj.console_view):
            prj.console_view.was_closed()
        prj.bkpt_view.hide_view_breakpoints(view)

class DlvStart(sublime_plugin.WindowCommand):
    def __create_cmd(self, prj):
        value = "dlv"
        cmd_server = []
        cmd_session = []
        cmd_server.append(value)
        cmd_session.append(value)
        if prj.is_local_mode():
            cmd_server.append(prj.const.MODE)
        cmd_session.append("connect")
        cmd_server.append("--headless")
        cmd_server.append("--accept-multiclient")
        cmd_server.append("--api-version=2")
        if prj.const.LOG:
            cmd_server.append("--log")
        value = "%s:%d" % (prj.const.HOST, prj.const.PORT)
        cmd_server.append("--listen=%s" % value)
        cmd_session.append(value)
        if prj.const.ARGS != "":
            cmd_server.append("--")
            cmd_server.append(prj.const.ARGS)
        return (cmd_session, cmd_server)

    def __get_cwd(self, prj):
        value = prj.const.CWD
        cwd = None
        if value != "":
            cwd = value
        else:
            file_name = prj.window.project_file_name()
            if file_name is None:
                file_name = prj.window.active_view().file_name()
            if file_name is not None:
                cwd = os.path.dirname(file_name)
        return cwd

    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        if prj.const.is_project_executable():
            prj.const.clear_project_executable()
            prj.logger.debug("Cleared project executable settings")
        exec_choices = prj.const.get_project_executables()
        if exec_choices is None:
            self.__launch(prj)
            return

        def on_choose(index):
            if index == -1:
                # User cancelled the panel, abort launch
                return
            exec_name = list(exec_choices)[index]
            prj.const.set_project_executable(exec_name)
            self.__launch(prj)

        self.window.show_quick_panel(list(exec_choices), on_choose)

    def __launch(self, prj):
        const = prj.const
        logger = prj.logger
        logger.start(const.DEBUG_FILE)
        if const.is_project_executable():
            logger.debug("Set project executable settings: %s" % const.get_project_executable_name())

        prj.panel_on_start()

        if prj.is_local_mode():
            v = prj.console_view 
            if v.is_closed():
                if v.is_open_at_start():
                    v.open(True)
            else:
                if v.is_open_at_start():
                    v.clear(True)
                else:
                    v.close()
            if v.is_open():
                logger.debug("Console view is ready")

        for v in prj.get_views():
            if v.is_closed():
                if v.is_open_at_start():
                    v.open(True)
            else:
                if v.is_open_at_start():
                    v.clear(True)
                else:
                    v.close()
        logger.debug("Debugging views is ready")

        cmd_session, cmd_server = self.__create_cmd(prj)        
        if prj.is_local_mode():
            prj.load_server_subprocess(cmd_server, cmd_session, self.__get_cwd(prj))
        else:
            prj.load_session_subprocess(cmd_session)

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and not prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and not prj.is_running())

class DlvResume(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        requests = []
        requests.append({"cmd": prj.const.CONTINUE_COMMAND, "parms": None})
        prj.add_goroutine_request(requests)
        prj.worker.do_batch(requests)
    
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvNext(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        requests = []
        requests.append({"cmd": prj.const.NEXT_COMMAND, "parms": None})
        prj.add_goroutine_request(requests)
        prj.worker.do_batch(requests)
    
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.is_next_enabled())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.is_next_enabled())

class DlvStepIn(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        requests = []
        requests.append({"cmd": prj.const.STEP_COMMAND, "parms": None})
        prj.add_goroutine_request(requests)
        prj.worker.do_batch(requests)
    
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvStepOut(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        requests = []
        requests.append({"cmd": prj.const.STEPOUT_COMMAND, "parms": None})
        prj.add_goroutine_request(requests)
        prj.worker.do_batch(requests)
    
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvRestart(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        requests = []
        requests.append({"cmd": prj.const.RESTART_COMMAND, "parms": None})
        requests.append({"cmd": prj.const.CONTINUE_COMMAND, "parms": None})
        prj.add_goroutine_request(requests)
        prj.worker.do_batch(requests)
    
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvCancelNext(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        requests = []
        requests.append({"cmd": prj.const.CANCEL_NEXT_COMMAND, "parms": None})
        prj.add_goroutine_request(requests)
        prj.worker.do_batch(requests)
    
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and not prj.is_next_enabled())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and not prj.is_next_enabled())

class DlvStop(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        prj.terminate_session(prj.is_local_mode())

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvInput(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        prj.show_input()

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvPrevCmd(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        if prj.command_history_pos > 0:
            prj.command_history_pos -= 1
        if prj.command_history_pos < len(prj.command_history):
            prj.set_input(edit, prj.command_history[prj.command_history_pos])

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvNextCmd(sublime_plugin.TextCommand):
    def run(self, edit):
        ok, prj = is_plugin_enable()
        if not ok:
            return
        if prj.command_history_pos < len(prj.command_history):
            prj.command_history_pos += 1
        if prj.command_history_pos < len(prj.command_history):
            prj.set_input(edit, prj.command_history[prj.command_history_pos])
        else:
            prj.set_input(edit, "")

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running())

class DlvOpenConsoleView(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if ok and prj.console_view.is_closed():
            prj.console_view.open()

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_local_mode() and prj.is_server_running() and prj.console_view.is_closed())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_local_mode() and prj.is_server_running() and prj.console_view.is_closed())

class DlvOpenBreakpointView(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if ok and prj.bkpt_view.is_closed():
            prj.bkpt_view.open()

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.bkpt_view.is_closed())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.bkpt_view.is_closed())

class DlvOpenVariableView(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if ok and prj.variable_view.is_closed():
            prj.variable_view.open()

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.variable_view.is_closed())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.variable_view.is_closed())

class DlvOpenWatchView(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if ok and prj.watch_view.is_closed():
            prj.watch_view.open()

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.watch_view.is_closed())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.watch_view.is_closed())

class DlvOpenStacktraceView(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if ok and  prj.stacktrace_view.is_closed():
            prj.stacktrace_view.open()

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.stacktrace_view.is_closed())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.stacktrace_view.is_closed())

class DlvOpenGoroutineView(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        if ok and prj.goroutine_view.is_closed():
            prj.goroutine_view.open()

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.goroutine_view.is_closed())

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and prj.is_running() and prj.goroutine_view.is_closed())

class DlvEnable(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        assert (not ok)
        window = sublime.active_window()
        if is_project_file_exists(window):
            data = window.project_data()
            if 'settings' not in data:
                data['settings'] = {}
            data['settings']['delve_enable'] = True
            window.set_project_data(data)
        else:
            sublime.error_message("An open project is required")

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return not ok

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return not ok

class DlvDisable(sublime_plugin.WindowCommand):
    def run(self):
        ok, prj = is_plugin_enable()
        assert (ok)
        prj.bkpt_view.clear_markers()
        for v in prj.get_views():
            if v.is_open():
                v.close()
        data = prj.window.project_data()
        data['settings']['delve_enable'] = False
        prj.window.set_project_data(data)
        dlv_project.pop(prj.window.id())
        
    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return ok

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return ok

class DlvTest(sublime_plugin.WindowCommand):
    def run(self):
        pass

    def is_enabled(self):
        ok, prj = is_plugin_enable()
        return (ok and False)

    def is_visible(self):
        ok, prj = is_plugin_enable()
        return (ok and False)
