"""Anomaly scenario registry.

Stub for PR1 (foundation + normal-mode engine). The self-registering
``Scenario`` dataclass, ``register()`` decorator, and the four anomaly
mutators (memory_leak, cpu_spike, disk_fill, service_down) land in PR2.

``SCENARIOS`` is intentionally empty here so ``generator.py`` has a real
import target for its dispatch lookup: any scenario name other than
``"normal"`` will fail with a clear ``ValueError`` until PR2 registers it.
"""

from __future__ import annotations

SCENARIOS: dict[str, object] = {}
