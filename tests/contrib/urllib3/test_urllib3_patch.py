from ddtrace.contrib.urllib3 import patch
from tests.contrib.patch import PatchTestCase


class TestUrllib3Patch(PatchTestCase.Base):
    __integration_name__ = "urllib3"
    __module_name__ = "urllib3"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, urllib3):
        self.assert_wrapped(urllib3.connectionpool.HTTPConnectionPool.urlopen)

    def assert_not_module_patched(self, urllib3):
        self.assert_not_wrapped(urllib3.connectionpool.HTTPConnectionPool.urlopen)

    def assert_not_module_double_patched(self, urllib3):
        self.assert_not_double_wrapped(urllib3.connectionpool.HTTPConnectionPool.urlopen)
