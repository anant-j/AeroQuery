from js import Object
from pyodide.ffi import to_js


class ApiError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


def _js_options(options):
    return to_js(options, dict_converter=Object.fromEntries)
