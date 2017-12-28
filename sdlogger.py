import queue
import threading
import logging

class DlvLogger(object):
    def __init__(self):
        self.__log_queue = queue.Queue()
        self.__enabled = False
        self.__started = False
        self.__initialized = False
        self.__lock = threading.RLock()
        self.__log = logging.getLogger('SublimeDelve')
        self.__logging_level_switch = {
                'debug':    self.__log.debug,
                'info':     self.__log.info,
                'warning':  self.__log.warning,
                'error':    self.__log.error,
                'critical': self.__log.critical 
                }

    def is_started(self):
        return self.__started

    def start(self, enabled, file):
        if enabled:
            self.__enabled = enabled
            if not self.__initialized:
                self.__log.setLevel(logging.DEBUG)
                if file != "stdout":
                    fh = logging.FileHandler(file);
                    fh.setLevel(logging.DEBUG)
                    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                    fh.setFormatter(formatter)
                    self.__log.addHandler(fh)
                self.__initialized = True
            if not self.__started:
                self.__logging_level_switch["info"]("Start logging")
                self.__started = True
            else:
                self.__logging_level_switch["debug"]("Logging already started!")

    def __write_log(self, get, logging_level_switch):
        item = get()
        if item is None:
            logging_level_switch["info"]("Stop logging")
            return
        logging_level_switch[item["level"]](item["message"])

    def stop(self):
        if self.__enabled and self.__started:
            self.__lock.acquire()
            self.__log_queue.put(None)
            self.__started = False
            self.__lock.release()
            self.__write_log(self.__log_queue.get, self.__logging_level_switch)


    def __do_log(self, level, message):
        if self.__enabled:
            self.__log_queue.put({"level":"%s" % level, "message":"%s" % message})
            self.__write_log(self.__log_queue.get, self.__logging_level_switch)

    def debug(self, message):
        if self.__enabled:
            self.__do_log("debug", message)

    def info(self, message):
        if self.__enabled:
            self.__do_log("info", message)

    def warning(self, message):
        if self.__enabled:
            self.__do_log("warning", message)

    def error(self, message):
        if self.__enabled:
            self.__do_log("error", message)

    def critical(self, message):
        if self.__enabled:
            self.__do_log("critical", message)

dlv_logger = DlvLogger()
