"""Account-level services.

A subpackage rather than another flat module because ``services/`` sits at the
folder-size limit. ``services.discover_services()`` imports this package, and
importing the modules below registers their ``@service`` functions.
"""

from services.account import sign_out_svc  # noqa: F401 - registers @service on import
