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

def __default_variable_cfg():
    return  {  
                'followPointers': True,
                'maxVariableRecurse': 1,
                'maxStringLen': 64,
                'maxArrayValues': 64,
                'maxStructFields': -1
            }

def __get_current_goroutine(response):
    if 'State' in response:
        if  not response['State']['exited'] and 'currentThread' in response['State']:
            return response['State']['currentThread']['goroutineID']
    return None

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
            goroutine_id = None
            for request in requests:
                cmd = request["cmd"]
                parms = request["parms"]
                if parms is None:
                    parms = {}
                try:
                    if cmd == dlv_const.CONTINUE_COMMAND or \
                        cmd == dlv_const.NEXT_COMMAND or \
                        cmd == dlv_const.STEP_COMMAND or \
                        cmd == dlv_const.STEPOUT_COMMAND:
                        parms['name'] = cmd
                        response = dlv_connect.RPCServer.Command(parms)
                        goroutine_id = __get_current_goroutine(response)
                    elif cmd == dlv_const.STATE_COMMAND:
                        if errors:
                            errors = False
                        response = dlv_connect.RPCServer.State(parms)
                    elif cmd == dlv_const.CREATE_BREAKPOINT_COMMAND:
                        response = dlv_connect.RPCServer.CreateBreakpoint(parms)
                    elif cmd == dlv_const.CLEAR_BREAKPOINT_COMMAND:
                        response = dlv_connect.RPCServer.ClearBreakpoint(parms)
                    elif cmd == dlv_const.RESTART_COMMAND:
                        response = dlv_connect.RPCServer.Restart(parms)
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
                errors = False
                try:
                    response = dlv_connect.RPCServer.State({})
                    goroutine_id = __get_current_goroutine(response)
                    responses.append({"cmd": dlv_const.STATE_COMMAND, "result": True, "response": response})
                except JsonRpcTcpProtocolError as e:
                    responses.append({"cmd": dlv_const.STATE_COMMAND, "parms": None, "result": False, "errorcode": e.code, "errormessage": e.message})
                    errors = True
                except:
                    responses.append({"cmd": dlv_const.STATE_COMMAND, "parms": None, "result": False})
                    errors = True
            if not errors and goroutine_id is not None and goroutine_id != 0:
                parms = {"Scope": {"GoroutineID": goroutine_id}, "Cfg": __default_variable_cfg()}
                try:
                    response = dlv_connect.RPCServer.ListLocalVars(parms)
                    responseArgs = dlv_connect.RPCServer.ListFunctionArgs(parms)
                    response['Args'] = responseArgs['Args']
                    responses.append({"cmd": dlv_const.VARIABLE_COMMAND, "result": True, "response": response})
                except JsonRpcTcpProtocolError as e:
                    responses.append({"cmd": dlv_const.VARIABLE_COMMAND, "parms": None, "result": False, "errorcode": e.code, "errormessage": e.message})
                    errors = True
                except:
                    responses.append({"cmd": dlv_const.VARIABLE_COMMAND, "parms": None, "result": False})
                    errors = True
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
        if self.__queue is not None:
            self.__queue.put(None)
        self.__stoped = True

    def do(self, cmd, parms=None):
        self.do_batch([{"cmd": cmd, "parms": parms}])

    def do_batch(self, requests):
        if not self.__alive.isSet():
            dlv_logger.warning("Worker still not started, put job to queue")         
            if self.__stoped:
                self.__start()
        if type(requests) is not list:
            dlv_logger.error("Wrong requests type %s on worker call, list expected" % type(requests))
            return
        if len(requests) == 0:
            dlv_logger.error("Call worker with empty request")
            return
        self.__queue.put(requests)
