import requests

from neutron_lbaas.common.exceptions import LbaasException
from oslo_log import helpers as log_helpers
from oslo_log import log as logging
from oslo_serialization import base64
from oslo_serialization import jsonutils

LOG = logging.getLogger(__name__)

class XOSClient(object):

    def __init__(self, endpoint=None, base_url=None, user=None, password=None):
        if not (endpoint or user or password or base_url):
            LbaasException('XOS client failed: missing arguments')

        self.base_url = endpoint + base_url
        self.auth = base64.encode_as_text('%s:%s' % (user, password))
        self.auth = self.auth.replace('\n', '')
        LOG.debug('XOS client initialized, endpoint:%s %s:%s',
                  self.base_url, user, password)

    def get(self, url):
        return self._request('GET', url)

    def post(self, url, data):
        return self._request('POST', url, data)

    def put(self, url, data):
        return self._request('PUT', url, data)

    def delete(self, url):
        self._request('DELETE', url)

    @log_helpers.log_method_call
    def _request(self, method, url, data=None, headers=None):
        if data:
            data = jsonutils.dumps(data)

        if not headers:
            headers = {'Content-type': 'application/json'}
        headers['Authorization'] = 'Basic %s' % self.auth

        r = requests.request(method,
                            '%s%s' % (self.base_url, str(url)),
                            data=data,
                            headers=headers)
        if not r.ok:
            r.raise_for_status()
        return r.json() if r.status_code != 204 else {}

