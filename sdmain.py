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
        self.const = DlvConst()

        self.cursor = ''
        self.cursor_position = 0
        self.last_cursor_view = None

        self.panel_layout = {}
        self.panel_window = None
        self.panel_view = None

        self.input_view = None
        self.command_history = []
        self.command_history_pos = 0

        self.client_subprocess = None
        self.server_subprocess = None

        log = DlvLogger(self.const)
        self.logger = log
        self.worker = DlvWorker(self.const, log, worker_callback)
        
        self.session_view = self.__initialize_view(window, self.const.SESSION_VIEW)
        self.console_view = self.__initialize_view(window, self.const.CONSOLE_VIEW)
        self.stacktrace_view = self.__initialize_view(window, self.const.STACKTRACE_VIEW)
        self.goroutine_view = self.__initialize_view(window, self.const.GOROUTINE_VIEW)
        self.variable_view = self.__initialize_view(window, self.const.VARIABLE_VIEW)
        self.watch_view = self.__initialize_view(window, self.const.WATCH_VIEW)
        self.bkpt_view = self.__initialize_view(window, self.const.BREAKPOINT_VIEW)

    def get_views(self):
       return [self.session_view, self.variable_view, self.watch_view, self.stacktrace_view, self.bkpt_view, self.goroutine_view]

    def __initialize_view(self, window, name):
        view = None
        for v in window.views():
            if v.name() == self.const.get_view_setting(name, self.const.TITLE):
                view = v
        if name == self.const.SESSION_VIEW:
            return DlvView(name, self.const, view, True)
        elif name == self.const.CONSOLE_VIEW:
            return DlvView(name, self.const, view, True)
        elif name == self.const.STACKTRACE_VIEW:
            return DlvStacktraceView(self.const, view)
        elif name == self.const.GOROUTINE_VIEW:
            return DlvGoroutineView(self.const, view)
        elif name == self.const.VARIABLE_VIEW:
            return DlvVariableView(name, self.const)
        elif name == self.const.WATCH_VIEW:
            return DlvVariableView(name, self.const, view)
        elif name == self.const.BREAKPOINT_VIEW:
            return DlvBreakpointView(self.const, view)

    def reset_cursor(self):
        self.cursor = ''
        self.cursor_position = 0
        self.last_cursor_view = None

    def panel_on_start(self):
        self.panel_window = sublime.active_window()
        self.panel_layout = self.panel_window.get_layout()
        self.panel_view = self.panel_window.active_view()
        self.panel_window.set_layout(self.const.PANEL_LAYOUT)

    def panel_on_stop(self):
        self.panel_window.set_layout(self.panel_layout)
        self.panel_window.focus_view(self.panel_view)

    def check_input_view(self, view):
        return self.input_view is not None and view.id() == self.input_view.id()

def is_project_file_exists():
    return sublime.active_window().project_file_name() is not None

def is_plugin_enable():
    window = sublime.active_window()
    if is_project_file_exists() and 'settings' in window.project_data():
        settings = window.project_data()['settings']
        if 'delve_enable' in settings and settings['delve_enable']:
            key = window.id()
            if not key in dlv_project:
                dlv_project[key] = DlvProject(window)
            return True
    return False

def getp():
    return dlv_project[sublime.active_window().id()]

def get_const():
    return getp().const

def get_logger():
    return getp().logger

def get_worker():
    return getp().worker

def get_session_view():
    return getp().session_view

def get_console_view():
    return getp().console_view

def get_stacktrace_view():
    return getp().stacktrace_view

def get_goroutine_view():
    return getp().goroutine_view

def get_variable_view():
    return getp().variable_view

def get_watch_view():
    return getp().watch_view

def get_bkpt_view():
    return getp().bkpt_view

def get_views():
    return getp().get_views()

def __open_subprocess(cmd, cwd=None):
    return subprocess.Popen(cmd, shell=False, cwd=cwd, universal_newlines=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def open_client_subprocess(cmd):
    proc = __open_subprocess(cmd)
    getp().client_subprocess = proc
    return proc

def open_server_subprocess(cmd, cwd):
    proc = __open_subprocess(cmd, cwd)
    getp().server_subprocess = proc
    return proc

def get_client_proc():
    return getp().client_subprocess

def get_server_proc():
    return getp().server_subprocess

def normalize(file):
    if file is None:
        return None
    return os.path.abspath(os.path.normcase(file))

def set_input(edit, text):
    view = getp().input_view
    view.erase(edit, sublime.Region(0, view.size()))
    view.insert(edit, 0, text)

def show_input():
    project = getp()
    project.command_history_pos = len(project.command_history)
    project.input_view = sublime.active_window().show_input_panel("Delve command", "", input_on_done, input_on_change, input_on_cancel)

def input_on_done(s):
    if not is_running():
        message = "Delve session not found, need to start debugging"
        get_logger().debug(message)
        sublime.status_message(message)
        return

    if s.strip() != "quit" and s.strip() != "exit" and s.strip() != "q":
        getp().command_history.append(s)
        show_input()
    
    run_input_cmd(s)

def input_on_cancel():
    pass

def input_on_change(s):
    pass

def is_running():
    proc =None
    key = sublime.active_window().id()
    if key in dlv_project:
        project = dlv_project[key]
        proc = project.client_subprocess
    return proc is not None and proc.poll() is None

def is_server_running():
    proc = get_server_proc()
    return proc is not None and proc.poll() is None

def is_gosource(s):
    if s is None:
        return False
    ext = os.path.splitext(os.path.basename(s))[1]
    if ext is not None  and ext == ".go":
        return True
    else:
        return False

def is_local_mode():
    return get_const().MODE in [get_const().MODE_DEBUG, get_const().MODE_TEST]

def run_input_cmd(cmd):
    if isinstance(cmd, list):
        for c in cmd:
            run_input_cmd(c)
        return
    elif cmd.strip() == "":
        return
    message = "Input command: %s" % cmd
    get_session_view().add_line(message)
    get_logger().info(message)
    try:
        proc = get_client_proc()
        proc.stdin.write(cmd + '\n')
        proc.stdin.flush()
    except:
        traceback.print_exc(file=(sys.stdout if get_logger().get_file() == get_const().STDOUT else open(get_logger().get_file(),"a")))
        get_logger().error("Exception thrown, details in file: %s" % get_logger().get_file())
    requests = []
    requests.append({"cmd": get_const().STATE_COMMAND, "parms": None})
    if get_watch_view().is_watches_exist():
        requests.append({"cmd": get_const().WATCH_COMMAND, "parms": {"watches": get_watch_view().get_watches_as_parm()}})
    get_worker().do_batch(requests)

def worker_callback(responses):
    update_views = []
    update_position_view = None
    bkpts_add = [] 
    bkpts_del = []
    commonResult = True

    for response in responses:
        result = response['result']
        error_code = None
        error_message = None
        if not result:
            commonResult = False
            if 'error_code' in response:
                error_code = response['error_code']
                error_message = response['error_message']
        if response['cmd'] == get_const().CREATE_BREAKPOINT_COMMAND:
            new_bkpt = DlvBreakpointType()
            if result:
                new_bkpt._update(response['response'])
                find_bkpt = get_bkpt_view().find_breakpoint(new_bkpt.file, new_bkpt.line)
                if find_bkpt is not None:
                    find_bkpt._update(response['response'])
                    find_bkpt._reset_error_message()
                else:
                    bkpts_add.append(new_bkpt)
            else:
                new_bkpt._update(response['parms'])
                find_bkpt = get_bkpt_view().find_breakpoint(new_bkpt.file, new_bkpt.line)
                if find_bkpt is None:
                    bkpts_add.append(new_bkpt)
                    find_bkpt = new_bkpt
                find_bkpt._set_error_message(error_message)
            if get_bkpt_view() not in update_views:
                update_views.append(get_bkpt_view())
        elif response['cmd'] == get_const().CLEAR_BREAKPOINT_COMMAND:
            if result:
                new_bkpt = DlvBreakpointType()
                new_bkpt._update(response['response'])
                bkpts_del.append(new_bkpt)
                if get_bkpt_view() not in update_views:
                    update_views.append(get_bkpt_view())
        elif response['cmd'] == get_const().BREAKPOINT_COMMAND:
            if result:
                get_bkpt_view().load_data(response['response'])
                if get_bkpt_view() not in update_views:
                    update_views.append(get_bkpt_view())
        elif response['cmd'] == get_const().STACKTRACE_COMMAND:
            if result:
                get_stacktrace_view().load_data(response['response'])
                if get_stacktrace_view() not in update_views:
                    update_views.append(get_stacktrace_view())
        elif response['cmd'] == get_const().GOROUTINE_COMMAND:
            if result:
                get_goroutine_view().load_data(response['response'], response['id'])
                if get_goroutine_view() not in update_views:
                    update_views.append(get_goroutine_view())
        elif response['cmd'] == get_const().WATCH_COMMAND:
            if result:
                get_watch_view().load_data(response['response'])
                if get_watch_view() not in update_views:
                    update_views.append(get_watch_view())
        elif response['cmd'] == get_const().STATE_COMMAND:
            if not result and error_code != -32803:
                terminate_session()
                return
        if not result and error_code == -32803:
            terminate_session(is_local_mode())
            return
        if result and 'State' in response['response']:
            state = DlvStateType()
            state._update(response['response'])
            if state.exited:
                get_logger().debug("Process exit with status: %d" % state.exitStatus)
                terminate_session()
                return
            else:
                thread = state._get_thread('currentThread')
                if thread is not None:
                    view = sublime.active_window().find_open_file(thread.file)
                    if view is None:
                        sublime.active_window().focus_group(0)
                    update_position_view = sublime.active_window().open_file("%s:%d" % (thread.file, thread.line), sublime.ENCODED_POSITION)
                    getp().cursor = thread.file
                    getp().cursor_position = thread.line

    get_bkpt_view().upgrade_breakpoints(bkpts_add, bkpts_del)

    for view in update_views:
        view.update_view()

    if update_position_view is not None:
        update_position(update_position_view)

    if not commonResult:
        set_status_message("Errors occured, details in file: %s" % get_logger().get_file())

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

    # def _add(self):
    #     get_worker().do(get_const().CREATE_BREAKPOINT_COMMAND, self._as_parm)

    # def _remove(self):
    #     get_worker().do(get_const().CLEAR_BREAKPOINT_COMMAND, {"id": self.id, "name": self.name})

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

    def _show(self, view):
        running = is_running()
        if not self.__showed or running != self.__show_running:
            icon_file = "Packages/SublimeDelve/%s" % ('bkpt_active.png' if running and not self._is_error() else 'bkpt_inactive.png')
            view.add_regions(self._key, [view.line(view.text_point(self.line - 1, 0))], "keyword.dlv", icon_file, sublime.HIDDEN)
            self.__showed = True
            self.__show_running = running

    def _hide(self, view):
        if self.__showed:
            view.erase_regions(self._key)
            self.__showed = False
            self.__show_running = False

    def _was_hided(self):
        self.__showed = False
        self.__show_running = False

    def _is_loaded(self):
        return hasattr(self, 'id')

    def _format(self):
        output = "\"%s:%d\"" % (os.path.basename(self.file), self.line)
        if is_running():
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
        return "%s \"%s:%d\"" % (self.function['name'], os.path.basename(self.file), self.line)

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
        return "%s \"%s:%d\" %d" % (self.currentLoc['function']['name'], os.path.basename(self.currentLoc['file']), self.currentLoc['line'], self.id)

class DlvBreakpointView(DlvView):
    def __init__(self, const, view):
        super(DlvBreakpointView, self).__init__(const.BREAKPOINT_VIEW, const, view)
        self.__breakpoints = []
        if view is not None and view.settings().has('bkpts'):
            bkpts_add = [] 
            for element in view.settings().get('bkpts'):
                bkpts_add.append(DlvBreakpointType(element['file'], element['line']))
            self.upgrade_breakpoints(bkpts_add)

    def open(self, reset=False):
        super(DlvBreakpointView, self).open(reset)
        if self.is_open():
            self.set_syntax("Packages/SublimeDelve/SublimeDelve.tmLanguage")
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
            find_view = sublime.active_window().find_open_file(bkpt.file)
            if find_view is None:
                sublime.active_window().focus_group(0)
            sublime.active_window().open_file("%s:%d" % (bkpt.file, bkpt.line), sublime.ENCODED_POSITION)

    def upgrade_breakpoints(self, bkpts_add=[], bkpts_del=[]):
        need_update = False
        for bkpt in bkpts_add:
            cur_bkpt = self.find_breakpoint(bkpt.file, bkpt.line)
            assert (cur_bkpt is None)
            cur_bkpt = bkpt
            self.__breakpoints.append(cur_bkpt)
            update_view = sublime.active_window().find_open_file(cur_bkpt.file)
            cur_bkpt._show(update_view)
            need_update = True
        for bkpt in bkpts_del:
            cur_bkpt = self.find_breakpoint(bkpt.file, bkpt.line)
            if cur_bkpt is None:
                continue
            update_view = sublime.active_window().find_open_file(cur_bkpt.file)
            cur_bkpt._hide(update_view)
            self.__breakpoints.remove(cur_bkpt)
            need_update = True
        return need_update

    def update_marker(self, views):
        for view in views:
            file = view.file_name()
            assert (file is not None)
            for bkpt in self.__breakpoints:
                if bkpt.file == file and not (getp().cursor_position == bkpt.line and getp().cursor == bkpt.file):
                    bkpt._show(view)
                            
    def update_view(self):
        super(DlvBreakpointView, self).update_view()
        if not self.is_open():
            return
        self.__breakpoints.sort(key=lambda b: (b.file, b.line))
        bkpts = []
        for bkpt in self.__breakpoints:
            self.add_line(bkpt._format())
            bkpts.append({"file": bkpt.file, "line": bkpt.line})
        self.view.settings().set('bkpts', bkpts)

    def find_breakpoint_by_idx(self, idx):
        if idx >= 0 and idx < len(self.__breakpoints):
            return self.__breakpoints[row]
        return None

    def find_breakpoint_by_id(self, id):
        assert (is_running())
        for bkpt in self.__breakpoints:
            if bkpt._is_error():
                continue
            if bkpt.id == id:
                return bkpt
        return None

    def find_breakpoint(self, file, line=None):
        for bkpt in self.__breakpoints:
            if bkpt.file == file and (line is None or line is not None and bkpt.line == line):
                return bkpt
        return None

    def load_data(self, data):
        assert (is_running())
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
            bkpt = self.find_breakpoint_by_id(cur_bkpt.id)
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
                if not bkpt._is_error():
                    if is_running():
                        requests.append({"cmd": get_const().CLEAR_BREAKPOINT_COMMAND, "parms": {"id": bkpt.id, "name": bkpt.name}})
                    bkpts_del.append(bkpt)
                else:
                    bkpts_error_del.append(bkpt)
            else:
                value = element['value']
                if not value.startswith('//') and not value.startswith('/*') and not value.endswith('*/'):
                    bkpt = DlvBreakpointType(element['file'], element['line'])
                    requests.append({"cmd": get_const().CREATE_BREAKPOINT_COMMAND, "parms": bkpt._as_parm})
                    bkpts_add.append(bkpt)
        if is_running():
            if len(requests) > 0:
                get_worker().do_batch(requests)
            if self.upgrade_breakpoints([], bkpts_error_del):
                self.update_view()
        else:
            if self.upgrade_breakpoints(bkpts_add, bkpts_del):
                self.update_view()
     
    def sync_breakpoints(self):
        requests = []
        for bkpt in self.__breakpoints:
            requests.append({"cmd": get_const().CREATE_BREAKPOINT_COMMAND, "parms": bkpt._as_parm})
        requests.append({"cmd": get_const().CONTINUE_COMMAND, "parms": None})
        if get_watch_view().is_watches_exist():
            requests.append({"cmd": get_const().WATCH_COMMAND, "parms": {"watches": get_watch_view().get_watches_as_parm()}})
        get_worker().do_batch(requests)

    def update_breakpoint_lines(self, view=None):
        got_changes = False
        for bkpt in self.__breakpoints:
            cur_view = view
            if view is None:
                cur_view = sublime.active_window().find_open_file(bkpt.file)
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
    def __init__(self, const, view):
        super(DlvStacktraceView, self).__init__(const.STACKTRACE_VIEW, const, view)
        self.__locations = []
        self.__cursor_position = 0

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
                find_view = sublime.active_window().find_open_file(loc.file)
                if find_view is None:
                    sublime.active_window().focus_group(0)
                sublime.active_window().open_file("%s:%d" % (loc.file, loc.line), sublime.ENCODED_POSITION)
        if loc is None:
            loc = self.__locations[self.__cursor_position]
        self.view.add_regions("dlv.location_pos", [self.view.line(self.view.text_point(self.__cursor_position, 0))], \
            "entity.name.class", "bookmark" if get_goroutine_view().is_current_goroutine_selected() and \
                        self.__cursor_position == 0 else "dot", sublime.HIDDEN)
        get_variable_view().load(loc._get_variables())
        if get_variable_view().is_open():
            get_variable_view().update_view()

    def load_data(self, data):
        if get_variable_view().is_open():
            get_variable_view().clear()
        self.__reset()
        load_variables = True
        for element in data['Locations']:
            loc = DlvLocationType()
            loc._update({"Location": element})
            self.__locations.append(loc)
            if load_variables:
                get_variable_view().load(loc._get_variables())
                load_variables = False

    def update_view(self):
        super(DlvStacktraceView, self).update_view()
        if not self.is_open():
            return
        for loc in self.__locations:
            self.add_line(loc._format(), '')
        self.select_location()

class DlvGoroutineView(DlvView):
    def __init__(self, const, view):
        super(DlvGoroutineView, self).__init__(const.GOROUTINE_VIEW, const, view)
        self.__goroutines = []
        self.__cursor_position = 0
        self.__current_goroutine_position = -1

    def __reset(self):
        self.__goroutines = []                
        self.__cursor_position = 0
        self.__current_goroutine_position = -1

    def is_current_goroutine_selected(self):
        return self.__cursor_position == self.__current_goroutine_position

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
                find_view = sublime.active_window().find_open_file(gr._current_file)
                if find_view is None:
                    sublime.active_window().focus_group(0)
                sublime.active_window().open_file("%s:%d" % (gr._current_file, gr._current_line), sublime.ENCODED_POSITION)
        if gr is None:
            gr = self.__goroutines[self.__cursor_position]
        self.view.add_regions("dlv.goroutine_pos", [self.view.line(self.view.text_point(self.__cursor_position, 0))], \
            "entity.name.class", "dot" if not self.is_current_goroutine_selected() else "bookmark", sublime.HIDDEN)
        get_worker().do(get_const().STACKTRACE_COMMAND, {"id": gr.id})

    def load_data(self, data, goroutine_id):
        self.__reset()
        idx = 0
        for element in data['Goroutines']:
            gr = DlvGoroutineType()
            gr._update({"Goroutine": element})
            self.__goroutines.append(gr)
            if gr.id == goroutine_id:
                self.__cursor_position = idx
                self.__current_goroutine_position = idx
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

    def _format(self, indent="", output="", line=0):
        self.__line = line
        line += 1
        if self._is_error() or not self._is_loaded() or not is_running():
            return ("%s = \"%s\"" % (self.name, self.__error_message if is_running() and self._is_error() else '<not available>'), line)
        icon = " "
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
                output, line = chld_var._format(indent, output, line)
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
    def __init__(self, name, const, view=None):
        super(DlvVariableView, self).__init__(name, const, view)
        self.__variables = []
        if view is not None and view.settings().has('watches'):
            assert (self.name == self.const.WATCH_VIEW)
            for element in view.settings().get('watches'):
                self.__variables.append(DlvtVariableType(name=element['exp']))

    def open(self, reset=False):
        super(DlvVariableView, self).open(reset)
        if self.is_open():
            self.set_syntax("Packages/SublimeDelve/SublimeDelve.tmLanguage")
            if reset and self.name == self.const.VARIABLE_VIEW:
                self.__variables = []                
            self.update_view()

    def clear(self, reset=False):
        if reset and self.name == self.const.VARIABLE_VIEW:
            self.__variables = []                
        super(DlvVariableView, self).clear(reset)

    def find_watch_by_uuid(self, uuid):
        assert (self.name == self.const.WATCH_VIEW)
        for var in self.__variables:
            if var._uuid == uuid:
                return var
        return None

    def load(self, variables):
        self.__variables = variables

    def load_data(self, data):
        assert (self.name == self.const.WATCH_VIEW)
        for element in data:
            if element['result']:
                var = self.find_watch_by_uuid(element['id'])
                if var is None:
                    continue
                cur_var = DlvtVariableType()
                var._update(element['eval'])
                var._reset_error_message()
            else:
                var = self.find_watch_by_uuid(element['parms']['id'])
                if var is None:
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
        watches = []
        for var in self.__variables:
            output, line = var._format(line=line)
            self.add_line(output, ' ')
            watches.append({"exp": var.name})
        self.view.settings().set('watches', watches)

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
        if self.is_open() and view.id() == self.get_view_id():
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

    def __edit_on_done(self, exp):
        assert (self.name == self.const.WATCH_VIEW)
        if exp.strip() == "":
            return
        var = DlvtVariableType(name=exp.strip())
        self.__variables.append(var)
        if is_running():
            get_worker().do(self.const.WATCH_COMMAND, {"watches": [{"id": var._uuid, "expr": var.name}] })
        else:
            self.update_view()

    def add_watch(self, view):
        assert (self.name == self.const.WATCH_VIEW)
        sublime.active_window().show_input_panel('Delve add watch =', '', self.__edit_on_done, None, None)

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
            response.append({"id": var._uuid, "expr": var.name})
        return response

    def is_watches_exist(self):
        assert (self.name == self.const.WATCH_VIEW)
        return (len(self.__variables) > 0)

def clear_position():
    last_cursor_view = getp().last_cursor_view
    if last_cursor_view is not None:
        region = last_cursor_view.get_regions("dlv.suspend_pos")
        if region is None or len(region) == 0:
            return
        assert (len(region) == 1)
        row, col = last_cursor_view.rowcol(region[0].a)
        bkpt = get_bkpt_view().find_breakpoint(last_cursor_view.file_name(), row + 1)
        last_cursor_view.erase_regions("dlv.suspend_pos")
        if bkpt is not None:
            bkpt._show(last_cursor_view)

def update_position(view=None):
    clear_position()
    getp().last_cursor_view = view
    project = getp() 
    cursor = project.cursor
    cursor_position = project.cursor_position
    if view is not None and cursor == view.file_name() and getp != 0:
        bkpt = get_bkpt_view().find_breakpoint(cursor, cursor_position)
        if  is_running():
            if bkpt is not None:
                bkpt._hide(view)
            view.add_regions("dlv.suspend_pos", [view.line(view.text_point(cursor_position - 1, 0))], \
                "entity.name.class", "bookmark", sublime.HIDDEN)

def sync_breakpoints():
    get_bkpt_view().sync_breakpoints()

class DlvToggleBreakpoint(sublime_plugin.TextCommand):
    def run(self, edit):
        elements = []
        update_view = self.view
        file = self.view.file_name()
        if get_bkpt_view().is_open() and self.view.id() == get_bkpt_view().get_view_id():
            row = self.view.rowcol(self.view.sel()[0].begin())[0]
            bkpt = get_bkpt_view().find_breakpoint_by_idx(row)
            if bkpt is not None:
                elements.append({"file": bkpt.file, "line": bkpt.line})
        elif file is not None: # code source file
            for sel in self.view.sel():
                line, col = self.view.rowcol(sel.a)
                value = ''.join(self.view.substr(self.view.line(self.view.text_point(line, 0))).split())
                if len(value) > 0:
                    elements.append({"file": file, "line": line + 1, "value": value})
        if len(elements) > 0:
            get_bkpt_view().toggle_breakpoint(elements)    

    def is_enabled(self):
        if is_plugin_enable():
            view = sublime.active_window().active_view()
            return view.file_name() is not None or get_bkpt_view().is_open() and view.id() == get_bkpt_view().get_view_id()
        return False

    def is_visible(self):
        if is_plugin_enable():
            view = sublime.active_window().active_view()
            return view.file_name() is not None or get_bkpt_view().is_open() and view.id() == get_bkpt_view().get_view_id()
        return False

class DlvClick(sublime_plugin.TextCommand):
    def run(self, edit):
        if not is_running():
            return
        if get_variable_view().is_open() and self.view.id() == get_variable_view().get_view_id():
            get_variable_view().expand_collapse_variable(self.view, toggle=True)
        elif get_watch_view().is_open() and self.view.id() == get_watch_view().get_view_id():
            get_watch_view().expand_collapse_variable(self.view, toggle=True)
        elif get_goroutine_view().is_open() and self.view.id() == get_goroutine_view().get_view_id():
            get_goroutine_view().select_goroutine(self.view)
        elif get_stacktrace_view().is_open() and self.view.id() == get_stacktrace_view().get_view_id():
            get_stacktrace_view().select_location(self.view)

    def is_enabled(self):
        return is_plugin_enable()

class DlvDoubleClick(sublime_plugin.TextCommand):
    def run(self, edit):
        if get_bkpt_view().is_open() and self.view.id() == get_bkpt_view().get_view_id():
            get_bkpt_view().select_breakpoint(self.view)
        if not is_running():
            return
        # if get_variable_view().is_open() and self.view.id() == get_variable_view().get_view_id():
        #     pass

    def is_enabled(self):
        return is_plugin_enable()

class DlvCollapseVariable(sublime_plugin.TextCommand):
    def run(self, edit):
        if self.view.id() == get_variable_view().get_view_id():
            get_variable_view().expand_collapse_variable(self.view, expand=False)
        else:
            get_watch_view().expand_collapse_variable(self.view, expand=False)

    def is_enabled(self):
        if not is_running():
            return False
        if get_variable_view().is_open() and self.view.id() == get_variable_view().get_view_id():
            return True
        elif get_watch_view().is_open() and self.view.id() == get_watch_view().get_view_id():
            return True
        return False

class DlvExpandVariable(sublime_plugin.TextCommand):
    def run(self, edit):
        if self.view.id() == get_variable_view().get_view_id():
            get_variable_view().expand_collapse_variable(self.view)
        else:
            get_watch_view().expand_collapse_variable(self.view)

    def is_enabled(self):
        if not is_running():
            return False
        if get_variable_view().is_open() and self.view.id() == get_variable_view().get_view_id():
            return True
        elif get_watch_view().is_open() and self.view.id() == get_watch_view().get_view_id():
            return True
        return False

class DlvAddWatch(sublime_plugin.TextCommand):
    def run(self, edit):
        get_watch_view().add_watch(self.view)

    def is_enabled(self):
        return is_plugin_enable()

    def is_visible(self):
        return is_plugin_enable()

class DlvRemoveWatch(sublime_plugin.TextCommand):
    def run(self, edit):
        get_watch_view().remove_watch(self.view)

    def is_enabled(self):
        if is_plugin_enable() and get_watch_view().is_open() and self.view.id() == get_watch_view().get_view_id():
            return True
        return False

    def is_visible(self):
        if is_plugin_enable() and get_watch_view().is_open() and self.view.id() == get_watch_view().get_view_id():
            return True
        return False

class DlvEventListener(sublime_plugin.EventListener):
    def on_query_context(self, view, key, operator, operand, match_all):
        if not is_plugin_enable():
            return None
        if key == "dlv_running":
            return is_running() == operand
        elif key == "dlv_input_view":
            return getp().check_input_view(view)
        elif key.startswith("dlv_"):
            v = get_variable_view() if (get_variable_view().get_view_id() == view.id()) else None
            if v is None:
                v = get_watch_view() if (get_watch_view().get_view_id() == view.id()) else None
            return None if v is None else True == operand
        return None

    def on_activated(self, view):
        if is_plugin_enable() and view.file_name() is not None:
            get_bkpt_view().update_marker([view])
            if is_running():
                update_position(view)

    def on_load(self, view):
        if is_plugin_enable() and view.file_name() is not None:
            get_bkpt_view().update_marker([view])
            if is_running():
                update_position(view)

    def on_modified(self, view):
        if is_plugin_enable() and get_bkpt_view().is_open() and not is_running():
            if get_bkpt_view().update_breakpoint_lines(view):
                get_bkpt_view().update_view()

    def on_pre_close(self, view):
        if is_plugin_enable() and not is_running():
            if get_bkpt_view().update_breakpoint_lines(view):
                get_bkpt_view().update_view()

    def on_close(self, view):
        if not is_plugin_enable():
            return
        for v in get_views():
            if v.is_open() and view.id() == v.get_view_id():
                v.was_closed()
                break
        if get_console_view().is_open() and view.id() == get_console_view().get_view_id():
            get_console_view().was_closed()
        get_bkpt_view().hide_view_breakpoints(view)

def set_status_message(message):
    sublime.status_message(message)

def dlv_output(pipe, cmd_session=None):
    started_session = False
    # reaesc = re.compile(r'\x1b[^m]*m')
    reaesc = re.compile(r'\x1b\[[\d;]*m')

    client_proc = get_client_proc()
    server_proc = get_server_proc()
    if client_proc is not None and pipe == client_proc.stdout:
        sublime.set_timeout(show_input, 0)
        get_logger().debug("Input field is ready")
        sublime.set_timeout(sync_breakpoints, 0)
        sublime.set_timeout(set_status_message("Delve session started"), 0)

    while True:
        try:
            line = pipe.readline()
            if len(line) == 0:
                if is_local_mode() and server_proc is not None:
                    if pipe in [server_proc.stdout, server_proc.stderr]:
                        get_logger().error("Broken %s pipe of the Delve server" % \
                            ("stdout" if pipe == server_proc.stdout else "stderr"))
                        break
                if client_proc is not None and client_proc.stdout is not None:
                    get_logger().error("Broken %s pipe of the Delve session" % \
                        ("stdout" if pipe == client_proc.stdout else "stderr"))
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
            if client_proc is not None:
                if pipe == client_proc.stdout:
                    get_session_view().add_line(line)
                    get_logger().info("Session stdout: " + line)
                elif pipe == client_proc.stderr:
                    get_session_view().add_line(line)
                    get_logger().error("Session stderr: " + line)
            if server_proc is not None:
                if pipe == server_proc.stdout:
                    get_console_view().add_line(line)
                    get_logger().info("Server stdout: " + line)
                    if not started_session:
                        get_logger().debug("Delve server is working, try to start Delve Session")
                        lock = threading.RLock()
                        lock.acquire()
                        sublime.set_timeout(load_session_subprocess(cmd_session), 0)
                        started_session = True
                        lock.release()
                elif pipe == server_proc.stderr:
                    get_console_view().add_line(line)
                    get_logger().error("Server stderr: " + line)
        except:
            traceback.print_exc(file=(sys.stdout if get_logger().get_file() == get_const().STDOUT else open(get_logger().get_file(),"a")))
            get_logger().error("Exception thrown, details in file: %s" % get_logger().get_file())

    if client_proc is not None and pipe == client_proc.stdout:
        message = "Delve session closed"
        sublime.set_timeout(set_status_message(message), 0)
        get_logger().info(message)
    if server_proc is not None and pipe == server_proc.stdout:
        get_logger().info("Delve server closed")
        sublime.set_timeout(terminate_session, 0)
    if (not is_local_mode() and client_proc is not None and pipe == client_proc.stdout) or \
                (is_local_mode() and server_proc is not None and pipe == server_proc.stdout):
        sublime.set_timeout(cleanup_session, 0)

def terminate_session(send_sigint=False):
    if is_running():
        try:
            if send_sigint:
                get_client_proc().send_signal(signal.SIGINT)
            else:
                get_client_proc().terminate()
            if is_server_running():
                get_server_proc().send_signal(signal.SIGINT)
        except:
            traceback.print_exc(file=(sys.stdout if get_logger().get_file() == get_const().STDOUT else open(get_logger().get_file(),"a")))
            get_logger().error("Exception thrown, details in file: %s" % get_logger().get_file())
            return False
    return True

def cleanup_session():
    v = get_console_view()
    if v.is_open():
        if v.is_close_at_stop():
            v.close()
        else:
            v.clear(True)
    for v in get_views():
        if v.is_open():
            if v.is_close_at_stop():
                v.close()
            else:
                v.clear(True)
    getp().panel_on_stop()
    get_logger().debug("Closed required debugging views")
    if get_const().is_project_executable():
        get_const().clear_project_executable()
        get_logger().debug("Cleared project executable settings")
    get_worker().stop()
    get_logger().stop()
    clear_position()

def terminate_server():
    if is_server_running():
        try:
            get_logger().debug('Try to terminate Delve server')
            get_server_proc().send_signal(signal.SIGINT)
        except:
            traceback.print_exc(file=(sys.stdout if get_logger().get_file() == get_const().STDOUT else open(get_logger().get_file(),"a")))
            get_logger().error("Exception thrown, details in file: %s" % get_logger().get_file())
            get_server_proc().kill()
            get_logger().error("Delve server killed after timeout")
    v = get_console_view()
    if v.is_open():
        if v.is_close_at_stop():
            v.close()
            get_logger().debug("Closed console view")
        else:
            v.clear(True)

    if get_console_view().is_open():
        get_console_view().close()

def load_session_subprocess(cmd_session):
    proc = None
    try:
        message = "Delve session started with command: %s" % " ".join(cmd_session)
        get_logger().info(message)
        get_session_view().add_line(message)
        proc = open_client_subprocess(cmd_session)
    except:
        traceback.print_exc(file=(sys.stdout if get_logger().get_file() == get_const().STDOUT else open(get_logger().get_file(),"a")))
        message = "Exception thrown, details in file: %s" % get_logger().get_file()
        get_logger().error(message)
        set_status_message(message)
        if is_local_mode():
            terminate_server()
        else:
            cleanup_session()
        return             
    getp().reset_cursor()
    t = threading.Thread(target=dlv_output, args=(proc.stdout,))
    t.start()
    t = threading.Thread(target=dlv_output, args=(proc.stderr,))
    t.start()

class DlvStart(sublime_plugin.WindowCommand):
    def create_cmd(self):
        value = "dlv"
        cmd_server = []
        cmd_session = []
        cmd_server.append(value)
        cmd_session.append(value)
        if is_local_mode():
            cmd_server.append(get_const().MODE)
        cmd_session.append("connect")
        cmd_server.append("--headless")
        cmd_server.append("--accept-multiclient")
        cmd_server.append("--api-version=2")
        if get_const().LOG:
            cmd_server.append("--log")
        value = "%s:%d" % (get_const().HOST, get_const().PORT)
        cmd_server.append("--listen=%s" % value)
        cmd_session.append(value)
        if get_const().ARGS != "":
            cmd_server.append("--")
            cmd_server.append(get_const().ARGS)
        return (cmd_session, cmd_server)

    def run(self):
        if get_const().is_project_executable():
            get_const().clear_project_executable()
            get_logger().debug("Cleared project executable settings")
        exec_choices = get_const().get_project_executables()
        if exec_choices is None:
            self.launch()
            return

        def on_choose(index):
            if index == -1:
                # User cancelled the panel, abort launch
                return
            exec_name = list(exec_choices)[index]
            get_const().set_project_executable(exec_name)
            self.launch()

        self.window.show_quick_panel(list(exec_choices), on_choose)

    def launch(self):
        global dlv_panel_window

        get_logger().start(get_const().DEBUG_FILE)
        if get_const().is_project_executable():
            get_logger().debug("Set project executable settings: %s" % get_const().get_project_executable_name())

        active_view = None
        window = sublime.active_window()
        if window is not None:
            active_view = window.active_view()

        getp().panel_on_start()

        if is_local_mode():
            if get_console_view().is_closed():
                if get_console_view().is_open_at_start():
                    get_console_view().open(True)
            else:
                if get_console_view().is_open_at_start():
                    get_console_view().clear(True)
                else:
                    get_console_view().close()
            if get_console_view().is_open():
                get_logger().debug("Console view is ready")

        for v in get_views():
            if v.is_closed():
                if v.is_open_at_start():
                    v.open(True)
            else:
                if v.is_open_at_start():
                    v.clear(True)
                else:
                    v.close()
        get_logger().debug("Debugging views is ready")

        cmd_session, cmd_server = self.create_cmd()        
        if is_local_mode():
            value = get_const().CWD
            cwd = None
            if value != "":
                cwd = value
            else:
                file_name = None
                if window is not None:
                    file_name = window.project_file_name()
                    if file_name is None and active_view is not None:
                        file_name = active_view.file_name()
                if file_name is not None:
                    cwd = os.path.dirname(file_name)
            set_status_message("Starts Delve server, wait...")
            message = "Delve server started with command: %s" % " ".join(cmd_server)
            get_logger().info(message)
            get_logger().debug("In directory: %s" % cwd)            
            get_console_view().add_line(message)
            proc = None
            try:
                proc = open_server_subprocess(cmd_server, cwd)
            except:
                traceback.print_exc(file=(sys.stdout if get_logger().get_file() == get_const().STDOUT else open(get_logger().get_file(),"a")))
                message = "Exception thrown, details in file: %s" % get_logger().get_file()
                get_logger().error(message)
                set_status_message(message)
                terminate_server()
                cleanup_session()
                return             
            t = threading.Thread(target=dlv_output, args=(proc.stdout, cmd_session))
            t.start()
            t = threading.Thread(target=dlv_output, args=(proc.stderr,))
            t.start()
        else:
            load_session_subprocess(cmd_session)

    def is_enabled(self):
        return is_plugin_enable() and not is_running()

    def is_visible(self):
        return is_plugin_enable() and not is_running()

class DlvResume(sublime_plugin.WindowCommand):
    def run(self):
        get_worker().do(get_const().CONTINUE_COMMAND)
    
    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvNext(sublime_plugin.WindowCommand):
    def run(self):
        get_worker().do(get_const().NEXT_COMMAND)
    
    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvStepIn(sublime_plugin.WindowCommand):
    def run(self):
        get_worker().do(get_const().STEP_COMMAND)
    
    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvStepOut(sublime_plugin.WindowCommand):
    def run(self):
        get_worker().do(get_const().STEPOUT_COMMAND)
    
    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvRestart(sublime_plugin.WindowCommand):
    def run(self):
        requests = []
        requests.append({"cmd": get_const().RESTART_COMMAND, "parms": None})
        requests.append({"cmd": get_const().CONTINUE_COMMAND, "parms": None})
        if get_watch_view().is_watches_exist():
            requests.append({"cmd": get_const().WATCH_COMMAND, "parms": {"watches": get_watch_view().get_watches_as_parm()}})
        get_worker().do_batch(requests)
    
    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvStop(sublime_plugin.WindowCommand):
    def run(self):
        terminate_session(is_local_mode())

    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvInput(sublime_plugin.WindowCommand):
    def run(self):
        show_input()

    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvPrevCmd(sublime_plugin.TextCommand):
    def run(self, edit):
        project = getp()
        if project.command_history_pos > 0:
            project.command_history_pos -= 1
        if project.command_history_pos < len(project.command_history):
            set_input(edit, project.command_history[project.command_history_pos])

    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvNextCmd(sublime_plugin.TextCommand):
    def run(self, edit):
        project = getp()
        if project.command_history_pos < len(project.command_history):
            project.command_history_pos += 1
        if project.command_history_pos < len(project.command_history):
            set_input(edit, project.command_history[project.command_history_pos])
        else:
            set_input(edit, "")

    def is_enabled(self):
        return is_plugin_enable() and is_running()

    def is_visible(self):
        return is_plugin_enable() and is_running()

class DlvOpenSessionView(sublime_plugin.WindowCommand):
    def run(self):
        if get_session_view().is_closed():
            get_session_view().open()

    def is_enabled(self):
        return is_plugin_enable() and is_running() and get_session_view().is_closed()

    def is_visible(self):
        return is_plugin_enable() and is_running() and get_session_view().is_closed()

class DlvOpenConsoleView(sublime_plugin.WindowCommand):
    def run(self):
        if get_console_view().is_closed():
            get_console_view().open()

    def is_enabled(self):
        return is_plugin_enable() and is_local_mode() and is_server_running() and get_console_view().is_closed()

    def is_visible(self):
        return is_plugin_enable() and is_local_mode() and is_server_running() and get_console_view().is_closed()

class DlvOpenBreakpointView(sublime_plugin.WindowCommand):
    def run(self):
        if get_bkpt_view().is_closed():
            get_bkpt_view().open()

    def is_enabled(self):
        return is_plugin_enable() and get_bkpt_view().is_closed()

    def is_visible(self):
        return is_plugin_enable() and get_bkpt_view().is_closed()

class DlvOpenVariableView(sublime_plugin.WindowCommand):
    def run(self):
        if get_variable_view().is_closed():
            get_variable_view().open()

    def is_enabled(self):
        return is_plugin_enable() and is_running() and get_variable_view().is_closed()

    def is_visible(self):
        return is_plugin_enable() and is_running() and get_variable_view().is_closed()

class DlvOpenWatchView(sublime_plugin.WindowCommand):
    def run(self):
        if get_watch_view().is_closed():
            get_watch_view().open()

    def is_enabled(self):
        return is_plugin_enable() and get_watch_view().is_closed()

    def is_visible(self):
        return is_plugin_enable() and get_watch_view().is_closed()

class DlvOpenStacktraceView(sublime_plugin.WindowCommand):
    def run(self):
        if get_stacktrace_view().is_closed():
            get_stacktrace_view().open()

    def is_enabled(self):
        return is_plugin_enable() and is_running() and get_stacktrace_view().is_closed()

    def is_visible(self):
        return is_plugin_enable() and is_running() and get_stacktrace_view().is_closed()

class DlvOpenGoroutineView(sublime_plugin.WindowCommand):
    def run(self):
        if get_goroutine_view().is_closed():
            get_goroutine_view().open()

    def is_enabled(self):
        return is_plugin_enable() and is_running() and get_goroutine_view().is_closed()

    def is_visible(self):
        return is_plugin_enable() and is_running() and get_goroutine_view().is_closed()

class DlvEnable(sublime_plugin.WindowCommand):
    def run(self):
        if is_project_file_exists():
            window = sublime.active_window()
            data = window.project_data()
            if 'settings' not in data:
                data['settings'] = {}
            data['settings']['delve_enable'] = True
            window.set_project_data(data)
        else:
            sublime.error_message("An open project is required")

    def is_enabled(self):
        return not is_plugin_enable()

    def is_visible(self):
        return not is_plugin_enable()

class DlvDisable(sublime_plugin.WindowCommand):
    def run(self):
        window = sublime.active_window()
        assert (window.project_file_name() is not None and 'settings' in window.project_data())
        data = window.project_data()
        data['settings']['delve_enable'] = False
        window.set_project_data(data)
        key = window.id()
        if key in dlv_project:
            dlv_project.pop(key)
        
    def is_enabled(self):
        return is_plugin_enable() and not is_running()

    def is_visible(self):
        return is_plugin_enable() and not is_running()

class DlvTest(sublime_plugin.WindowCommand):
    def is_enabled(self):
        return is_plugin_enable()

    def is_visible(self):
        return is_plugin_enable()

    def run(self):
        print(sublime.active_window().extract_variables())
        print(sublime.active_window().project_data())
        print(sublime.active_window().project_file_name())
        print(sublime.active_window().id())

        # bkpt = DlvBreakpointType("/home/dmitry/Projects/gotest/hello.go", 17)
        # dlv_worker.do('createbreakpoint', bkpt._as_parm)
        # global dlv_const
        # global dlv_logger
        # global dlv_connect

        # response = dlv_connect.RPCServer.State({})

        # breakpointin1 = DlvBreakpointType("/home/dmitry/Projects/gotest/hello.go", 16)
        # breakpointin2 = DlvBreakpointType("/home/dmitry/Projects/gotest/hello.go", 17)

        # try:
        #     dlv_connect._open(dlv_const.HOST, dlv_const.PORT)
        #     dlv_connect._prepare_batch()
        #     dlv_connect.RPCServer.CreateBreakpoint(breakpointin1._as_parm)
        #     dlv_connect.RPCServer.CreateBreakpoint(breakpointin2._as_parm)
        #     result = dlv_connect()
        #     # print(breakpointin.name)
        #     # result = dlv_connect.RPCServer.CreateBreakpoint(breakpointin._as_parm)
        #     # result = dlv_connect.RPCServer.CreateBreakpoint({"Breakpoint":{"name":"bp1","file":"/home/dmitry/Projects/gotest/hello.go","line":16}})
        #     # breakpointin._update(result)
        #     # breakpointout = DlvBreakpointType(**result['Breakpoint'])
        #     print(result)
        # finally:
        #     dlv_connect._close()

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
