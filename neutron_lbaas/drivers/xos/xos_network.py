from neutron_lbaas.drivers.xos import xos_client

from oslo_config import cfg
from oslo_log import log as logging


LOG = logging.getLogger(__name__)

OPTS = [
    cfg.IntOpt(
        'lbaas_slice_id',
        default=1,
        help=_('Slice ID for XOS LBaaS service.')
    ),
    cfg.IntOpt(
        'kuryr_template_id',
        default=1,
        help=_('Network template ID for Kuryr type network.')
    ),
]

cfg.CONF.register_opts(OPTS, 'xos')


class XOSNetwork(object):

    def __init__(self, name=None,
                 subnetpool=None,
                 subnet_range=None,
                 gateway_ip=None):
        self.name = name
        self.subnetpool = subnetpool
        self.subnet_range = subnet_range
        self.gateway_ip = gateway_ip


class XOSNetworkManager(object):

    def __init__(self):
        self.client = xos_client.XOSClient(cfg.CONF.xos.endpoint,
                                           'api/core/networks/',
                                           cfg.CONF.xos.user,
                                           cfg.CONF.xos.password)

    def create(self, xos_net):
        owner = '%s/api/core/slices/%s/' %\
                (cfg.CONF.xos.endpoint, cfg.CONF.xos.lbaas_slice_id)
        template = '%s/api/core/networktemplates/%s/' %\
                   (cfg.CONF.xos.endpoint, cfg.CONF.xos.kuryr_template_id)
        args = {
            'name': xos_net.name,
            'subnet': xos_net.subnet_range,
            'start_ip': xos_net.gateway_ip,
            'labels': xos_net.subnetpool,
            'template': template,
            'owner': owner
        }

        r = self.client.post('', args)
        LOG.info('created xos network %s', r)

    def network_exist(self, net_name):
        r = self.client.get('?name=%s' % net_name)
        return True if len(r) == 1 else False

    def delete(self, net_name):
        # need xos specific id to remove
        pass

