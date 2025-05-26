"""
Design Patterns Module
Provides common design patterns for the Discord bot
"""

from .observe import Observer, Observable
from .singleton import SingletonMeta

__all__ = ['Observer', 'Observable', 'SingletonMeta']
