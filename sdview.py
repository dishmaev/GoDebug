import sublime
import sublime_plugin

class DlvView(object):
    def __init__(self, name, window, const, view=None, scroll=False):
        self.__name = name
        self.__window = window
        self.__const = const
        self.__view = view
        self.__scroll = scroll
        self.__dirty = (view is not None)

    @property
    def name(self):
        return self.__name

    @property
    def window(self):
        return self.__window

    @property
    def const(self):
        return self.__const

    @property
    def view(self):
        return self.__view

    def __get_panel_group(self):
        return self.__const.get_view_setting(self.__name, self.__const.PANEL_GROUP)

    def open(self, reset=False):
        if self.__view is None or self.__view.window() is None:
            self.__window.focus_group(self.__get_panel_group())
            self.__create_view()

    def close(self):
        if self.__view is not None:
            self.__window.focus_group(self.__get_panel_group())
            self.__destroy_view()

    def clear(self, reset=False):
        self.update_view()

    def __create_view(self):
        self.__view = self.__window.new_file()
        self.__view.set_name(self.__const.get_view_setting(self.__name, self.__const.TITLE))
        self.__view.set_scratch(True)
        self.__view.set_read_only(True)
        self.__view.settings().set('command_mode', False)

    def is_open_at_start(self):
        return self.__const.get_view_setting(self.__name, self.__const.OPEN_AT_START)

    def is_close_at_stop(self):
        return self.__const.get_view_setting(self.__name, self.__const.CLOSE_AT_STOP)

    def is_dirty(self):
        return self.__dirty

    def reset_dirty(self):
        self.__dirty = False

    def is_open(self):
        return self.__view is not None

    def is_closed(self):
        return self.__view is None

    def id(self):
        if self.__view is not None:
            return self.__view.id()
        else:
            return None

    def was_closed(self):
        self.__view = None

    def __destroy_view(self):
        self.__window.focus_view(self.__view)
        self.__window.run_command("close")
        self.__view = None

    def add_line(self, line, prefix=' - '):
        if self.__view is not None:
            full_line = prefix + line + "\n"
            self.view.run_command("dlv_view_add_line", {"line": full_line, "scroll": self.__scroll})

    def update_view(self):
        if self.__view is not None:
            self.view.run_command("dlv_view_clear")

    def set_syntax(self, syntax):
        if self.is_open():
            self.__view.set_syntax_file(syntax)

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
