"""
Configuration and Secrets Management for Discord Bot
Provides secure handling of environment variables, secrets, and configuration
"""

import os
import logging
import json
from typing import Optional, Dict, Any, Union
from pathlib import Path
from dataclasses import dataclass
from cryptography.fernet import Fernet
import base64

_log = logging.getLogger(__name__)


@dataclass
class BotConfig:
    """Bot configuration data class"""

    token: str
    command_prefix: str = "?"
    log_level: str = "INFO"
    max_voice_connections: int = 5
    api_timeout: int = 30
    enable_dev_mode: bool = False
    error_channel_id: Optional[int] = None
    admin_user_ids: list = None

    def __post_init__(self):
        if self.admin_user_ids is None:
            self.admin_user_ids = []


class SecureConfigManager:
    """Manages bot configuration and secrets securely"""

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(exist_ok=True)
        self._config: Optional[BotConfig] = None
        self._encryption_key: Optional[bytes] = None

    def _get_encryption_key(self) -> bytes:
        """Get or create encryption key for sensitive data"""
        if self._encryption_key is None:
            key_file = self.config_dir / ".key"

            if key_file.exists():
                with open(key_file, "rb") as f:
                    self._encryption_key = f.read()
            else:
                self._encryption_key = Fernet.generate_key()
                with open(key_file, "wb") as f:
                    f.write(self._encryption_key)
                # Make key file read-only
                os.chmod(key_file, 0o600)
                _log.info("Created new encryption key")

        return self._encryption_key

    def encrypt_value(self, value: str) -> str:
        """Encrypt a sensitive value"""
        key = self._get_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(value.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt_value(self, encrypted_value: str) -> str:
        """Decrypt a sensitive value"""
        key = self._get_encryption_key()
        f = Fernet(key)
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_value.encode())
        return f.decrypt(encrypted_bytes).decode()

    def get_env_var(
        self, key: str, default: Optional[str] = None, required: bool = False
    ) -> Optional[str]:
        """Get environment variable with validation"""
        value = os.getenv(key, default)

        if required and value is None:
            raise ValueError(f"Required environment variable '{key}' is not set")

        if value and key.lower() in ["token", "secret", "key", "password"]:
            # Mask sensitive values in logs
            _log.debug(f"Retrieved sensitive env var: {key}=***masked***")
        else:
            _log.debug(f"Retrieved env var: {key}={value}")

        return value

    def load_config(self) -> BotConfig:
        """Load bot configuration from environment and config files"""
        if self._config is not None:
            return self._config

        # Load from environment variables
        config_data = {
            "token": self.get_env_var("DISCORD_TOKEN", required=True),
            "command_prefix": self.get_env_var("COMMAND_PREFIX", "?"),
            "log_level": self.get_env_var("LOG_LEVEL", "INFO"),
            "max_voice_connections": int(
                self.get_env_var("MAX_VOICE_CONNECTIONS", "5")
            ),
            "api_timeout": int(self.get_env_var("API_TIMEOUT", "30")),
            "enable_dev_mode": self.get_env_var("DEV_MODE", "false").lower() == "true",
            "error_channel_id": self._parse_int_env("ERROR_CHANNEL_ID"),
            "admin_user_ids": self._parse_list_env("ADMIN_USER_IDS"),
        }

        # Load additional config from file if exists
        config_file = self.config_dir / "bot_config.json"
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    file_config = json.load(f)
                    config_data.update(file_config)
                _log.info(f"Loaded additional config from {config_file}")
            except Exception as e:
                _log.warning(f"Failed to load config file {config_file}: {e}")

        self._config = BotConfig(**config_data)
        _log.info("Bot configuration loaded successfully")
        return self._config

    def _parse_int_env(self, key: str) -> Optional[int]:
        """Parse integer environment variable"""
        value = self.get_env_var(key)
        if value:
            try:
                return int(value)
            except ValueError:
                _log.warning(f"Invalid integer value for {key}: {value}")
        return None

    def _parse_list_env(self, key: str) -> list:
        """Parse comma-separated list environment variable"""
        value = self.get_env_var(key)
        if value:
            try:
                return [int(x.strip()) for x in value.split(",") if x.strip()]
            except ValueError:
                _log.warning(f"Invalid list value for {key}: {value}")
        return []

    def save_config_template(self):
        """Save a configuration template file"""
        template_file = self.config_dir / "bot_config.template.json"
        template = {
            "command_prefix": "?",
            "log_level": "INFO",
            "max_voice_connections": 5,
            "api_timeout": 30,
            "enable_dev_mode": False,
            "error_channel_id": None,
            "admin_user_ids": [],
        }

        with open(template_file, "w") as f:
            json.dump(template, f, indent=2)

        _log.info(f"Saved config template to {template_file}")

    def validate_config(self) -> bool:
        """Validate current configuration"""
        try:
            config = self.load_config()

            # Validate token format (basic check)
            if not config.token or len(config.token) < 50:
                _log.error("Invalid Discord token format")
                return False

            # Validate log level
            valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            if config.log_level.upper() not in valid_log_levels:
                _log.error(f"Invalid log level: {config.log_level}")
                return False

            # Validate numeric values
            if config.max_voice_connections < 1:
                _log.error("max_voice_connections must be at least 1")
                return False

            if config.api_timeout < 5:
                _log.error("api_timeout must be at least 5 seconds")
                return False

            _log.info("Configuration validation passed")
            return True

        except Exception as e:
            _log.error(f"Configuration validation failed: {e}")
            return False

    def get_api_config(self) -> Dict[str, Any]:
        """Get API-specific configuration"""
        return {
            "timeout": self._config.api_timeout if self._config else 30,
            "user_agent": "Discord Bot/1.0",
            "max_retries": 3,
            "backoff_factor": 2,
        }

    def is_admin_user(self, user_id: int) -> bool:
        """Check if user is an admin"""
        config = self.load_config()
        return user_id in config.admin_user_ids

    def is_dev_mode(self) -> bool:
        """Check if development mode is enabled"""
        config = self.load_config()
        return config.enable_dev_mode


# Global config manager instance
config_manager = SecureConfigManager()


def get_config() -> BotConfig:
    """Get bot configuration"""
    return config_manager.load_config()


def get_token() -> str:
    """Get Discord bot token"""
    return config_manager.get_env_var("DISCORD_TOKEN", required=True)


def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return config_manager.is_admin_user(user_id)


def is_development() -> bool:
    """Check if running in development mode"""
    return config_manager.is_dev_mode()
