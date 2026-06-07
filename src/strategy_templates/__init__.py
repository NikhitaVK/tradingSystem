# Strategy template library — see registry.py for the playbook.
from src.strategy_templates.registry import get_templates_for_regime, get_templates_by_mechanism

__all__ = ["get_templates_for_regime", "get_templates_by_mechanism"]
