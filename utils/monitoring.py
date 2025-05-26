"""
Monitoring and Alerting System for Discord Bot
Provides comprehensive monitoring with alerts and health checks
"""

import asyncio
import time
import psutil
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from collections import deque
import discord
from discord.ext import tasks

_log = logging.getLogger(__name__)


@dataclass
class HealthMetric:
    """Individual health metric"""

    name: str
    value: float
    unit: str
    timestamp: datetime = field(default_factory=datetime.now)
    threshold_warning: Optional[float] = None
    threshold_critical: Optional[float] = None
    status: str = "ok"  # ok, warning, critical

    def update_status(self):
        """Update status based on thresholds"""
        if self.threshold_critical and self.value >= self.threshold_critical:
            self.status = "critical"
        elif self.threshold_warning and self.value >= self.threshold_warning:
            self.status = "warning"
        else:
            self.status = "ok"


@dataclass
class SystemHealth:
    """System health snapshot"""

    timestamp: datetime = field(default_factory=datetime.now)
    uptime_seconds: float = 0
    memory_usage_mb: float = 0
    memory_percent: float = 0
    cpu_percent: float = 0
    disk_usage_percent: float = 0
    active_connections: int = 0
    commands_per_minute: float = 0
    errors_per_minute: float = 0
    response_time_ms: float = 0
    cache_hit_rate: float = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)


@dataclass
class Alert:
    """Alert configuration and state"""

    name: str
    condition: Callable[[SystemHealth], bool]
    message: str
    severity: str = "warning"  # info, warning, critical
    cooldown_minutes: int = 15
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0

    def should_trigger(self, health: SystemHealth) -> bool:
        """Check if alert should trigger"""
        if not self.condition(health):
            return False

        # Check cooldown
        if self.last_triggered:
            time_since = datetime.now() - self.last_triggered
            if time_since < timedelta(minutes=self.cooldown_minutes):
                return False

        return True

    def trigger(self):
        """Trigger the alert"""
        self.last_triggered = datetime.now()
        self.trigger_count += 1


class PerformanceMonitor:
    """Monitors bot performance and system metrics"""

    def __init__(self, bot=None):
        self.bot = bot
        self.start_time = time.time()
        self.metrics_history: deque = deque(maxlen=1440)  # 24 hours of minute data
        self.alerts: List[Alert] = []
        self.alert_channel_id: Optional[int] = None
        self.command_counter = 0
        self.error_counter = 0
        self.response_times: deque = deque(maxlen=100)
        self.monitoring_active = False

        self._setup_default_alerts()

    def _setup_default_alerts(self):
        """Setup default system alerts"""
        self.alerts = [
            Alert(
                name="High Memory Usage",
                condition=lambda h: h.memory_percent > 85,
                message="🚨 Memory usage is above 85%",
                severity="warning",
                cooldown_minutes=10,
            ),
            Alert(
                name="Critical Memory Usage",
                condition=lambda h: h.memory_percent > 95,
                message="🔥 CRITICAL: Memory usage is above 95%",
                severity="critical",
                cooldown_minutes=5,
            ),
            Alert(
                name="High CPU Usage",
                condition=lambda h: h.cpu_percent > 80,
                message="⚡ High CPU usage detected (>80%)",
                severity="warning",
                cooldown_minutes=15,
            ),
            Alert(
                name="High Error Rate",
                condition=lambda h: h.errors_per_minute > 5,
                message="❌ High error rate detected (>5 errors/minute)",
                severity="warning",
                cooldown_minutes=10,
            ),
            Alert(
                name="Slow Response Time",
                condition=lambda h: h.response_time_ms > 2000,
                message="🐌 Slow response times detected (>2000ms)",
                severity="warning",
                cooldown_minutes=20,
            ),
            Alert(
                name="Low Cache Hit Rate",
                condition=lambda h: h.cache_hit_rate < 50,
                message="📊 Low cache hit rate detected (<50%)",
                severity="info",
                cooldown_minutes=30,
            ),
        ]

    def set_alert_channel(self, channel_id: int):
        """Set channel for sending alerts"""
        self.alert_channel_id = channel_id
        _log.info(f"Alert channel set to {channel_id}")

    def start_monitoring(self):
        """Start the monitoring loop"""
        if not self.monitoring_active:
            self.monitoring_active = True
            self.monitor_loop.start()
            _log.info("Performance monitoring started")

    def stop_monitoring(self):
        """Stop the monitoring loop"""
        if self.monitoring_active:
            self.monitoring_active = False
            self.monitor_loop.cancel()
            _log.info("Performance monitoring stopped")

    @tasks.loop(minutes=1)
    async def monitor_loop(self):
        """Main monitoring loop - runs every minute"""
        try:
            health = await self.collect_health_metrics()
            self.metrics_history.append(health)

            # Check alerts
            await self.check_alerts(health)

            # Log metrics for debugging
            _log.debug(f"Health metrics: {health.to_dict()}")

        except Exception as e:
            _log.error(f"Error in monitoring loop: {e}")

    async def collect_health_metrics(self) -> SystemHealth:
        """Collect current system health metrics"""
        process = psutil.Process()

        # Memory metrics
        memory_info = process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024
        memory_percent = process.memory_percent()

        # CPU metrics
        cpu_percent = process.cpu_percent()

        # Disk metrics
        disk_usage = psutil.disk_usage("/")
        disk_percent = disk_usage.percent

        # Bot-specific metrics
        uptime = time.time() - self.start_time
        active_connections = len(self.bot.voice_clients) if self.bot else 0

        # Calculate rates
        commands_per_minute = self.command_counter  # Reset counter after reading
        errors_per_minute = self.error_counter  # Reset counter after reading
        self.command_counter = 0
        self.error_counter = 0

        # Response time
        avg_response_time = (
            sum(self.response_times) / len(self.response_times)
            if self.response_times
            else 0
        )

        # Cache hit rate (would need to be passed from cache manager)
        cache_hit_rate = 0  # TODO: Integrate with cache_utils

        return SystemHealth(
            uptime_seconds=uptime,
            memory_usage_mb=memory_mb,
            memory_percent=memory_percent,
            cpu_percent=cpu_percent,
            disk_usage_percent=disk_percent,
            active_connections=active_connections,
            commands_per_minute=commands_per_minute,
            errors_per_minute=errors_per_minute,
            response_time_ms=avg_response_time,
            cache_hit_rate=cache_hit_rate,
        )

    async def check_alerts(self, health: SystemHealth):
        """Check all alerts against current health"""
        for alert in self.alerts:
            if alert.should_trigger(health):
                alert.trigger()
                await self.send_alert(alert, health)

    async def send_alert(self, alert: Alert, health: SystemHealth):
        """Send alert to configured channel"""
        try:
            if not self.bot or not self.alert_channel_id:
                _log.warning(
                    f"Alert triggered but no channel configured: {alert.message}"
                )
                return

            channel = self.bot.get_channel(self.alert_channel_id)
            if not channel:
                _log.error(f"Alert channel {self.alert_channel_id} not found")
                return

            # Create alert embed
            color_map = {
                "info": discord.Color.blue(),
                "warning": discord.Color.orange(),
                "critical": discord.Color.red(),
            }

            embed = discord.Embed(
                title=f"🚨 {alert.name}",
                description=alert.message,
                color=color_map.get(alert.severity, discord.Color.yellow()),
                timestamp=datetime.now(),
            )

            # Add system metrics
            embed.add_field(
                name="System Status",
                value=f"Memory: {health.memory_percent:.1f}%\n"
                f"CPU: {health.cpu_percent:.1f}%\n"
                f"Disk: {health.disk_usage_percent:.1f}%",
                inline=True,
            )

            embed.add_field(
                name="Bot Metrics",
                value=f"Uptime: {health.uptime_seconds/3600:.1f}h\n"
                f"Commands/min: {health.commands_per_minute}\n"
                f"Errors/min: {health.errors_per_minute}",
                inline=True,
            )

            embed.add_field(
                name="Alert Info",
                value=f"Severity: {alert.severity.upper()}\n"
                f"Trigger count: {alert.trigger_count}",
                inline=True,
            )

            await channel.send(embed=embed)
            _log.warning(f"Alert sent: {alert.name}")

        except Exception as e:
            _log.error(f"Failed to send alert: {e}")

    def record_command_execution(self, execution_time_ms: float = None):
        """Record command execution metrics"""
        self.command_counter += 1
        if execution_time_ms:
            self.response_times.append(execution_time_ms)

    def record_error(self):
        """Record error occurrence"""
        self.error_counter += 1

    def get_health_summary(self) -> Dict[str, Any]:
        """Get current health summary"""
        if not self.metrics_history:
            return {"status": "no_data"}

        latest = self.metrics_history[-1]

        # Calculate trends (last 10 minutes)
        recent_metrics = list(self.metrics_history)[-10:]
        if len(recent_metrics) > 1:
            memory_trend = (
                recent_metrics[-1].memory_percent - recent_metrics[0].memory_percent
            )
            cpu_trend = recent_metrics[-1].cpu_percent - recent_metrics[0].cpu_percent
        else:
            memory_trend = cpu_trend = 0

        return {
            "status": "healthy",
            "uptime_hours": latest.uptime_seconds / 3600,
            "memory_usage": {
                "current_percent": latest.memory_percent,
                "current_mb": latest.memory_usage_mb,
                "trend": memory_trend,
            },
            "cpu_usage": {"current_percent": latest.cpu_percent, "trend": cpu_trend},
            "performance": {
                "commands_per_minute": latest.commands_per_minute,
                "errors_per_minute": latest.errors_per_minute,
                "avg_response_time_ms": latest.response_time_ms,
                "cache_hit_rate": latest.cache_hit_rate,
            },
            "connections": {"voice_clients": latest.active_connections},
            "alerts": {
                "total_triggered": sum(alert.trigger_count for alert in self.alerts),
                "recent_alerts": [
                    {
                        "name": alert.name,
                        "last_triggered": (
                            alert.last_triggered.isoformat()
                            if alert.last_triggered
                            else None
                        ),
                        "count": alert.trigger_count,
                    }
                    for alert in self.alerts
                    if alert.trigger_count > 0
                ],
            },
        }

    def get_metrics_export(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Export metrics for external monitoring systems"""
        cutoff = datetime.now() - timedelta(hours=hours)

        return [
            health.to_dict()
            for health in self.metrics_history
            if health.timestamp >= cutoff
        ]


class HealthChecker:
    """Health check endpoints and utilities"""

    def __init__(self, bot, monitor: PerformanceMonitor):
        self.bot = bot
        self.monitor = monitor

    async def basic_health_check(self) -> Dict[str, Any]:
        """Basic health check for load balancers"""
        try:
            # Check Discord connection
            discord_ok = self.bot.is_ready() and not self.bot.is_closed()

            # Check recent metrics
            recent_health = None
            if self.monitor.metrics_history:
                recent_health = self.monitor.metrics_history[-1]
                metrics_ok = (
                    recent_health.memory_percent < 90
                    and recent_health.cpu_percent < 90
                    and recent_health.errors_per_minute < 10
                )
            else:
                metrics_ok = True

            overall_healthy = discord_ok and metrics_ok

            return {
                "status": "healthy" if overall_healthy else "unhealthy",
                "timestamp": datetime.now().isoformat(),
                "checks": {
                    "discord_connection": discord_ok,
                    "system_metrics": metrics_ok,
                    "uptime_seconds": time.time() - self.monitor.start_time,
                },
                "details": recent_health.to_dict() if recent_health else {},
            }

        except Exception as e:
            _log.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
            }

    async def detailed_health_check(self) -> Dict[str, Any]:
        """Detailed health check with full metrics"""
        basic = await self.basic_health_check()
        summary = self.monitor.get_health_summary()

        return {**basic, "detailed_metrics": summary}


# Global monitor instance
performance_monitor: Optional[PerformanceMonitor] = None


def init_monitoring(bot, alert_channel_id: int = None) -> PerformanceMonitor:
    """Initialize performance monitoring"""
    global performance_monitor
    performance_monitor = PerformanceMonitor(bot)

    if alert_channel_id:
        performance_monitor.set_alert_channel(alert_channel_id)

    return performance_monitor


def start_monitoring():
    """Start monitoring if initialized"""
    if performance_monitor:
        performance_monitor.start_monitoring()


def stop_monitoring():
    """Stop monitoring if active"""
    if performance_monitor:
        performance_monitor.stop_monitoring()


def record_command(execution_time_ms: float = None):
    """Record command execution"""
    if performance_monitor:
        performance_monitor.record_command_execution(execution_time_ms)


def record_error():
    """Record error occurrence"""
    if performance_monitor:
        performance_monitor.record_error()


def get_health_summary() -> Dict[str, Any]:
    """Get current health summary"""
    if performance_monitor:
        return performance_monitor.get_health_summary()
    return {"status": "monitoring_not_initialized"}
