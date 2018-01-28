import sublime
import os

class DlvConst(object):
    def __init__(self, window):
        self.__window = window
        self.__settings_file_name = "GoDebug.sublime-settings"
        self.__bkpt_settings_file_name = "GoDebug.breakpoint-settings"
        self.__watch_settings_file_name = "GoDebug.watch-settings"
        self.__project_settings_prefix = "godebug"
        self.__panel_group_suffix = "group"
        self.__open_at_start_suffix = "open_at_start"
        self.__close_at_stop_suffix = "close_at_stop"
        self.__title_suffix = "title"
        self.__breakpoint_suffix = "breakpoints"
        self.__watch_suffix = "watches"
        self.__project_exec_suffix = "executables"
        self.__project_exec_settings = {}
        self.__project_exec_name = None
        self.__view_switch = {
            self.STACKTRACE_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_stacktrace_group,     
                self.__open_at_start_suffix: self.__get_stacktrace_open_at_start,     
                self.__close_at_stop_suffix: self.__get_stacktrace_close_at_stop,
                self.__title_suffix: self.__get_stacktrace_title
            },
            self.GOROUTINE_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_goroutine_group,     
                self.__open_at_start_suffix: self.__get_goroutine_open_at_start,   
                self.__close_at_stop_suffix: self.__get_goroutine_close_at_stop,
                self.__title_suffix: self.__get_goroutine_title
            },
            self.VARIABLE_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_variable_group,     
                self.__open_at_start_suffix: self.__get_variable_open_at_start,   
                self.__close_at_stop_suffix: self.__get_variable_close_at_stop,
                self.__title_suffix: self.__get_variable_title
            },
            self.WATCH_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_watch_group,     
                self.__open_at_start_suffix: self.__get_watch_open_at_start,   
                self.__close_at_stop_suffix: self.__get_watch_close_at_stop,
                self.__title_suffix: self.__get_watch_title
            },
            self.SESSION_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_session_group,     
                self.__open_at_start_suffix: self.__get_session_open_at_start,   
                self.__close_at_stop_suffix: self.__get_session_close_at_stop,
                self.__title_suffix: self.__get_session_title
            },
            self.CONSOLE_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_console_group,     
                self.__open_at_start_suffix: self.__get_console_open_at_start,    
                self.__close_at_stop_suffix: self.__get_console_close_at_stop,
                self.__title_suffix: self.__get_console_title
            },
            self.BREAKPOINT_VIEW: 
            { 
                self.__panel_group_suffix: self.__get_breakpoint_group, 
                self.__open_at_start_suffix: self.__get_breakpoint_open_at_start, 
                self.__close_at_stop_suffix: self.__get_breakpoint_close_at_stop,
                self.__title_suffix: self.__get_breakpoint_title
            }, 
        }

    def __get_settings(self, key, default):
        if default is None:
            raise Exception("Default key %s value cannot be None" % key)
        if key in self.__project_exec_settings:
            return self.__project_exec_settings[key]
        view = self.__window.active_view()
        if view is not None:
            settings = view.settings()
            if settings.has("%s_%s" % (self.__project_settings_prefix, key)):
                return settings.get("%s_%s" % (self.__project_settings_prefix, key))
        value = sublime.load_settings(self.__settings_file_name).get(key, default)
        if value is None:
            value = default
        return value

    def get_view_setting(self, code, key):
        return (self.__view_switch[code])[key]()

    def set_project_executable(self, name):
        view = self.__window.active_view()
        if view is not None:
            settings = view.settings()
            exec_choices = settings.get("%s_%s" % (self.__project_settings_prefix, self.__project_exec_suffix))
            if exec_choices is None or type(exec_choices) != dict or not name in exec_choices:
                raise Exception("Project executable settings %s not found" % name)
            self.__project_exec_settings = exec_choices[name]
            self.__project_exec_name = name
    
    def is_project_executable(self):
        return self.__project_exec_name is not None

    def get_project_executable_name(self):
        return self.__project_exec_name

    def clear_project_executable(self):
        self.__project_exec_settings = {}
        self.__project_exec_name = None
    
    def get_project_executables(self):
        settings = self.__window.active_view().settings()
        exec_choices = settings.get("%s_%s" % (self.__project_settings_prefix, self.__project_exec_suffix))
        if exec_choices is not None and type(exec_choices) == dict:
            return list(exec_choices)
        return None

    def __load_project_settings(self, base_name):
        settings = sublime.load_settings(base_name)
        key = os.path.dirname(self.__window.project_file_name())
        return settings.get(key) if settings.has(key) else []

    def __save_project_settings(self, base_name, values):
        settings = sublime.load_settings(base_name)
        key = os.path.dirname(self.__window.project_file_name())
        settings.set(key,values)
        sublime.save_settings(base_name)


    def load_breakpoints(self):
        return self.__load_project_settings(self.__bkpt_settings_file_name)

    def save_breakpoints(self, bkpts):
        self.__save_project_settings(self.__bkpt_settings_file_name, bkpts)

    def load_watches(self):
        return self.__load_project_settings(self.__watch_settings_file_name)

    def save_watches(self, watches):
        self.__save_project_settings(self.__watch_settings_file_name, watches)

    @property
    def STATE_COMMAND(self):
        return 'state'

    @property
    def STACKTRACE_COMMAND(self):
        return 'stacktrace'

    @property
    def GOROUTINE_COMMAND(self):
        return 'goroutine'

    @property
    def VARIABLE_COMMAND(self):
        return 'variable'

    @property
    def WATCH_COMMAND(self):
        return 'watch'

    @property
    def CREATE_BREAKPOINT_COMMAND(self):
        return 'createbreakpoint'

    @property
    def CLEAR_BREAKPOINT_COMMAND(self):
        return 'clearbreakpoint'

    @property
    def BREAKPOINT_COMMAND(self):
        return 'listbreakpoints'

    @property
    def CONTINUE_COMMAND(self):
        return 'continue'

    @property
    def NEXT_COMMAND(self):
        return 'next'

    @property
    def CANCEL_NEXT_COMMAND(self):
        return 'cancelnext'

    @property
    def STEP_COMMAND(self):
        return 'step'

    @property
    def STEPOUT_COMMAND(self):
        return 'stepOut'

    @property
    def RESTART_COMMAND(self):
        return 'restart'

    @property
    def RUNTIME_COMMANDS(self):
        return [self.CONTINUE_COMMAND, self.NEXT_COMMAND, self.STEP_COMMAND, self.STEPOUT_COMMAND]

    @property
    def PANEL_GROUP(self):
        return self.__panel_group_suffix

    @property
    def OPEN_AT_START(self):
        return self.__open_at_start_suffix

    @property
    def CLOSE_AT_STOP(self):
        return self.__close_at_stop_suffix

    @property
    def TITLE(self):
        return self.__title_suffix

    @property
    def STDOUT(self):
        return 'stdout'

    @property
    def DEFAULT_HOST(self):
        return 'localhost'

    @property
    def DEFAULT_PORT(self):
        return 3456

    @property
    def DEFAULT_TIMEOUT(self):
        return 10

    @property
    def BUFFER(self):
        return 4096

    @property
    def DEBUG_MODE(self):
        return 'debug'

    @property
    def TEST_MODE(self):
        return 'test'

    @property
    def REMOTE_MODE(self):
        return 'remote'

    @property
    def DLV_REGION(self):
        return 'dlv.suspend_pos'

    # The mode of run Delve server, "remote" mean is not need start dlv headless instance
    # "debug" | "test" | "remote"
    @property
    def MODE(self):
        return self.__get_settings('mode', self.DEBUG_MODE) # 

    # The host of the Delve server
    @property
    def HOST(self):
        return self.__get_settings('host', self.DEFAULT_HOST)

    # The port of the Delve server
    @property
    def PORT(self):
        return self.__get_settings('port', self.DEFAULT_PORT)

    # If set, Delve server run in logging mode. Used for "local" or "test" mode
    @property
    def LOG(self):
        return self.__get_settings('log', False)

    # Arguments for run the program. (OPTIONAL)
    @property
    def ARGS(self):
        return self.__get_settings('args', '')

    # The current working directory where delve starts from. 
    # Default is project directory. Used for "local" or "test" mode. (OPTIONAL)
    @property
    def CWD(self):
        return self.__get_settings('cwd', '')

    # For the larger operation, by socket and background thread, in seconds, must be above zero
    @property
    def TIMEOUT(self):
        value = self.__get_settings('timeout', self.DEFAULT_TIMEOUT)
        if value <= 0:
            value = self.DEFAULT_TIMEOUT
        return value

    # Save breakpoints to the settings file before start debug, restore when the project is loaded
    @property
    def SAVE_BREAKPOINT(self):
        return self.__get_settings('save_breakpoints', True)

    # Save watches to the settings file before start debug, restore when the project is loaded
    @property
    def SAVE_WATCH(self):
        return self.__get_settings('save_watches', True)

    # Whether to log the raw data read from and written to the Delve session and the inferior program
    @property
    def DEBUG(self):
        return self.__get_settings('debug', False)

    # File to optionally write all the raw data read from and written to the Delve session and the inferior program.
    # Must be set 'stdout' or file name. If file name set without full path, save into project directory
    @property
    def DEBUG_FILE(self):
        return self.__get_settings('debug_file', self.STDOUT)

    # Defalt Delve panel layout
    @property
    def PANEL_LAYOUT(self):
        return self.__get_settings('panel_layout', 
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

    # View name
    @property
    def STACKTRACE_VIEW(self):
        return self.STACKTRACE_COMMAND

    # View group in Delve panel
    def __get_stacktrace_group(self):
        return self.__get_settings("%s_%s" % (self.STACKTRACE_VIEW, self.__panel_group_suffix), 2)

    # Open view when debugging starts
    def __get_stacktrace_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.STACKTRACE_VIEW, self.__open_at_start_suffix), True)

    # Close view when debugging stops
    def __get_stacktrace_close_at_stop(self):
        return self.__get_settings("%s_%s" % (self.STACKTRACE_VIEW, self.__close_at_stop_suffix), True)

    # View title
    def __get_stacktrace_title(self):
        return self.__get_settings("%s_%s" % (self.STACKTRACE_VIEW, self.__title_suffix), 'Delve Stacktrace')

    # View name
    @property
    def GOROUTINE_VIEW(self):
        return self.GOROUTINE_COMMAND

    # View group in Delve panel
    def __get_goroutine_group(self):
        return self.__get_settings("%s_%s" % (self.GOROUTINE_VIEW, self.__panel_group_suffix), 3)

    # Open view when debugging starts
    def __get_goroutine_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.GOROUTINE_VIEW, self.__open_at_start_suffix), True)

    # Close view when debugging stops
    def __get_goroutine_close_at_stop(self):
        return self.__get_settings("%s_%s" % (self.GOROUTINE_VIEW, self.__close_at_stop_suffix), True)

    # View title
    def __get_goroutine_title(self):
        return self.__get_settings("%s_%s" % (self.GOROUTINE_VIEW, self.__title_suffix), 'Delve Gorounites')

    # View name
    @property
    def VARIABLE_VIEW(self):
        return self.VARIABLE_COMMAND

    # View group in Delve panel
    def __get_variable_group(self):
        return self.__get_settings("%s_%s" % (self.VARIABLE_VIEW, self.__panel_group_suffix), 1)

    # Open view when debugging starts
    def __get_variable_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.VARIABLE_VIEW, self.__open_at_start_suffix), True)

    # Close view when debugging stops
    def __get_variable_close_at_stop(self):
        return self.__get_settings("%s_%s" % (self.VARIABLE_VIEW, self.__close_at_stop_suffix), True)

    # View title
    def __get_variable_title(self):
        return self.__get_settings("%s_%s" % (self.VARIABLE_VIEW, self.__title_suffix), 'Delve Variables')

    # View name
    @property
    def WATCH_VIEW(self):
        return self.WATCH_COMMAND

    # View group in Delve panel
    def __get_watch_group(self):
        return self.__get_settings("%s_%s" % (self.WATCH_VIEW, self.__panel_group_suffix), 2)

    # Open view when debugging starts
    def __get_watch_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.WATCH_VIEW, self.__open_at_start_suffix), True)

    # Close view when debugging stops
    def __get_watch_close_at_stop(self):
        return self.__get_settings("%s_%s" % (self.WATCH_VIEW, self.__close_at_stop_suffix), True)

    # View title
    def __get_watch_title(self):
        return self.__get_settings("%s_%s" % (self.WATCH_VIEW, self.__title_suffix), 'Delve Watches')

    # View name
    @property
    def SESSION_VIEW(self):
        return "session"

    # View group in Delve panel
    def __get_session_group(self):
        return self.__get_settings("%s_%s" % (self.SESSION_VIEW, self.__panel_group_suffix), 1)

    # Open view when debugging starts
    def __get_session_open_at_start(self):
        return True

    # Close view when debugging stops
    def __get_session_close_at_stop(self):
        return True

    # View title
    def __get_session_title(self):
        return self.__get_settings("%s_%s" % (self.SESSION_VIEW, self.__title_suffix), 'Delve Session')

    # View name
    @property
    def CONSOLE_VIEW(self):
        return "console"

    # View group in Delve panel
    def __get_console_group(self):
        return self.__get_settings("%s_%s" % (self.CONSOLE_VIEW, self.__panel_group_suffix), 1)

    # Open view when debugging starts
    def __get_console_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.CONSOLE_VIEW, self.__open_at_start_suffix), True)

    # Close view when debugging stops
    def __get_console_close_at_stop(self):
        return self.__get_settings("%s_%s" % (self.CONSOLE_VIEW, self.__close_at_stop_suffix), True)

    # View title
    def __get_console_title(self):
        return self.__get_settings("%s_%s" % (self.CONSOLE_VIEW, self.__title_suffix), 'Delve Console')

    # View name
    @property
    def BREAKPOINT_VIEW(self):
        return "breakpoints"

    # View group in Delve panel
    def __get_breakpoint_group(self):
        return self.__get_settings("%s_%s" % (self.BREAKPOINT_VIEW, self.__panel_group_suffix), 3)

    # Open view when debugging starts
    def __get_breakpoint_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.BREAKPOINT_VIEW, self.__open_at_start_suffix), True)

        # Close view when debugging stops
    def __get_breakpoint_close_at_stop(self):
        return self.__get_settings("%s_%s" % (self.BREAKPOINT_VIEW, self.__close_at_stop_suffix), True)

    # View title
    def __get_breakpoint_title(self):
        return self.__get_settings("%s_%s" % (self.BREAKPOINT_VIEW, self.__title_suffix), 'Delve Breakpoints')
