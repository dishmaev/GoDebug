import sublime
import threading
import traceback
import sys 
import queue

from SublimeDelve.sdconst import dlv_const
from SublimeDelve.sdlogger import dlv_logger
from SublimeDelve.jsonrpctcp_client import dlv_connect
from SublimeDelve.jsonrpctcp_client import JsonRpcTcpProtocolError

def __start():
    dlv_logger.debug("Start worker")
    try:
        dlv_connect._open(dlv_const.HOST, dlv_const.PORT)
        return True
    except:
        traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
        dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())
    return False

def __stop():
    try:
        dlv_connect._close()
    except:
        traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
        dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())
    dlv_logger.debug("Stop worker")

def _do_method(alive, queue, worker_callback=None):
    if __start():
        alive.set()
        while alive.isSet():
            requests = queue.get()
            if requests is None:
                alive.clear()
                continue
            responses = []
            errors = False
            for request in requests:
                cmd = request["cmd"]
                parms = request["parms"]
                try:
                    if cmd == dlv_const.CONTINUE_COMMAND or \
                        cmd == dlv_const.NEXT_COMMAND or \
                        cmd == dlv_const.STEP_COMMAND or \
                        cmd == dlv_const.STEPOUT_COMMAND or \
                        cmd == dlv_const.RESTART_COMMAND or \
                        cmd == dlv_const.EXIT_COMMAND:
                        response = dlv_connect.RPCServer.Command({"name": cmd})
                    elif cmd == dlv_const.STATE_COMMAND:
                        if errors:
                            errors = False
                        response = dlv_connect.RPCServer.State({})
                    elif cmd == dlv_const.CREATE_BREAKPOINT_COMMAND:
                        response = dlv_connect.RPCServer.CreateBreakpoint(parms)
                    elif cmd == dlv_const.CLEAR_BREAKPOINT_COMMAND:
                        response = dlv_connect.RPCServer.ClearBreakpoint(parms)
                    else:
                        raise ValueError("Unknown worker command: %s" % cmd)
                    responses.append({"cmd": cmd, "result": True, "response": response})
                except JsonRpcTcpProtocolError as e:
                    traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
                    dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())
                    responses.append({"cmd": cmd, "parms": parms, "result": False, "errorcode": e.code, "errormessage": e.message})
                    if cmd != dlv_const.STATE_COMMAND:
                        errors = True
                except:
                    traceback.print_exc(file=(sys.stdout if dlv_logger.get_file() == dlv_const.STDOUT else open(dlv_logger.get_file(),"a")))
                    dlv_logger.error("Exception thrown, details in file: %s" % dlv_logger.get_file())
                    responses.append({"cmd": cmd, "parms": parms, "result": False})
                    if cmd != dlv_const.STATE_COMMAND:
                        errors = True
            if errors:
                try:
                    response = dlv_connect.RPCServer.State({})
                    responses.append({"cmd": 'state', "result": True, "response": response})
                except JsonRpcTcpProtocolError as e:
                    responses.append({"cmd": 'state', "parms": None, "result": False, "errorcode": e.code, "errormessage": e.message})
                except:
                    responses.append({"cmd": 'state', "parms": None, "result": False})
            if worker_callback is not None:
                # callback
                sublime.set_timeout(worker_callback(responses), 0)
    __stop()

class DlvWorker(object):
    def __init__(self, worker_callback = None):
        self.__worker_callback = worker_callback
        self.__alive = threading.Event()
        self.__queue = None
        self.__stoped = True

    def __start(self):
        self.__stoped = False
        self.__queue = queue.Queue()
        t = threading.Thread(name='worker', 
                      target=_do_method,
                      args=(self.__alive, self.__queue, self.__worker_callback))
        t.start()

    def stop(self):
        self.__queue.put(None)
        self.__stoped = True

    def do(self, cmd, parms=None):
        self.do_batch([{"cmd": cmd, "parms": parms}])

    def do_batch(self, requests):
        if not self.__alive.isSet():
            dlv_logger.warning("Worker still not started, call put to queue")         
            if self.__stoped:
                self.__start()
        if type(requests) is not list:
            dlv_logger.error("Wrong requests type %s on worker call, list expected" % type(requests))
            return
        if len(requests) == 0:
            dlv_logger.error("Call worker with empty request")
            return
        self.__queue.put(requests)
