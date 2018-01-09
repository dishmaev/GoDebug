import sublime

class DlvConst(object):
    def __init__(self):
        self.__settings_file_name = "SublimeDelve.sublime-settings"
        self.__project_settings_prefix = "sublimedelve"
        self.__panel_group_suffix = "group"
        self.__open_at_start_suffix = "open_at_start"
        self.__project_exec_suffix = "executables"
        self.__project_exec_settings = {}
        self.__project_exec_name = None
        self.__view_switch = {
            self.SESSION_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_session_group,     
                self.__open_at_start_suffix: self.__get_session_open_at_start     
            },
            self.CONSOLE_VIEW:     
            { 
                self.__panel_group_suffix: self.__get_console_group,     
                self.__open_at_start_suffix: self.__get_console_open_at_start     
            },
            self.BREAKPOINTS_VIEW: 
            { 
                self.__panel_group_suffix: self.__get_breakpoints_group, 
                self.__open_at_start_suffix: self.__get_breakpoints_open_at_start 
            }, 
        }

    def __get_settings(self, key, default):
        if default is None:
            raise Exception("Default key %s value cannot be None" % key)
        if key in self.__project_exec_settings:
            return self.__project_exec_settings[key]
        window = sublime.active_window()
        if window is not None:
            view = window.active_view()
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
        window = sublime.active_window()
        if window is not None:
            view = window.active_view()
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
        window = sublime.active_window()
        if window is not None:
            view = window.active_view()
            if view is not None:
                settings = view.settings()
                exec_choices = settings.get("%s_%s" % (self.__project_settings_prefix, self.__project_exec_suffix))
                if exec_choices is not None and type(exec_choices) == dict:
                    return list(exec_choices)
        return None

    @property
    def CREATE_BREAKPOINT_COMMAND(self):
        return 'createbreakpoint'

    @property
    def CLEAR_BREAKPOINT_COMMAND(self):
        return 'clearbreakpoint'

    @property
    def CONTINUE_COMMAND(self):
        return 'continue'

    @property
    def NEXT_COMMAND(self):
        return 'next'

    @property
    def STEP_COMMAND(self):
        return 'step'

    @property
    def STEPOUT_COMMAND(self):
        return 'stepout'

    @property
    def RESTART_COMMAND(self):
        return 'restart'

    @property
    def STATE_COMMAND(self):
        return 'state'

    @property
    def EXIT_COMMAND(self):
        return 'exit'

    @property
    def PANEL_GROUP(self):
        return self.__panel_group_suffix

    @property
    def OPEN_AT_START(self):
        return self.__open_at_start_suffix

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
    def MODE_DEBUG(self):
        return 'debug'

    @property
    def MODE_TEST(self):
        return 'test'

    @property
    def MODE_REMOTE(self):
        return 'remote'

    # The mode of run Delve server, "remote" mean is not need start dlv headless instance
    @property
    def MODE(self):
        return self.__get_settings('mode', self.MODE_DEBUG) # "debug" | "test" | "remote"

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

    # Arguments for run the program
    @property
    def ARGS(self):
        return self.__get_settings('args', '') # OPTIONAL

    # The current working directory where delve starts from. Used for "local" or "test" mode
    @property
    def CWD(self):
        return self.__get_settings('cwd', '') # OPTIONAL

    # For the larger operation, by socket and background thread
    @property
    def TIMEOUT(self):
        return self.__get_settings('timeout', 10) # in seconds

    # Buffer size
    @property
    def BUFFER(self):
        return self.__get_settings('buffer', 4096) # in bytes

    # Whether to log the raw data read from and written to the Delve session and the inferior program.
    @property
    def DEBUG(self):
        return self.__get_settings('debug', False)

    # File to optionally write all the raw data read from and written to the Delve session and the inferior program
    @property
    def DEBUG_FILE(self):
        return self.__get_settings('debug_file', self.STDOUT) # 'stdout' or file name with full path

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
    def SESSION_VIEW(self):
        return "session"

    # View group in Delve panel
    def __get_session_group(self):
        return self.__get_settings("%s_%s" % (self.SESSION_VIEW, self.__panel_group_suffix), 1)

    # Open view when debugging starts
    def __get_session_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.SESSION_VIEW, self.__open_at_start_suffix), True)

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

    # View name
    @property
    def BREAKPOINTS_VIEW(self):
        return "breakpoints"

    # View group in Delve panel
    def __get_breakpoints_group(self):
        return self.__get_settings("%s_%s" % (self.BREAKPOINTS_VIEW, self.__panel_group_suffix), 3)

    # Open view when debugging starts
    def __get_breakpoints_open_at_start(self):
        return self.__get_settings("%s_%s" % (self.BREAKPOINTS_VIEW, self.__open_at_start_suffix), True)

dlv_const = DlvConst()
