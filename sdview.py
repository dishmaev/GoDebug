import sublime
import sublime_plugin

from SublimeDelve.sdconst import dlv_const

class DlvView(object):
    def __init__(self, name, title, scroll=True):
        self.name = name
        self.title = title
        self.scroll = scroll
        self.view = None
        self.counter = 0

    def get_panel_group(self):
        global dlv_const
        return dlv_const.get_view_setting(self.name, dlv_const.PANEL_GROUP)

    def open(self):
        if self.view is None or self.view.window() is None:
            sublime.active_window().focus_group(self.get_panel_group())
            self.__create_view()

    def close(self):
        if self.view is not None:
            sublime.active_window().focus_group(self.get_panel_group())
            self.__destroy_view()

    def clear(self):
        self.update_view()

    def __create_view(self):
        self.view = sublime.active_window().new_file()
        self.view.set_name(self.title)
        self.view.set_scratch(True)
        self.view.set_read_only(True)
        self.view.settings().set('command_mode', False)

    def is_open_at_start(self):
        global dlv_const
        return dlv_const.get_view_setting(self.name, dlv_const.OPEN_AT_START)

    def is_open(self):
        return self.view is not None

    def is_closed(self):
        return self.view is None

    def get_view_id(self):
        if self.view is not None:
            return self.view.id()
        else:
            return None

    def get_viewport_position(self):
        if self.view is not None:
            return self.view.viewport_position()
        else:
            return None

    def was_closed(self):
        self.view = None

    def __destroy_view(self):
        sublime.active_window().focus_view(self.view)
        sublime.active_window().run_command("close")
        self.view = None
        self.counter = 0

    def add_line(self, line):
        if self.view is not None:
            self.counter += 1
            full_line = str(self.counter) + " - " + line + "\n"
            self.view.run_command("dlv_view_add_line", {"line": full_line, "scroll": self.scroll })

    def update_view(self):
        if self.view is not None:
            self.view.run_command("dlv_view_clear")
            self.counter = 0


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
