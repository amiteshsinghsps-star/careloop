"""alexa-mcp-preflight — connect to a remote CareLoop-style MCP endpoint the way an
Alexa+ skill would, and report which contract and safety requirements actually hold.

Stdlib only. The checks are independent re-implementations on purpose: the tool does
not import the server's own policy code, so a bug in the server's masking cannot hide
itself from the audit.
"""

__version__ = "1.0.0"
