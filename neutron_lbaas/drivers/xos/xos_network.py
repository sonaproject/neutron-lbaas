from neutron_lbaas.drivers.xos import xos_client

from oslo_config import cfg
from oslo_log import log as logging


LOG = logging.getLogger(__name__)

OPTS = [
    cfg.IntOpt(
        'user_id',
        default=1,
        help=_('XOS user ID.')
    ),
    cfg.IntOpt(
        'site_id',
        default=1,
        help=_('Site ID for XOS LBaaS service.')
    ),
    cfg.StrOpt(
        'site_name',
        default='mysite',
        help=_('Site name for XOS LBaaS service.')
    ),
    cfg.IntOpt(
        'service_id',
        default=1,
        help=_('XOS LBaaS service ID.')
    ),
    cfg.IntOpt(
        'image_id',
        default=1,
        help=_('XOS LBaaS image ID.')
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
        self.template = '%s/api/core/networktemplates/%s/' %\
                        (cfg.CONF.xos.endpoint,
                         cfg.CONF.xos.kuryr_template_id)


class XOSNetworkManager(object):

    def __init__(self):
        self.client = xos_client.XOSClient(
            cfg.CONF.xos.endpoint,
            '',
            cfg.CONF.xos.user,
            cfg.CONF.xos.password
        )

    def create(self, xos_net):
        slice_name = '%s_%s' % (cfg.CONF.xos.site_name, xos_net.name)
        slice_id = self.slice_exist(slice_name)

        if not slice_id:
            endpoint = '%sapi/core' % cfg.CONF.xos.endpoint
            slice_args = {
                'name': slice_name,
                'description': '/usr/local/etc/haproxy/',
                'mount_data_sets': '/usr/local/etc/haproxy/',
                'network': 'noauto',
                'creator': '%s/users/%s/' % (endpoint, cfg.CONF.xos.user_id),
                'site': '%s/sites/%s/' % (endpoint, cfg.CONF.xos.site_id),
                'service': '%s/services/%s/' % (endpoint, cfg.CONF.xos.service_id),
                'default_image': '%s/images/%s/' % (endpoint, cfg.CONF.xos.image_id),
            }

            r = self.client.post('api/core/slices/', slice_args)
            slice_id = r.get('id')
            LOG.info('created xos slice %s', r)

        network_args = {
            'name': xos_net.name,
            'subnet': xos_net.subnet_range,
            'start_ip': xos_net.gateway_ip,
            'labels': xos_net.subnetpool,
            'template': xos_net.template,
            'owner': '%s/slices/%s/' % (endpoint, slice_id)
        }

        r = self.client.post('api/core/networks/', network_args)
        LOG.info('created xos network %s', r)

    def slice_exist(self, slice_name):
        r = self.client.get('api/core/slices/?name=%s' % slice_name)
        return r[0].get('id') if len(r) == 1 else None

    def network_exist(self, net_name):
        r = self.client.get('api/core/networks/?name=%s' % net_name)
        return True if len(r) == 1 else False

    def delete(self, net_name):
        # need xos specific id to remove
        pass


