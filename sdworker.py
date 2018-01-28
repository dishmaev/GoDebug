import sublime
import threading
import traceback
import sys 
import queue

from GoDebug.jsonrpctcp_client import JsonRpcTcpClient
from GoDebug.jsonrpctcp_client import JsonRpcTcpProtocolError

def __start(connect, const, logger):
    logger.debug("Start worker")
    try:
        connect._open(const.HOST, const.PORT)
        return True
    except:
        traceback.print_exc(file=(sys.stdout if logger.get_file() == const.STDOUT else open(logger.get_file(),"a")))
        logger.error("Exception thrown, details in file: %s" % logger.get_file())
    return False

def __stop(connect, const, logger):
    try:
        if connect._is_open():
            connect._close()
    except:
        traceback.print_exc(file=(sys.stdout if logger.get_file() == const.STDOUT else open(logger.get_file(),"a")))
        logger.error("Exception thrown, details in file: %s" % logger.get_file())
    logger.debug("Stop worker")

def __default_cfg():
    return  {  
                'followPointers': True,
                'maxVariableRecurse': 1,
                'maxStringLen': 64,
                'maxArrayValues': 64,
                'maxStructFields': -1
            }

def __get_eval_parms(goroutine_id, frame, expr):
    return {"Scope": {"GoroutineID": goroutine_id, "Frame": frame}, "Expr": expr, "Cfg": __default_cfg()}

def __get_variable_parms(goroutine_id, frame):
    return {"Scope": {"GoroutineID": goroutine_id, "Frame": frame}, "Cfg": __default_cfg()}

def __get_stacktrace_parms(goroutine_id):
    # return {"Id": goroutine_id, "Depth": 20, "Full": False, "Cfg": __default_cfg()}
    return {"Id": goroutine_id, "Depth": 20}

def __get_current_goroutine(response):
    if type(response) is dict:
        if 'State' in response:
            if not response['State']['exited'] and 'currentThread' in response['State']:
                return response['State']['currentThread']['goroutineID']
    return None

def __get_error_response(cmd, parms):
    return {"cmd": cmd, "parms": parms, "result": False}

def __get_error_response_ex(cmd, parms, e):
    return {"cmd": cmd, "parms": parms, "result": False, "error_code": e.code, "error_message": e.message}

def _do_method(alive, queue, prj, worker_callback=None):
    const = prj.const
    logger = prj.logger
    connect = JsonRpcTcpClient(const, logger)
    if __start(connect, const, logger):
        alive.set()
        while alive.isSet():
            requests = queue.get()
            if requests is None:
                alive.clear()
                continue
            responses = []
            errors = False
            goroutine_id = None
            goroutines = False
            frame = 0
            watches = None
            for request in requests:
                cmd = request["cmd"]
                parms = request["parms"]
                if parms is None:
                    parms = {}
                try:
                    if cmd in const.RUNTIME_COMMANDS:
                        parms['name'] = cmd
                        response = connect.RPCServer.Command(parms)
                        goroutine_id = __get_current_goroutine(response)
                    elif cmd == const.STATE_COMMAND:
                        if errors:
                            errors = False
                        response = connect.RPCServer.State(parms)
                        goroutine_id = __get_current_goroutine(response)
                    elif cmd == const.CREATE_BREAKPOINT_COMMAND:
                        response = connect.RPCServer.CreateBreakpoint(parms)
                    elif cmd == const.CLEAR_BREAKPOINT_COMMAND:
                        response = connect.RPCServer.ClearBreakpoint({"Id": parms['bkpt_id'], "Name": parms['bkpt_name']})
                    elif cmd == const.RESTART_COMMAND:
                        response = connect.RPCServer.Restart(parms)
                    elif cmd == const.CANCEL_NEXT_COMMAND:
                        response = connect.RPCServer.CancelNext(parms)
                    elif cmd == const.STACKTRACE_COMMAND:
                        response = connect.RPCServer.Stacktrace(__get_stacktrace_parms(parms['goroutine_id']))
                    elif cmd == const.BREAKPOINT_COMMAND:
                        response = connect.RPCServer.ListBreakpoints(parms)
                    elif cmd == const.VARIABLE_COMMAND:
                        call_parms = __get_variable_parms(parms['goroutine_id'], parms['frame'])
                        response_locals = connect.RPCServer.ListLocalVars(call_parms)
                        response_args = connect.RPCServer.ListFunctionArgs(call_parms)
                        response = {"Locals": response_locals['Variables'], "Arguments": response_args['Args']}
                    elif cmd == const.WATCH_COMMAND:
                        if 'goroutine_id' in parms:
                            goroutine_id = parms['goroutine_id']
                            frame = parms['frame']
                        watches = parms['watches']
                        continue  
                    elif cmd == const.GOROUTINE_COMMAND:
                        if not goroutines:
                            goroutines = True
                        continue  
                    else:
                        raise ValueError("Unknown worker command: %s" % cmd)
                    responses.append({"cmd": cmd, "result": True, "response": response})
                except JsonRpcTcpProtocolError as e:
                    traceback.print_exc(file=(sys.stdout if logger.get_file() == const.STDOUT else open(logger.get_file(),"a")))
                    logger.error("Exception thrown, details in file: %s" % logger.get_file())
                    responses.append(__get_error_response_ex(cmd, parms, e))
                    if cmd not in [const.STATE_COMMAND, const.CREATE_BREAKPOINT_COMMAND, const.CLEAR_BREAKPOINT_COMMAND]:
                        errors = True
                except:
                    traceback.print_exc(file=(sys.stdout if logger.get_file() == const.STDOUT else open(logger.get_file(),"a")))
                    logger.error("Exception thrown, details in file: %s" % logger.get_file())
                    responses.append(__get_error_response(cmd, parms))
                    if cmd not in [const.STATE_COMMAND, const.CREATE_BREAKPOINT_COMMAND, const.CLEAR_BREAKPOINT_COMMAND]:
                        errors = True
            parms = {}
            if errors:
                errors = False
                cmd = const.STATE_COMMAND
                try:
                    response = connect.RPCServer.State(parms)
                    goroutine_id = __get_current_goroutine(response)
                    responses.append({"cmd": cmd, "result": True, "response": response})
                except JsonRpcTcpProtocolError as e:
                    responses.append(__get_error_response_ex(cmd, parms, e))
                    errors = True
                except:
                    responses.append(__get_error_response(cmd, parms))
                    errors = True
            if not errors and goroutines:
                cmd = const.GOROUTINE_COMMAND
                try:
                    response = connect.RPCServer.ListGoroutines(parms)
                    found = False
                    for gr in response['Goroutines']:
                        if gr['id'] == goroutine_id:
                            found = True
                            break
                    if not found:
                        goroutine_id = 0
                        errors = True
                    responses.append({"cmd": const.GOROUTINE_COMMAND, "result": True, "response": response, "current_goroutine_id": goroutine_id})
                except JsonRpcTcpProtocolError as e:
                    responses.append(__get_error_response_ex(cmd, parms, e))
                    errors = True
                except:
                    responses.append(__get_error_response(cmd, parms))
                    errors = True
            if not errors and watches is not None and goroutine_id > 0:
                cmd = const.WATCH_COMMAND
                response_watches = []
                for element in watches:
                    try:
                        value = connect.RPCServer.Eval(__get_eval_parms(goroutine_id, frame, element['expr']))
                        response_watches.append({"watch_id": element['watch_id'], "result": True, "eval": value})
                    except JsonRpcTcpProtocolError as e:
                        response_watches.append(__get_error_response_ex(cmd, element, e))
                    except:
                        response_watches.append(__get_error_response(cmd, element))
                responses.append({"cmd": const.WATCH_COMMAND, "result": True, "response": response_watches})

            if worker_callback is not None:
                # callback
                sublime.set_timeout(worker_callback(prj, responses), 0)
    __stop(connect, const, logger)

class DlvWorker(object):
    def __init__(self, prj, worker_callback = None):
        self.__prj = prj
        self.__worker_callback = worker_callback
        self.__alive = threading.Event()
        self.__queue = None
        self.__stoped = True

    def __start(self):
        self.__stoped = False
        self.__queue = queue.Queue()
        t = threading.Thread(name='worker', target=_do_method, args=(self.__alive, self.__queue, self.__prj, self.__worker_callback))
        t.start()

    def stop(self):
        if self.__queue is not None:
            self.__queue.put(None)
        self.__stoped = True

    def do(self, cmd, parms=None):
        self.do_batch([{"cmd": cmd, "parms": parms}])

    def do_batch(self, requests):
        logger = self.__prj.logger
        if not self.__alive.isSet():
            logger.warning("Worker not started, put requests to the queue")         
            if self.__stoped:
                self.__start()
        if type(requests) is not list:
            logger.error("Wrong requests type %s on worker call, list expected" % type(requests))
            return
        elif len(requests) == 0:
            logger.error("Call worker with empty request")
            return
        self.__queue.put(requests)
