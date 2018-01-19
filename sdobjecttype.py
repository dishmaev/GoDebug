class DlvObjectType(object):
    def __init__(self, __name, **kwargs):
        self.__object_name = __name
        self._kwargs = kwargs

    def __getattr__(self, attr):
        if attr.startswith('_'):
            raise AttributeError("Methods that start with _  , like %s, are not allowed" % attr)
        elif attr in self._kwargs:
            return self._kwargs.get(attr, None)
        else:
            raise AttributeError("Attribute %s not found, maybe data object still not loaded" % attr)

    @property
    def _as_parm(self):
        response = {}
        response[self.__object_name] = {}
        response[self.__object_name].update(self._kwargs)
        return response

    @property
    def _object_name(self):
        return self.__object_name

    def _update(self, data, name=None):
        if name is None:
            name = self._object_name
        if type(data) != dict or name not in data:
            raise TypeError("Wrong data type for update %s" % name)
        self._kwargs.update(data[name])

    def _is_loaded(self):
        return False
