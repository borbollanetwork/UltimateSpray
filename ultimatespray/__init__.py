"""UltimateSpray - rotating source-IP proxies via AWS API Gateway.

A modernized fork of FireProx (Black Hills Information Security), focused on
password spraying and high-volume web requests where rotating the source IP
address matters.
"""

__version__ = "2.0.0"

from ultimatespray.core import UltimateSpray

__all__ = ["UltimateSpray", "__version__"]
