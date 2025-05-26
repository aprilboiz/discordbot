"""
Enhanced .env file loader with validation and fallback mechanisms
Supports multiple .env files, variable expansion, and type conversion
"""

import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union, List
import re
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass
class EnvConfig:
    """Environment configuration with validation"""
    
    # Discord Bot Configuration
    discord_token: str
    command_prefix: str = "?"
    
    # Performance Settings
    max_voice_connections: int = 50
    api_timeout: int = 30
    connection_pool_size: int = 100
    
    # Logging Configuration
    log_level: str = "INFO"
    log_format: str = "detailed"
    enable_file_logging: bool = True
    log_rotation_size: str = "10MB"
    log_retention_days: int = 7
    
    # Cache Settings
    cache_ttl: int = 300
    max_cache_size: int = 1000
    enable_redis: bool = False
    redis_url: Optional[str] = None
    
    # Monitoring & Alerts
    enable_monitoring: bool = True
    alert_channel_id: Optional[int] = None
    health_check_interval: int = 60
    
    # Development Settings
    dev_mode: bool = False
    debug_guilds: Optional[List[int]] = None
    admin_user_ids: Optional[List[int]] = None
    
    # Security Settings
    rate_limit_per_user: int = 60
    rate_limit_per_command: int = 10
    enable_encryption: bool = False
    
    # External Services
    youtube_api_key: Optional[str] = None
    spotify_client_id: Optional[str] = None
    spotify_client_secret: Optional[str] = None
    soundcloud_client_id: Optional[str] = None
    
    def __post_init__(self):
        if self.debug_guilds is None:
            self.debug_guilds = []
        if self.admin_user_ids is None:
            self.admin_user_ids = []


class EnvLoader:
    """Enhanced .env file loader with validation and type conversion"""
    
    def __init__(self, env_files: List[str] = None):
        self.env_files = env_files or [".env", ".env.local", ".env.production"]
        self.loaded_vars: Dict[str, str] = {}
        self.validation_errors: List[str] = []
        
    def load_env_files(self) -> bool:
        """Load environment variables from multiple .env files"""
        loaded_any = False
        
        for env_file in self.env_files:
            if self._load_single_env_file(env_file):
                loaded_any = True
                
        # Also load from system environment (highest priority)
        self.loaded_vars.update(dict(os.environ))
        
        if not loaded_any:
            _log.warning("No .env files found, using system environment only")
            
        return loaded_any
    
    def _load_single_env_file(self, file_path: str) -> bool:
        """Load a single .env file"""
        path = Path(file_path)
        
        if not path.exists():
            _log.debug(f"Env file not found: {file_path}")
            return False
            
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            parsed_vars = self._parse_env_content(content)
            self.loaded_vars.update(parsed_vars)
            
            _log.info(f"Loaded {len(parsed_vars)} variables from {file_path}")
            return True
            
        except Exception as e:
            _log.error(f"Failed to load {file_path}: {e}")
            return False
    
    def _parse_env_content(self, content: str) -> Dict[str, str]:
        """Parse .env file content with variable expansion"""
        variables = {}
        
        for line_num, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
                
            # Parse KEY=VALUE format
            if '=' not in line:
                _log.warning(f"Invalid line format at line {line_num}: {line}")
                continue
                
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            
            # Remove quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
                
            # Expand variables (${VAR} or $VAR format)
            value = self._expand_variables(value, variables)
            
            variables[key] = value
            
        return variables
    
    def _expand_variables(self, value: str, current_vars: Dict[str, str]) -> str:
        """Expand environment variables in value"""
        # Pattern for ${VAR} or $VAR
        pattern = r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)'
        
        def replace_var(match):
            var_name = match.group(1) or match.group(2)
            
            # Check current variables first, then system env
            if var_name in current_vars:
                return current_vars[var_name]
            elif var_name in os.environ:
                return os.environ[var_name]
            else:
                _log.warning(f"Undefined variable: {var_name}")
                return match.group(0)  # Return original if not found
                
        return re.sub(pattern, replace_var, value)
    
    def get_env_config(self) -> EnvConfig:
        """Convert loaded environment variables to EnvConfig"""
        try:
            config = EnvConfig(
                # Required fields
                discord_token=self._get_required_str("DISCORD_TOKEN"),
                
                # Optional fields with defaults
                command_prefix=self._get_str("COMMAND_PREFIX", "?"),
                max_voice_connections=self._get_int("MAX_VOICE_CONNECTIONS", 50),
                api_timeout=self._get_int("API_TIMEOUT", 30),
                connection_pool_size=self._get_int("CONNECTION_POOL_SIZE", 100),
                
                log_level=self._get_str("LOG_LEVEL", "INFO"),
                log_format=self._get_str("LOG_FORMAT", "detailed"),
                enable_file_logging=self._get_bool("ENABLE_FILE_LOGGING", True),
                log_rotation_size=self._get_str("LOG_ROTATION_SIZE", "10MB"),
                log_retention_days=self._get_int("LOG_RETENTION_DAYS", 7),
                
                cache_ttl=self._get_int("CACHE_TTL", 300),
                max_cache_size=self._get_int("MAX_CACHE_SIZE", 1000),
                enable_redis=self._get_bool("ENABLE_REDIS", False),
                redis_url=self._get_str("REDIS_URL"),
                
                enable_monitoring=self._get_bool("ENABLE_MONITORING", True),
                alert_channel_id=self._get_int("ALERT_CHANNEL_ID"),
                health_check_interval=self._get_int("HEALTH_CHECK_INTERVAL", 60),
                
                dev_mode=self._get_bool("DEV_MODE", False),
                debug_guilds=self._get_int_list("DEBUG_GUILDS"),
                admin_user_ids=self._get_int_list("ADMIN_USER_IDS"),
                
                rate_limit_per_user=self._get_int("RATE_LIMIT_PER_USER", 60),
                rate_limit_per_command=self._get_int("RATE_LIMIT_PER_COMMAND", 10),
                enable_encryption=self._get_bool("ENABLE_ENCRYPTION", False),
                
                youtube_api_key=self._get_str("YOUTUBE_API_KEY"),
                spotify_client_id=self._get_str("SPOTIFY_CLIENT_ID"),
                spotify_client_secret=self._get_str("SPOTIFY_CLIENT_SECRET"),
                soundcloud_client_id=self._get_str("SOUNDCLOUD_CLIENT_ID"),
            )
            
            self._validate_config(config)
            return config
            
        except Exception as e:
            _log.error(f"Failed to create config: {e}")
            raise
    
    def _get_required_str(self, key: str) -> str:
        """Get required string environment variable"""
        value = self.loaded_vars.get(key)
        if not value:
            error = f"Required environment variable '{key}' is not set"
            self.validation_errors.append(error)
            raise ValueError(error)
        return value
    
    def _get_str(self, key: str, default: str = None) -> Optional[str]:
        """Get string environment variable with default"""
        return self.loaded_vars.get(key, default)
    
    def _get_int(self, key: str, default: int = None) -> Optional[int]:
        """Get integer environment variable with default"""
        value = self.loaded_vars.get(key)
        if value is None:
            return default
            
        try:
            return int(value)
        except ValueError:
            error = f"Invalid integer value for '{key}': {value}"
            self.validation_errors.append(error)
            _log.warning(error)
            return default
    
    def _get_bool(self, key: str, default: bool = None) -> Optional[bool]:
        """Get boolean environment variable with default"""
        value = self.loaded_vars.get(key)
        if value is None:
            return default
            
        return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
    
    def _get_int_list(self, key: str) -> List[int]:
        """Get comma-separated integer list"""
        value = self.loaded_vars.get(key)
        if not value:
            return []
            
        try:
            return [int(x.strip()) for x in value.split(',') if x.strip()]
        except ValueError:
            error = f"Invalid integer list for '{key}': {value}"
            self.validation_errors.append(error)
            _log.warning(error)
            return []
    
    def _validate_config(self, config: EnvConfig) -> None:
        """Validate configuration values"""
        # Validate Discord token format
        if len(config.discord_token) < 50:
            self.validation_errors.append("Discord token appears to be invalid (too short)")
        
        # Validate log level
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if config.log_level.upper() not in valid_levels:
            self.validation_errors.append(f"Invalid log level: {config.log_level}")
        
        # Validate numeric ranges
        if config.max_voice_connections < 1 or config.max_voice_connections > 100:
            self.validation_errors.append("max_voice_connections must be between 1 and 100")
            
        if config.api_timeout < 5 or config.api_timeout > 300:
            self.validation_errors.append("api_timeout must be between 5 and 300 seconds")
        
        # Log validation errors
        if self.validation_errors:
            for error in self.validation_errors:
                _log.error(f"Config validation error: {error}")
    
    def create_env_template(self, file_path: str = ".env.template") -> None:
        """Create a template .env file with all available options"""
        template_content = """# Discord Bot Configuration
DISCORD_TOKEN=your_bot_token_here
COMMAND_PREFIX=?

# Performance Settings
MAX_VOICE_CONNECTIONS=50
API_TIMEOUT=30
CONNECTION_POOL_SIZE=100

# Logging Configuration
LOG_LEVEL=INFO
LOG_FORMAT=detailed
ENABLE_FILE_LOGGING=true
LOG_ROTATION_SIZE=10MB
LOG_RETENTION_DAYS=7

# Cache Settings
CACHE_TTL=300
MAX_CACHE_SIZE=1000
ENABLE_REDIS=false
# REDIS_URL=redis://localhost:6379

# Monitoring & Alerts
ENABLE_MONITORING=true
# ALERT_CHANNEL_ID=123456789
HEALTH_CHECK_INTERVAL=60

# Development Settings
DEV_MODE=false
# DEBUG_GUILDS=123456789,987654321
# ADMIN_USER_IDS=123456789,987654321

# Security Settings
RATE_LIMIT_PER_USER=60
RATE_LIMIT_PER_COMMAND=10
ENABLE_ENCRYPTION=false

# External Services (Optional)
# YOUTUBE_API_KEY=your_youtube_api_key
# SPOTIFY_CLIENT_ID=your_spotify_client_id
# SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
# SOUNDCLOUD_CLIENT_ID=your_soundcloud_client_id

# Variable expansion example:
# DATABASE_URL=postgresql://user:pass@localhost:5432/${DB_NAME}
# DB_NAME=discord_bot
"""
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(template_content)
            _log.info(f"Created .env template at {file_path}")
        except Exception as e:
            _log.error(f"Failed to create .env template: {e}")
    
    def get_validation_errors(self) -> List[str]:
        """Get list of validation errors"""
        return self.validation_errors.copy()
    
    def print_loaded_config(self, mask_sensitive: bool = True) -> None:
        """Print loaded configuration for debugging"""
        _log.info("Loaded environment configuration:")
        
        sensitive_keys = {'token', 'secret', 'key', 'password', 'api_key'}
        
        for key, value in sorted(self.loaded_vars.items()):
            if mask_sensitive and any(sensitive in key.lower() for sensitive in sensitive_keys):
                _log.info(f"  {key}=***masked***")
            else:
                _log.info(f"  {key}={value}")


# Global instance
env_loader = EnvLoader()


def load_environment() -> EnvConfig:
    """Load and return environment configuration"""
    env_loader.load_env_files()
    return env_loader.get_env_config()


def create_env_template() -> None:
    """Create .env template file"""
    env_loader.create_env_template()


def validate_environment() -> bool:
    """Validate current environment configuration"""
    try:
        env_loader.load_env_files()
        env_loader.get_env_config()
        errors = env_loader.get_validation_errors()
        
        if errors:
            _log.error("Environment validation failed:")
            for error in errors:
                _log.error(f"  - {error}")
            return False
            
        _log.info("Environment validation passed")
        return True
        
    except Exception as e:
        _log.error(f"Environment validation failed: {e}")
        return False 