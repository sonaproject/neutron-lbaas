from datetime import datetime
import threading
import time

from neutron_lbaas.drivers import driver_base
from neutron_lbaas.drivers.xos import xos_client
from neutron_lbaas.drivers.xos import xos_network

from oslo_config import cfg
from oslo_log import log as logging


LOG = logging.getLogger(__name__)

OPTS = [
    cfg.StrOpt(
        'endpoint',
        default='http://127.0.0.1:9000',
        help=_('XOS service endpoint URL'),
    ),
    cfg.StrOpt(
        'user',
        default='xosadmin@opencord.org',
        help=_('XOS admin user'),
    ),
    cfg.StrOpt(
        'password',
        default='',
        help=_('XOS admin user password'),
    ),
    cfg.BoolOpt(
        'allocates_vip',
        default=True,
        help=_('True if XOS is responsible for allocating the VIP'),
    ),
    cfg.IntOpt(
        'request_poll_interval',
        default=3,
        help=_('Interval in seconds to poll XOS when an entity is created,'
               ' updated, or deleted.')
    ),
    cfg.IntOpt(
        'request_poll_timeout',
        default=100,
        help=_('Time to stop polling XOS when a status of an entity does '
               'not change.')
    ),
]

cfg.CONF.register_opts(OPTS, 'xos')


class XOSDriver(driver_base.LoadBalancerBaseDriver):

    def __init__(self, plugin):
        super(XOSDriver, self).__init__(plugin)

        self.client = xos_client.XOSClient(cfg.CONF.xos.endpoint,
                                           'api/tenant',
                                           cfg.CONF.xos.user,
                                           cfg.CONF.xos.password)

        self.xos_network = xos_network.XOSNetworkManager()
        self.load_balancer = LoadBalancerManager(self)
        self.listener = ListenerManager(self)
        self.pool = PoolManager(self)
        self.member = MemberManager(self)
        self.health_monitor = HealthMonitorManager(self)
        LOG.info('XOS LBaaS driver initialized')

    @property
    def allocates_vip(self):
        return self.load_balancer.allocates_vip


class LoadBalancerManager(driver_base.BaseLoadBalancerManager):

    @staticmethod
    def _url(id=None):
        s = '/loadbalancers/'
        if id:
            s += '%s/' % id
        return s

    @property
    def allocates_vip(self):
        return cfg.CONF.xos.allocates_vip

    @property
    def allows_create_graph(self):
        return False

    def _construct_args(self, db_lb, vip_network=None):
        args = {
            'name': db_lb.name,
        }

        if vip_network:
            vip_network_name = {'vip_network_name': vip_network}
            args.update(vip_network_name)

        if db_lb.listeners and db_lb.listeners[0].name:
            try:
                listener = {'listener': int(db_lb.listeners[0].name)}
                args.update(listener)
            except ValueError:
                pass

        if db_lb.pools and db_lb.pools[0].name:
            try:
                pool = {'pool': int(db_lb.pools[0].name)}
                args.update(pool)
            except ValueError:
                pass

        LOG.debug("returning loadbalancer args: %s", args)
        return args

    def _get_vip_port_id(self, context, vip_subnet_id, vip_address):
        filters = {'fixed_ips': {'ip_address': [vip_address],
                                 'subnet_id': [vip_subnet_id]}}
        vip_ports = self.driver.plugin.db._core_plugin.get_ports(context, filters=filters)
        vip_port = vip_ports[0] if vip_ports and len(vip_ports) == 1 else None
        return vip_port.get('id') if vip_port else None

    def _ensure_xos_network(self, context, lb):
        s = self.driver.plugin.db._core_plugin.get_subnet(context, lb.vip_subnet_id)
        n = self.driver.plugin.db._core_plugin.get_network(context, s.get('network_id'))
        vip_network_name = n.get('name')
        if self.driver.xos_network.network_exist(vip_network_name):
            LOG.debug("xos network %s already exists", vip_network_name)
            return vip_network_name
        xos_net = xos_network.XOSNetwork(name=vip_network_name,
                                         subnetpool=s.get('subnetpool_id'),
                                         subnet_range=s.get('cidr'),
                                         gateway_ip=s.get('gateway_ip'))
        self.driver.xos_network.create(xos_net)
        return vip_network_name

    def _thread_op(self, context, lb, xos_lb_id):
        poll_interval = cfg.CONF.xos.request_poll_interval
        poll_timeout = cfg.CONF.xos.request_poll_timeout

        start_dt = datetime.now()
        while (datetime.now() - start_dt).seconds < poll_timeout:
            r = self.driver.load_balancer.get(xos_lb_id)
            xos_lb = r.get('loadbalancer')
            vip_address = xos_lb.get('vip_address')
         
            if self.driver.allocates_vip and vip_address and vip_address != '0.0.0.0':
                lb.vip_port_id = \
                    self._get_vip_port_id(context,
                                          lb.vip_subnet_id,
                                          vip_address)
                lb.vip_address = vip_address
                kwargs = {'lb_create': True}
                self.successful_completion(context, lb, **kwargs)
                return
            time.sleep(poll_interval)

        LOG.debug("Timeout has expired for load balancer {0} to complete an "
                  "operation.".format(lb.id))
        self.failed_completion(context, lb)

    def create_and_allocate_vip(self, context, lb):
        self.create(context, lb)

    def create(self, context, lb):
        vip_network = self._ensure_xos_network(context, lb)
        r = self.driver.client.post(self._url(), self._construct_args(lb, vip_network))
        xos_lb_id = r.get('loadbalancer').get('loadbalancer_id')
        self.driver.plugin.db.update_loadbalancer(
            context, lb.id, {'description': xos_lb_id})

        thread = threading.Thread(target=self._thread_op,
                                  args=(context, lb, xos_lb_id))
        thread.setDaemon(True)
        thread.start()
        LOG.info("created xos loadbalancer :%s", lb.name)

    def delete(self, context, lb):
        self.driver.client.delete(self._url(lb.description))
        self.successful_completion(context, lb, delete=True)

    def update(self, context, old_lb, lb):
        self.driver.client.put(self._url(lb.description),
                               self._construct_args(lb))
        self.successful_completion(context, lb)
        LOG.info("updated xos loadbalancer :%s", lb.name)

    def refresh(self, context, lb):
        pass

    def stats(self, context, lb):
        pass

    def get(self, xos_lb_id):
        return self.driver.client.get(self._url(xos_lb_id))

    def delete_pool(self, lb, xos_pool_id):
        xos_lb_id = lb.description
        xos_lb = self.get(xos_lb_id).get('loadbalancer')
        pools = xos_lb.get('pools')
        if xos_pool_id in pools:
            args = {'pool_id': None}
            self.driver.client.put(self._url(xos_lb_id), args)
        LOG.debug('updated xos lb for removing pool %s', xos_pool_id)

    def delete_listener(self, lb, xos_lsn_id):
        xos_lb_id = lb.description
        xos_lb = self.get(xos_lb_id).get('loadbalancer')
        listeners = xos_lb.get('listeners')
        if xos_lsn_id in listeners:
            args = {'listener_id': None}
            self.driver.client.put(self._url(xos_lb_id), args)
        LOG.debug('updated xos lb for removing listener %s', xos_lsn_id)


class ListenerManager(driver_base.BaseListenerManager):

    @staticmethod
    def _url(id=None):
        s = '/listeners/'
        if id:
            s += '%s/' % id
        return s

    def _construct_args(self, listener):
        args = {
                'name': listener.name,
                'protocol': listener.protocol,
                'protocol_port': listener.protocol_port,
                'stat_port': 10002
            }
        LOG.debug("returning listener args:%s", args)
        return args

    def create(self, context, listener):
        r = self.driver.client.post(self._url(),
                                    self._construct_args(listener))
        xos_listener_name = r.get('listener').get('id')
        xos_listener_id = r.get('listener').get('listener_id')
        self.driver.plugin.db.update_listener(
            context, listener.id, {'description': xos_listener_id,
                                   'name': xos_listener_name})
        self.successful_completion(context, listener)

        updated_lb = self.driver.plugin.db.get_loadbalancer(
            context, listener.loadbalancer_id)
        self.driver.load_balancer.update(context,
                                         listener.root_loadbalancer,
                                         updated_lb)
        LOG.info("created xos listener %s lb.listeners %s",
                 listener.name, updated_lb.listeners[0].name)

    def delete(self, context, listener):
        self.driver.load_balancer.delete_listener(
            listener.loadbalancer, listener.description)
        self.driver.client.delete(self._url(listener.description))
        self.successful_completion(context, listener, delete=True)

    def update(self, context, old_listener, listener):
        pass

    def refresh(self, context, lb):
        pass

    def stats(self, context, lb):
        pass


class PoolManager(driver_base.BasePoolManager):

    @staticmethod
    def _url(id=None):
        s = '/pools/'
        if id:
            s += '%s/' % id
        return s

    def _construct_args(self, pool):
        args = {
            'name': pool.name,
            'lb_algorithm': pool.lb_algorithm,
            'protocol': pool.protocol,
        }
        if pool.healthmonitor and pool.healthmonitor.name:
            try:
                healthmon = {'health_monitor_id': int(pool.healthmonitor.name)}
                args.update(healthmon)
            except ValueError:
                pass
        LOG.debug("returning pool args:%s", args)
        return args

    def create(self, context, pool):
        r = self.driver.client.post(self._url(),
                                    self._construct_args(pool))
        xos_pool_name = r.get('pool').get('id')
        xos_pool_id = r.get('pool').get('pool_id')
        self.driver.plugin.db.update_pool(
            context, pool.id, {'description': xos_pool_id,
                               'name': xos_pool_name})

        self.successful_completion(context, pool)
        updated_lb = self.driver.plugin.db.get_loadbalancer(
            context, pool.loadbalancer_id)
        self.driver.load_balancer.update(context,
                                         pool.root_loadbalancer,
                                         updated_lb)
        LOG.info("created xos pool %s lb.listeners %s lb.pools %s",
                 pool.name, updated_lb.listeners[0].description,
                 updated_lb.pools[0].description)

    def delete(self, context, pool):
        self.driver.load_balancer.delete_pool(pool.loadbalancer, pool.description)
        self.driver.client.delete(self._url(pool.description))
        self.successful_completion(context, pool, delete=True)

    def update(self, context, old_pool, pool):
        self.driver.client.put(self._url(pool.description),
                               self._construct_args(pool))
        self.successful_completion(context, pool)
        LOG.info("updated xos pool %s", pool.name)

    def refresh(self, context, pool):
        pass

    def stats(self, context, pool):
        pass

    def delete_healthmon(self, pool, hm_id):
        xos_pool_id = pool.description
        xos_pool = self.get(xos_pool_id)
        healthmons = xos_pool.get('pool').get('health_monitors')
        if hm_id in healthmons:
            args = {'health_monitor_id': None}
            self.driver.client.put(self._url(xos_pool_id), args)
        LOG.debug('updated pools for removing healthmon %s', hm_id)

    def get(self, xos_pool_id):
        return self.driver.client.get(self._url(xos_pool_id))


class MemberManager(driver_base.BaseMemberManager):

    @staticmethod
    def _url(member, id=None):
        s = '/pools/%s/members/' % member.pool.id
        if id:
            s += '%s/' % id
        return s

    def create(self, context, lb):
            pass

    def delete(self, context, lb):
        pass

    def update(self, context, old_lb, lb):
        pass

    def refresh(self, context, lb):
        pass

    def stats(self, context, lb):
        pass


class HealthMonitorManager(driver_base.BaseHealthMonitorManager):

    @staticmethod
    def _url(id=None):
        s = '/healthmonitors/'
        if id:
            s += '%s/' % id
        return s

    def _construct_args(self, hm):
        args = {
            'name': 'ping',
            'type': hm.type,
            'delay': hm.delay,
            'max_retries': hm.max_retries,
            'timeout': hm.timeout,
        }
        LOG.debug("returning healthmon args:%s", args)
        return args

    def create(self, context, hm):
        r = self.driver.client.post(self._url(),
                                    self._construct_args(hm))
        xos_hm_name = r.get('health_monitor').get('id')
        xos_hm_id = r.get('health_monitor').get('health_monitor_id')
        self.driver.plugin.db.update_healthmonitor(
            context, hm.id, {'name': xos_hm_name,
                             'url_path': xos_hm_id})
        self.successful_completion(context, hm)

        updated_pool = self.driver.plugin.db.get_pool(
            context, hm.pool.id)
        self.driver.pool.update(context, hm.pool, updated_pool)
        LOG.info("created xos healthmon:%s pool.hm %s",
                 xos_hm_name, updated_pool.healthmonitor.name)

    def delete(self, context, hm):
        self.driver.pool.delete_healthmon(hm.pool, hm.url_path)
        self.driver.client.delete(self._url(hm.url_path))
        self.successful_completion(context, hm, delete=True)

    def update(self, context, old_hm, hm):
        pass

    def refresh(self, context, hm):
        pass

    def stats(self, context, hm):
        pass



