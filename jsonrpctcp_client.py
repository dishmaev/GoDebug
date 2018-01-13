import sys 
import json
import socket 
import uuid

JSONRPC_ERRORS = {
    -32800: {'code':-32800, 'message':'Client connection not opened'},
    -32801: {'code':-32801, 'message':'Client socket send error'},
    -32802: {'code':-32802, 'message':'Client socket receive error'},
    -32803: {'code':-32803, 'message':'Client socket timeout'},
    -32804: {'code':-32804, 'message':'Client batch mode already enabled'},
    -32700: {'code':-32700, 'message':'Parse Delve response error'},
    -32701: {'code':-32701, 'message':'Internal Delve error'},
    -32600: {'code':-32600, 'message':'Invalid client request'},
}

class JsonRpcTcpProtocolError(Exception):
    """ Used for system errors and custom errors. """
    
    def __init__(self, code, message=None, data=None):
        if message is None:
            message = JSONRPC_ERRORS.get(code, {}).get('message', 'Unknown error')
        self.__message = message
        self.__code = code
        self.__data = data

    @property
    def code(self):
        return self.__code

    @property
    def message(self):
        return self.__message

    def generate_error(self, *args, **kwargs):
        response = {
            'jsonrpc':"2.0", 
            'error': {
                'message': self.__message,
                'code': self.__code
            },
            'id':kwargs.get('id', None)
        }
        return response
        
    def __repr__(self):
        return (
            '<ProtocolError> code:%s, message:%s, data:%s' % (self.__code, self.__message, self.__data)
        )

    def __str__(self):
        return self.__repr__()

class JsonRpcTcpClient(object):
    """
    This is the JSON RPC client class, which translates attributes into
    function calls and request / response translations, and organizes
    batches, notifications, etc.
    """
    # _requests = None
    # _request = None
    # _response = None

    def __init__(self, const, logger):
        self.__const = const
        self.__logger = logger
        self.__requests = []
        self.__batch = False
        self.__sock_opened = False

    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError('Methods that start with _ are not allowed')
        req_id = u'%s' % uuid.uuid4()
        request = JsonRpcTcpClientRequest(self, namespace=key, req_id=req_id)
        self.__requests.append(request)
        return request

    def _is_open(self):
        return self.__sock_opened

    def _open(self, host, port):
        if self.__sock_opened:
            self.__logger.debug("Socket already opened!")
            return
        self.__requests = []
        self.__batch = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.__const.TIMEOUT)
        self.sock.connect((host, port))
        self.__sock_opened = True
        self.__logger.debug("Open socket %s:%d" % (host, port))

    def _close(self):
        if self.__sock_opened:
            self.sock.close()
            self.sock = None
            self.__logger.debug("Close socket")
            self.__sock_opened = False
        else:
            self.__logger.debug("Socket already closed!")

    @property
    def _notification(self):
        """
        Returns a specialized version of the ClientRequest object,
        which is prepped for notification.
        """
        request = JsonRpcTcpClientRequest(
            self,
            notify = True,
            req_id = None
        )
        self.__requests.append(request)
        return request
        
    def _prepare_batch(self):
        """
        Prepare Client for batch calls
        """
        if self.__batch:
            raise JsonRpcTcpProtocolError(-32804)
        self.__batch = True
        
    def _is_batch(self):
        """ Checks whether the batch flag is set. """
        return self.__batch
        
    def __call__(self):
        if not self.__sock_opened:
            raise JsonRpcTcpProtocolError(-32800)
        assert len(self.__requests) > 0
        requests = []
        for i in range(len(self.__requests)):
            request = self.__requests.pop(0)
            requests.append(request._request())
        if not self._is_batch():
            result = self._call_single(requests[0])
        else:
            result = self._call_batch(requests)
            self.__batch = False    
        self.__requests = []
        return result
            
    def _call_single(self, request):
        """
        Processes a single request, and returns the response.
        """
        self._request = request
        try:
            message = json.dumps(request)
        except:
            raise JsonRpcTcpProtocolError(-32600)
        notify = False
        if 'id' not in request:
            notify = True
        response_text = self._send_and_receive(message, notify=notify)
        response = self._parse_response(response_text)
        if response is None:
            return response
        self._response = response        
        jsonrpctcp_validate_response(response)
        return response['result']
        
    def _call_batch(self, requests):
        """
        Processes a batch, and returns a generator to iterate over the
        response results.
        """
        ids = []
        for request in requests:
            if 'id' in request:
                ids.append(request['id'])
        self._request = requests
        try:
            message = json.dumps(requests)
        except:
            raise JsonRpcTcpProtocolError(-32600)
        notify = False
        if len(ids) == 0:
            notify = True
        response_text = self._send_and_receive(
            message, batch=True, notify=notify
        )
        responses = self._parse_response(response_text)
        if responses is None:
            responses = []
        assert type(responses) is list
        return JsonRpcTcpBatchResponses(responses, ids)
    
    def _send_and_receive(self, message, batch=False, notify=False):
        """
        Handles the socket connection, sends the JSON request, and
        (if not a notification) retrieves the response and decodes the
        JSON text.
        """
        responselist = []
        self.__logger.debug('CLIENT | REQUEST: %s' % message)

        try:
            self.sock.send(message.encode(sys.getdefaultencoding()))
        except socket.timeout:
            self._close()
            raise JsonRpcTcpProtocolError(-32803)
        except:
            self._close()
            raise JsonRpcTcpProtocolError(-32801)

        while not notify and self.__sock_opened:
            try:
                data = self.sock.recv(self.__const.BUFFER)
            except socket.timeout:
                self._close()
                raise JsonRpcTcpProtocolError(-32803)
            except:
                self._close()
                raise JsonRpcTcpProtocolError(-32802)
            if not data: 
                break
            response_text = data.strip().decode(sys.getdefaultencoding())
            responselist.append(response_text)
            if len(data) < self.__const.BUFFER:
                break
        response = ''.join(responselist)
        self.__logger.debug('CLIENT | RESPONSE: %s' % response)
        return response
        
    def _parse_response(self, response):
        if response == '':
            return None
        try:
            obj = json.loads(response)
        except ValueError:
            raise JsonRpcTcpProtocolError(-32700)
        if type(obj) is dict and 'error' in obj and obj.get('error') is not None:
            raise JsonRpcTcpProtocolError(-32701, obj.get('error'))
        return obj
        
class JsonRpcTcpBatchResponses(object):
    """ 
    This is just a wrapper around the responses so you can 
    iterate or retrieve by single id.
    """
    
    def __init__(self, responses, ids):
        self.__responses = responses
        self.__ids = ids        
        response_by_id = {}
        for response in responses:
            response_id = response.get('id', None)
            response_by_id.setdefault(response_id, [])
            response_by_id[response_id].append(response)
        self.__response_by_id = response_by_id
        
    def __iter__(self):
        for request_id in self.__ids:
            yield self.get(request_id)
            
    def get(self, req_id):
        responses = self.__response_by_id.get(req_id, None)
        if not responses:
            responses = self.__response_by_id.get(None)
        if not responses or len(responses) == 0:
            raise KeyError(
                'Job "%s" does not exist or has already be retrieved' 
                % req_id
            )
        response = responses.pop(0)
        jsonrpctcp_validate_response(response)
        return response['result']
        
           
class JsonRpcTcpClientRequest(object):
    """
    This is the class that holds all of the namespaced methods,
    as well as whether or not it is a notification. When it is
    finally called, it parses the arguments and passes it to
    the parent Client.
    """

    def __init__(self, client, namespace='', notify=False, req_id=None):
        self.__client = client
        self.__namespace = namespace
        self.__notification = notify
        self.__req_id = req_id
        self.__params = None

    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError
        if self.__namespace:
            self.__namespace += '.'
        self.__namespace += key
        return self
    
    def __call__(self,  *args, **kwargs):
        if not (len(args) == 0 or len(kwargs) == 0):
            raise ValueError(
                "JSON spec allows positional arguments OR " + \
                "keyword arguments, not both."
            )
        params = list(args)
        if len(kwargs) > 0:
            params = kwargs
        return self._call_server(params)
        
    def _call_server(self, params):
        """
        Forms a valid jsonrpc query, and passes it on to the parent
        Client, returning the response.
        """
        self.__params = params
        if not self.__client._is_batch():
            return self.__client()
        # Add batch logic here
        
    def _request(self):
        request = {
            'jsonrpc':'2.0', 
            'method': self.__namespace
        }
        if self.__params:
            request['params'] = self.__params
        if not self.__notification:
            request['id'] = self.__req_id
        return request
            
def jsonrpctcp_validate_response(response):
    """
    Parses the returned JSON object, verifies that it follows
    the JSON-RPC spec, and checks for errors, raising exceptions
    as necessary.
    """
#    jsonrpc = 'jsonrpc' in response
    response_id = 'id' in response
    result = 'result' in response
    error = 'error' in response
 #   if not jsonrpc or not response_id or (not result and not error):
    if not response_id or (not result and not error):
        raise Exception('Server returned invalid response')
    if error and response.get('error') is not None:
        raise JsonRpcTcpProtocolError(
            -32701, 
            response.get('error')
        )