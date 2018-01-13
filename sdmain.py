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

from SublimeDelve.sdconst import DlvConst
from SublimeDelve.sdlogger import DlvLogger
from SublimeDelve.sdworker import DlvWorker

from SublimeDelve.sdview import DlvView
from SublimeDelve.sdobjecttype import *

dlv_project = {}

class DlvProject(object):
    def __init__(self, key):
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

        log = DlvLogger(key, self.const)
        self.logger = log
        self.worker = DlvWorker(self.const, log, worker_callback)
        
        self.session_view = DlvView(self.const.SESSION_VIEW, "Delve Session", self.const)
        self.console_view = DlvView(self.const.CONSOLE_VIEW, "Delve Console", self.const)
        self.variable_view = DlvVariableView(self.const)
        self.bkpt_view = DlvBreakpointView(self.const)

    def get_views(self):
       return [self.session_view, self.variable_view, self.bkpt_view]

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

def is_plugin_enable():
    window = sublime.active_window()
    if window.project_file_name() is not None and 'settings' in window.project_data():
        settings = window.project_data()['settings']
        if 'delve_enable' in settings and settings['delve_enable']:
            key = __get_delve_key()
            if not key in dlv_project:
                dlv_project[key] = DlvProject(key)
            return True
    return False

def __get_delve_key():
    settings = sublime.active_window().project_data()['settings']
    return settings['delve_key'] if 'delve_key' in settings else 'changeme'

def getp():
    return dlv_project[__get_delve_key()]

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

def get_variable_view():
    return getp().variable_view

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
    project.input_view = sublime.active_window().show_input_panel("Delve", "", input_on_done, input_on_change, input_on_cancel)

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
    proc = get_client_proc()
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
    get_worker().do(get_const().STATE_COMMAND)

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
            if 'errorcode' in response:
                error_code = response['errorcode']
                error_message = response['errormessage']
        if response['cmd'] == get_const().CREATE_BREAKPOINT_COMMAND:
            new_bkpt = DlvBreakpointType()
            if result:
                new_bkpt._update(response['response'])
                find_bkpt = get_bkpt_view().find_breakpoint(new_bkpt.file, new_bkpt.line)
                if find_bkpt is not None:
                    find_bkpt._update(response['response'])
                else:
                    bkpts_add.append(new_bkpt)
            else:
                new_bkpt._update(response['parms'])
                bkpts_del.append(new_bkpt)
            if get_bkpt_view() not in update_views:
                update_views.append(get_bkpt_view())
        elif response['cmd'] == get_const().CLEAR_BREAKPOINT_COMMAND:
            new_bkpt = DlvBreakpointType()
            if result:
                new_bkpt._update(response['response'])
                bkpts_del.append(new_bkpt)
                if get_bkpt_view() not in update_views:
                    update_views.append(get_bkpt_view())
        elif response['cmd'] == get_const().VARIABLE_COMMAND:
            if result:
                get_variable_view().load_data(response['response'])
                if get_variable_view() not in update_views:
                    update_views.append(get_variable_view())
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
    def __init__(self, file=None, line=None, name = None, **kwargs):
        super(DlvBreakpointType, self).__init__("Breakpoint", **kwargs)
        self.__file = file
        self.__line = line
        self.__original_line = line
        self.__name = name
        self.__showed = False

    def __getattr__(self, attr):
        if attr == "file" and self.__file is not None:
            return self.__file
        if attr == "line" and self.__line is not None:
            return self.__line
        if attr == "name" and self.__name is not None:
            return self.__name
        return super(DlvBreakpointType, self).__getattr__(attr)

    @property
    def _as_parm(self):
        response = super(DlvBreakpointType, self)._as_parm
        if self.__file is not None:
            response[self._object_name]['file'] = self.__file
        if self.__line is not None:
            response[self._object_name]['line'] = self.__line
        if self.__name is not None:
            response[self._object_name]['name'] = self.__name
        return response

    @property
    def _key(self):
        if self.__original_line is None:
            self.__original_line = self.line
        return "dlv.bkpt%s" % self.__original_line

    def _add(self):
        get_worker().do(get_const().CREATE_BREAKPOINT_COMMAND, self._as_parm)

    def _remove(self):
        get_worker().do(get_const().CLEAR_BREAKPOINT_COMMAND, {"id": self.id, "name": self.name})

    def _update_line(self, line):
        assert (self.__original_line is not None)
        self.__line = line

    def _show(self, view):
        if not self.__showed:
            view.add_regions(self._key, [view.line(view.text_point(self.line - 1, 0))], "keyword.dlv", "circle", sublime.HIDDEN)
            self.__showed = True

    def _hide(self, view):
        if self.__showed:
            view.erase_regions(self._key)
            self.__showed = False

    def _was_hided(self):
        self.__showed = False

    def _format(self):
        if self.__name is not None:
            return "%s %s:%d" % (self.name, self.file, self.line)
        else:
            return "%s:%d" % (self.file, self.line)

class DlvStateType(DlvObjectType):
    def __init__(self, **kwargs):
        super(DlvStateType, self).__init__("State", **kwargs)

    def _get_thread(self, name=None):
        thread = DlvtThreadType()
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

class DlvtThreadType(DlvObjectType):
    def __init__(self, **kwargs):
        super(DlvtThreadType, self).__init__("Thread", **kwargs)

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

class DlvBreakpointView(DlvView):
    def __init__(self, const):
        super(DlvBreakpointView, self).__init__(const.BREAKPOINTS_VIEW, "Delve Breakpoints", const, scroll=False)
        self.breakpoints = []

    def open(self, reset=False):
        super(DlvBreakpointView, self).open(reset)
        if self.is_open():
            self.update_breakpoint_lines()
            self.update_view()

    def hide_view_breakpoints(self, view):
        for bkpt in self.breakpoints:
            if bkpt.file == view.file_name():
                bkpt._was_hided()

    def upgrade_breakpoints(self, bkpts_add=[], bkpts_del=[]):
        need_update = False
        for bkpt in bkpts_add:
            cur_bkpt = self.find_breakpoint(bkpt.file, bkpt.line)
            assert (cur_bkpt is None)
            cur_bkpt = bkpt
            self.breakpoints.append(cur_bkpt)
            update_view = sublime.active_window().find_open_file(cur_bkpt.file)
            cur_bkpt._show(update_view)
            need_update = True
        for bkpt in bkpts_del:
            cur_bkpt = self.find_breakpoint(bkpt.file, bkpt.line)
            assert (cur_bkpt is not None)
            update_view = sublime.active_window().find_open_file(cur_bkpt.file)
            cur_bkpt._hide(update_view)
            self.breakpoints.remove(cur_bkpt)
            need_update = True
        return need_update

    def update_marker(self, views):
        for view in views:
            file = view.file_name()
            assert (file is not None)
            for bkpt in self.breakpoints:
                if bkpt.file == file and not (getp().cursor_position == bkpt.line and getp().cursor == bkpt.file):
                    bkpt._show(view)
                            
    def update_view(self):
        super(DlvBreakpointView, self).update_view()
        if not self.is_open():
            return
        self.breakpoints.sort(key=lambda b: (b.file, b.line))
        for bkpt in self.breakpoints:
            self.add_line(bkpt._format())

    def find_breakpoint(self, file, line=None):
        for bkpt in self.breakpoints:
            if bkpt.file == file and (line is None or line is not None and bkpt.line == line):
                return bkpt
        return None

    def toggle_breakpoint(self, elements):
        assert (len(elements) > 0)
        requests = []
        bkpts_add = [] 
        bkpts_del = []
        for element in elements:
            bkpt = self.find_breakpoint(element['file'], element['line'])
            if bkpt is not None:
                if is_running():
                    requests.append({"cmd": get_const().CLEAR_BREAKPOINT_COMMAND, "parms": {"id": bkpt.id, "name": bkpt.name}})
                bkpts_del.append(bkpt)
            else:
                value = element['value']
                if not value.startswith('//') and not value.startswith('/*') and not value.endswith('*/'):
                    bkpt = DlvBreakpointType(element['file'], element['line'])
                    requests.append({"cmd": get_const().CREATE_BREAKPOINT_COMMAND, "parms": bkpt._as_parm})
                    bkpts_add.append(bkpt)
        if is_running():
            if len(requests) > 0:
                get_worker().do_batch(requests)
        else:
            if (self.upgrade_breakpoints(bkpts_add, bkpts_del)):
                self.update_view()
     
    def sync_breakpoints(self):
        requests = []
        for bkpt in self.breakpoints:
            requests.append({"cmd": get_const().CREATE_BREAKPOINT_COMMAND, "parms": bkpt._as_parm})
        requests.append({"cmd": get_const().CONTINUE_COMMAND, "parms": None})
        get_worker().do_batch(requests)

    def update_breakpoint_lines(self, view=None):
        got_changes = False
        for bkpt in get_bkpt_view().breakpoints:
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

class DlvtVariableType(DlvObjectType):
    def __init__(self, parent=None, name=None, **kwargs):
        super(DlvtVariableType, self).__init__("Variable", **kwargs)
        self.__parent = parent
        self.__name = name
        self.__children = []
        self.__expanded = False
        self.__line = 0
        self.__map_element = False

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

    def _set_map_key(self, key):
        assert (self.__name is None)
        self.__name = key
        self.__map_element = True

    def _format(self, indent="", output="", line=0):
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

        self.__line = line
        line += 1
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
    def __init__(self, const):
        super(DlvVariableView, self).__init__(const.VARIABLE_VIEW, "Delve Variables", const, scroll=False)
        self.variables = []

    def open(self, reset=False):
        super(DlvVariableView, self).open(reset)
        if self.is_open():
            self.set_syntax("Packages/SublimeDelve/Go.tmLanguage")
            if reset:
                self.variables = []                
            self.update_view()

    def clear(self, reset=False):
        if reset:
            self.variables = []                
        super(DlvVariableView, self).clear(reset)

    def load_data(self, data):
        self.variables = []
        for element in data['Variables']:
            var = DlvtVariableType()
            var._update({"Variable": element})
            self.variables.append(var)
        for element in data['Args']:
            var = DlvtVariableType()
            var._update({"Variable": element})
            self.variables.append(var)

    def update_view(self):
        super(DlvVariableView, self).update_view()
        if not self.is_open():
            return
        output = ""
        line = 0
        for var in self.variables:
            output, line = var._format(line=line)
            self.add_line(output, ' ')

    def get_variable_at_line(self, line, var_list=None):
        if var_list is None:
            var_list = self.variables
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
            if var is not None and var._has_children():
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

def clear_position():
    last_cursor_view = getp().last_cursor_view
    if last_cursor_view is not None:
        region = last_cursor_view.get_regions("dlv.pos")
        if region is None or len(region) == 0:
            return
        assert (len(region) == 1)
        row, col = last_cursor_view.rowcol(region[0].a)
        bkpt = get_bkpt_view().find_breakpoint(last_cursor_view.file_name(), row + 1)
        last_cursor_view.erase_regions("dlv.pos")
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
            view.add_regions("dlv.pos", [view.line(view.text_point(cursor_position - 1, 0))], \
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
            if row < len(get_bkpt_view().breakpoints):
                bkpt = get_bkpt_view().breakpoints[row]
                elements.append({"file": bkpt.file, "line": bkpt.line})
        elif file is not None: # code source file
            for sel in self.view.sel():
                line, col = self.view.rowcol(sel.a)
                value = ''.join(self.view.substr(self.view.line(self.view.text_point(line, 0))).split())
                if len(value) > 0:
                    elements.append({"file": file, "line": line + 1, "value": value})
        if len(elements) > 0:
            get_bkpt_view().toggle_breakpoint(elements)    



#                 if is_running():
#                     bkpt._remove()
#                 else:
#                     get_bkpt_view().breakpoints.pop(row)
#                     get_bkpt_view().update_view()
#                 update_view = sublime.active_window().find_open_file(bkpt.file)



        

#         update_view = self.view
#         file = update_view.file_name()
#         if get_bkpt_view().is_open() and self.view.id() == dlv_bkpt_view.get_view_id():
#             row = self.view.rowcol(self.view.sel()[0].begin())[0]
#             if row < len(dlv_bkpt_view.breakpoints):
#                 bkpt = dlv_bkpt_view.breakpoints[row]
#                 if is_running():
#                     bkpt._remove()
#                 else:
#                     dlv_bkpt_view.breakpoints.pop(row)
#                     dlv_bkpt_view.update_view()
#                 update_view = sublime.active_window().find_open_file(bkpt.file)
#         elif file is not None: # not dlv_bkpt_view, where file is None
#             files_lines = []
#             for sel in self.view.sel():
#                 line, col = self.view.rowcol(sel.a)
#                 value = ''.join(self.view.substr(self.view.line(self.view.text_point(line, 0))).split())
# # TO-DO Error
#                 if len(value) > 0 and not value.startswith('//') and not value.startswith('/*') and not value.endswith('*/'):
#                     files_lines.append({"file": file, "line": line + 1})
#             if len(files_lines) > 0:
#                 dlv_bkpt_view.toggle_breakpoint(files_lines)
#         if not is_running() and update_view is not None:
#             dlv_bkpt_view.update_marker([update_view], bkpts_append, bkpts_remove)

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
            print('test')
            get_variable_view().expand_collapse_variable(self.view, toggle=True)

    def is_enabled(self):
        return is_plugin_enable() and is_running()

class DlvDoubleClick(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view.run_command("dlv_edit_variable")

    def is_enabled(self):
        return is_plugin_enable() and is_running() and get_variable_view().is_open() and self.view.id() == get_variable_view().get_view_id()

class DlvCollapseVariable(sublime_plugin.TextCommand):
    def run(self, edit):
        get_variable_view().expand_collapse_variable(self.view, expand=False)

    def is_enabled(self):
        if not is_running():
            return False
        if get_variable_view().is_open() and self.view.id() == get_variable_view().get_view_id():
            return True
        return False

class DlvExpandVariable(sublime_plugin.TextCommand):
    def run(self, edit):
        get_variable_view().expand_collapse_variable(self.view)

    def is_enabled(self):
        if not is_running():
            return False
        if get_variable_view().is_open() and self.view.id() == get_variable_view().get_view_id():
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
            v = get_variable_view()
            # if key.startswith("gdb_register_view"):
            #     v = gdb_register_view
            # elif key.startswith("gdb_disassembly_view"):
            #     v = gdb_disassembly_view
            if key.endswith("open"):
                return v.is_open() == operand
            else:
                if v.is_closed():
                    return False == operand
                return (view.id() == v.get_view_id()) == operand
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
    for view in get_views():
        if view.is_open():
            view.close()
    if is_local_mode() and get_console_view().is_open():
        get_console_view().close()
    getp().panel_on_stop()
    get_logger().debug("Closed debugging views")
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
    if get_console_view().is_open():
        get_console_view().close()
        get_logger().debug("Closed console view")

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

class DlvTest(sublime_plugin.WindowCommand):
    def is_enabled(self):
        return is_plugin_enable()

    def is_visible(self):
        return is_plugin_enable()

    def run(self):
        print(sublime.active_window().project_data())
        print(sublime.active_window().project_file_name())

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
