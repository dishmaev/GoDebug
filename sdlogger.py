import queue
import threading
import logging
import os

class DlvLogger(object):
    def __init__(self, window, const):
        self.__window = window
        self.__const = const
        self.__fh = None
        self.__file = "" # log file name, not "stdout"
        self.__log_queue = queue.Queue()
        self.__started = False
        self.__lock = threading.RLock()
        self.__log = logging.getLogger("SublimeDelve")
        self.__logging_level_switch = {
                'debug':    self.__log.debug,
                'info':     self.__log.info,
                'warning':  self.__log.warning,
                'error':    self.__log.error,
                'critical': self.__log.critical 
                }
    
    def __get_file_name(self, file):
        file_name = self.__file
        dir_name = os.path.dirname(file_name)
        if dir_name == '':
            dir_name = os.path.dirname(self.__window.project_file_name())
            file_name = dirname + file_name
        return file_name

    def start(self, file):
        file_name = (file if file == self.__const.STDOUT else __get_file_name(file))
        if self.__started:
            if self.__file == file_name:
                self.__logging_level_switch["debug"]("Logging already started!")
                return
            else:
                self.stop()

        self.__log.setLevel(logging.DEBUG if self.__const.DEBUG else logging.INFO)
        if file != self.__const.STDOUT:
            self.__file = file_name
            self.__fh = logging.FileHandler(file_name);
            self.__fh.setLevel(logging.DEBUG if self.__const.DEBUG else logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            self.__fh.setFormatter(formatter)
            self.__log.addHandler(self.__fh)

        self.__logging_level_switch["info"]("Start logging to file: %s" % self.get_file())
        self.__started = True

    def get_file(self):
        return (self.__const.STDOUT if self.__file == "" else self.__file)

    def __write_log(self, get, logging_level_switch):
        item = get()
        if item is None:
            logging_level_switch["info"]("Stop logging")
            return
        logging_level_switch[item["level"]](item["message"])

    def stop(self):
        if not self.__started:
            self.__logging_level_switch["debug"]("Logging already stopped!")
            return

        if self.__started:
            self.__lock.acquire()
            self.__log_queue.put(None)
            self.__lock.release()
            self.__write_log(self.__log_queue.get, self.__logging_level_switch)
            if self.__fh is not None:
                self.__log.removeHandler(self.__fh)
                self.__file = ""
                self.__fh = None
            self.__started = False

    def __do_log(self, level, message):
        self.__log_queue.put({"level":"%s" % level, "message":"%s" % message})
        self.__write_log(self.__log_queue.get, self.__logging_level_switch)

    def debug(self, message):
        self.__do_log("debug", message)

    def info(self, message):
        self.__do_log("info", message)

    def warning(self, message):
        self.__do_log("warning", message)

    def error(self, message):
        self.__do_log("error", message)

    def critical(self, message):
        self.__do_log("critical", message)
