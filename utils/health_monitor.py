"""
Health Monitoring System for Discord Music Bot
Provides health checks, metrics endpoints, and alerting
"""

import asyncio
import logging
import time
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import psutil
import aiohttp
from aiohttp import web
import weakref

_log = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health status information"""
    
    service_name: str
    status: str  # healthy, degraded, unhealthy
    last_check: float
    response_time_ms: float = 0.0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertConfig:
    """Alert configuration"""
    
    name: str
    condition: str  # python expression
    threshold: float
    duration_seconds: int = 60
    cooldown_seconds: int = 300
    webhook_url: Optional[str] = None
    enabled: bool = True


class HealthChecker:
    """Individual health check implementation"""
    
    def __init__(self, name: str, check_func: Callable, interval: int = 30):
        self.name = name
        self.check_func = check_func
        self.interval = interval
        self.last_status: Optional[HealthStatus] = None
        self.check_history: List[HealthStatus] = []
        self.max_history = 100
        
    async def run_check(self) -> HealthStatus:
        """Run the health check"""
        start_time = time.time()
        
        try:
            result = await self.check_func()
            response_time = (time.time() - start_time) * 1000
            
            if isinstance(result, dict):
                status = HealthStatus(
                    service_name=self.name,
                    status=result.get('status', 'healthy'),
                    last_check=time.time(),
                    response_time_ms=response_time,
                    error_message=result.get('error'),
                    metadata=result.get('metadata', {})
                )
            else:
                status = HealthStatus(
                    service_name=self.name,
                    status='healthy' if result else 'unhealthy',
                    last_check=time.time(),
                    response_time_ms=response_time
                )
            
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            status = HealthStatus(
                service_name=self.name,
                status='unhealthy',
                last_check=time.time(),
                response_time_ms=response_time,
                error_message=str(e)
            )
            _log.error(f"Health check failed for {self.name}: {e}")
        
        self.last_status = status
        self.check_history.append(status)
        
        # Trim history
        if len(self.check_history) > self.max_history:
            self.check_history.pop(0)
        
        return status


class AlertManager:
    """Manages alerts and notifications"""
    
    def __init__(self):
        self.alerts: Dict[str, AlertConfig] = {}
        self.alert_states: Dict[str, Dict[str, Any]] = {}
        self.notification_handlers: List[Callable] = []
        
    def add_alert(self, alert: AlertConfig) -> None:
        """Add an alert configuration"""
        self.alerts[alert.name] = alert
        self.alert_states[alert.name] = {
            'triggered': False,
            'trigger_time': None,
            'last_notification': None,
            'trigger_count': 0
        }
        _log.info(f"Added alert: {alert.name}")
    
    def add_notification_handler(self, handler: Callable) -> None:
        """Add a notification handler"""
        self.notification_handlers.append(handler)
    
    async def check_alerts(self, metrics: Dict[str, Any]) -> None:
        """Check all alerts against current metrics"""
        for alert_name, alert in self.alerts.items():
            if not alert.enabled:
                continue
                
            try:
                await self._check_single_alert(alert, metrics)
            except Exception as e:
                _log.error(f"Error checking alert {alert_name}: {e}")
    
    async def _check_single_alert(self, alert: AlertConfig, metrics: Dict[str, Any]) -> None:
        """Check a single alert"""
        state = self.alert_states[alert.name]
        current_time = time.time()
        
        try:
            # Evaluate alert condition
            condition_met = eval(alert.condition, {"__builtins__": {}}, metrics)
            
            if condition_met:
                if not state['triggered']:
                    # First time triggering
                    state['trigger_time'] = current_time
                    state['triggered'] = True
                    _log.warning(f"Alert triggered: {alert.name}")
                
                # Check if alert should fire (duration threshold met)
                if (current_time - state['trigger_time']) >= alert.duration_seconds:
                    # Check cooldown
                    if (state['last_notification'] is None or 
                        (current_time - state['last_notification']) >= alert.cooldown_seconds):
                        
                        await self._send_alert_notification(alert, metrics)
                        state['last_notification'] = current_time
                        state['trigger_count'] += 1
            else:
                if state['triggered']:
                    _log.info(f"Alert resolved: {alert.name}")
                    state['triggered'] = False
                    state['trigger_time'] = None
                    
        except Exception as e:
            _log.error(f"Error evaluating alert condition for {alert.name}: {e}")
    
    async def _send_alert_notification(self, alert: AlertConfig, metrics: Dict[str, Any]) -> None:
        """Send alert notification"""
        notification_data = {
            'alert_name': alert.name,
            'condition': alert.condition,
            'threshold': alert.threshold,
            'current_metrics': metrics,
            'timestamp': datetime.now().isoformat(),
            'trigger_count': self.alert_states[alert.name]['trigger_count']
        }
        
        # Send to all notification handlers
        for handler in self.notification_handlers:
            try:
                await handler(notification_data)
            except Exception as e:
                _log.error(f"Error sending notification: {e}")
        
        # Send webhook if configured
        if alert.webhook_url:
            await self._send_webhook_notification(alert.webhook_url, notification_data)
    
    async def _send_webhook_notification(self, webhook_url: str, data: Dict[str, Any]) -> None:
        """Send webhook notification"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data) as response:
                    if response.status == 200:
                        _log.info("Webhook notification sent successfully")
                    else:
                        _log.warning(f"Webhook notification failed: {response.status}")
        except Exception as e:
            _log.error(f"Error sending webhook notification: {e}")


class HealthMonitor:
    """Main health monitoring system"""
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.health_checkers: Dict[str, HealthChecker] = {}
        self.alert_manager = AlertManager()
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.monitoring_task: Optional[asyncio.Task] = None
        self.start_time = time.time()
        
    def add_health_check(self, name: str, check_func: Callable, interval: int = 30) -> None:
        """Add a health check"""
        checker = HealthChecker(name, check_func, interval)
        self.health_checkers[name] = checker
        _log.info(f"Added health check: {name} (interval: {interval}s)")
    
    def add_alert(self, alert: AlertConfig) -> None:
        """Add an alert"""
        self.alert_manager.add_alert(alert)
    
    async def start_monitoring(self) -> None:
        """Start the health monitoring system"""
        # Setup web server
        await self._setup_web_server()
        
        # Start monitoring loop
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        
        # Setup default health checks
        await self._setup_default_health_checks()
        
        # Setup default alerts
        self._setup_default_alerts()
        
        _log.info(f"Health monitoring started on port {self.port}")
    
    async def stop_monitoring(self) -> None:
        """Stop the health monitoring system"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            
        if self.site:
            await self.site.stop()
            
        if self.runner:
            await self.runner.cleanup()
            
        _log.info("Health monitoring stopped")
    
    async def _setup_web_server(self) -> None:
        """Setup the web server for health endpoints"""
        self.app = web.Application()
        
        # Add routes
        self.app.router.add_get('/health', self._health_endpoint)
        self.app.router.add_get('/health/detailed', self._detailed_health_endpoint)
        self.app.router.add_get('/metrics', self._metrics_endpoint)
        self.app.router.add_get('/status', self._status_endpoint)
        
        # Start server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        
        self.site = web.TCPSite(self.runner, 'localhost', self.port)
        await self.site.start()
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop"""
        while True:
            try:
                # Run all health checks
                for checker in self.health_checkers.values():
                    asyncio.create_task(checker.run_check())
                
                # Get current metrics
                metrics = await self._collect_metrics()
                
                # Check alerts
                await self.alert_manager.check_alerts(metrics)
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                _log.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(30)  # Wait longer on error
    
    async def _setup_default_health_checks(self) -> None:
        """Setup default health checks"""
        
        # System health check
        async def system_health():
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            status = 'healthy'
            if cpu_percent > 90 or memory.percent > 90 or disk.percent > 90:
                status = 'degraded'
            if cpu_percent > 95 or memory.percent > 95 or disk.percent > 95:
                status = 'unhealthy'
            
            return {
                'status': status,
                'metadata': {
                    'cpu_percent': cpu_percent,
                    'memory_percent': memory.percent,
                    'disk_percent': disk.percent
                }
            }
        
        # Discord connection health check
        async def discord_health():
            # This would check Discord connection status
            # Placeholder implementation
            return {'status': 'healthy', 'metadata': {'latency_ms': 50}}
        
        # Database health check (if applicable)
        async def database_health():
            # This would check database connection
            # Placeholder implementation
            return {'status': 'healthy'}
        
        self.add_health_check('system', system_health, 30)
        self.add_health_check('discord', discord_health, 60)
        self.add_health_check('database', database_health, 60)
    
    def _setup_default_alerts(self) -> None:
        """Setup default alerts"""
        
        # High CPU usage alert
        cpu_alert = AlertConfig(
            name='high_cpu',
            condition='system_cpu_percent > 90',
            threshold=90.0,
            duration_seconds=120,
            cooldown_seconds=600
        )
        
        # High memory usage alert
        memory_alert = AlertConfig(
            name='high_memory',
            condition='system_memory_percent > 90',
            threshold=90.0,
            duration_seconds=120,
            cooldown_seconds=600
        )
        
        # Discord connection alert
        discord_alert = AlertConfig(
            name='discord_unhealthy',
            condition='discord_status != "healthy"',
            threshold=0,
            duration_seconds=60,
            cooldown_seconds=300
        )
        
        self.add_alert(cpu_alert)
        self.add_alert(memory_alert)
        self.add_alert(discord_alert)
    
    async def _collect_metrics(self) -> Dict[str, Any]:
        """Collect current metrics"""
        metrics = {
            'timestamp': time.time(),
            'uptime_seconds': time.time() - self.start_time
        }
        
        # Add health check results
        for name, checker in self.health_checkers.items():
            if checker.last_status:
                status = checker.last_status
                metrics[f'{name}_status'] = status.status
                metrics[f'{name}_response_time_ms'] = status.response_time_ms
                
                # Add metadata
                for key, value in status.metadata.items():
                    metrics[f'{name}_{key}'] = value
        
        # Add system metrics
        try:
            cpu_percent = psutil.cpu_percent()
            memory = psutil.virtual_memory()
            
            metrics.update({
                'system_cpu_percent': cpu_percent,
                'system_memory_percent': memory.percent,
                'system_memory_used_mb': memory.used / 1024 / 1024,
                'system_memory_available_mb': memory.available / 1024 / 1024
            })
        except Exception as e:
            _log.warning(f"Failed to collect system metrics: {e}")
        
        return metrics
    
    async def _health_endpoint(self, request: web.Request) -> web.Response:
        """Simple health endpoint"""
        overall_status = 'healthy'
        
        for checker in self.health_checkers.values():
            if checker.last_status and checker.last_status.status != 'healthy':
                overall_status = 'unhealthy'
                break
        
        status_code = 200 if overall_status == 'healthy' else 503
        
        return web.json_response({
            'status': overall_status,
            'timestamp': datetime.now().isoformat()
        }, status=status_code)
    
    async def _detailed_health_endpoint(self, request: web.Request) -> web.Response:
        """Detailed health endpoint"""
        health_data = {
            'overall_status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': time.time() - self.start_time,
            'checks': {}
        }
        
        overall_healthy = True
        
        for name, checker in self.health_checkers.items():
            if checker.last_status:
                status = checker.last_status
                health_data['checks'][name] = {
                    'status': status.status,
                    'last_check': datetime.fromtimestamp(status.last_check).isoformat(),
                    'response_time_ms': status.response_time_ms,
                    'error_message': status.error_message,
                    'metadata': status.metadata
                }
                
                if status.status != 'healthy':
                    overall_healthy = False
            else:
                health_data['checks'][name] = {
                    'status': 'unknown',
                    'error_message': 'No check results available'
                }
                overall_healthy = False
        
        health_data['overall_status'] = 'healthy' if overall_healthy else 'unhealthy'
        status_code = 200 if overall_healthy else 503
        
        return web.json_response(health_data, status=status_code)
    
    async def _metrics_endpoint(self, request: web.Request) -> web.Response:
        """Metrics endpoint"""
        metrics = await self._collect_metrics()
        return web.json_response(metrics)
    
    async def _status_endpoint(self, request: web.Request) -> web.Response:
        """Status endpoint with alert information"""
        status_data = {
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': time.time() - self.start_time,
            'health_checks': len(self.health_checkers),
            'alerts': {
                'total': len(self.alert_manager.alerts),
                'active': sum(1 for state in self.alert_manager.alert_states.values() if state['triggered']),
                'details': {}
            }
        }
        
        # Add alert details
        for name, state in self.alert_manager.alert_states.items():
            status_data['alerts']['details'][name] = {
                'triggered': state['triggered'],
                'trigger_count': state['trigger_count'],
                'last_notification': state['last_notification']
            }
        
        return web.json_response(status_data)


# Global health monitor
health_monitor: Optional[HealthMonitor] = None


def init_health_monitoring(port: int = 8080) -> HealthMonitor:
    """Initialize health monitoring"""
    global health_monitor
    health_monitor = HealthMonitor(port)
    return health_monitor


def get_health_monitor() -> Optional[HealthMonitor]:
    """Get global health monitor"""
    return health_monitor


async def start_health_monitoring(port: int = 8080) -> None:
    """Start health monitoring system"""
    monitor = init_health_monitoring(port)
    await monitor.start_monitoring()


async def stop_health_monitoring() -> None:
    """Stop health monitoring system"""
    if health_monitor:
        await health_monitor.stop_monitoring() 