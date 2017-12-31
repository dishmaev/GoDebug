
class DlvObjectType(object):
    def __init__(self, __name, **kwargs):
        self.__object_name = __name
        self.__kwargs = kwargs

    def __getattr__(self, attr):
        if attr.startswith('_'):
            raise AttributeError('Methods that start with _ are not allowed')
        elif attr in self.__kwargs:
            return self.__kwargs.get(attr, None)
        else:
            raise AttributeError("Attribute %s not found, maybe data object still not loaded" % attr)

    @property
    def _as_parm(self):
        response = {}
        response[self._object_name] = {}
        response[self._object_name].update(self.__kwargs)
        return response

    @property
    def _object_name(self):
        return self.__object_name

    def _update(self, data):
        if type(data) != dict or self._object_name not in data:
            raise TypeError("Wrong data type for update" % attr)
        self.__kwargs.update(data[self._object_name])
