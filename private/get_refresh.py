"""One-off helper: obtain a DMDATA refresh token for your own DM-DSS account.

Set WEBQUAKE_DMDATA_CLIENT_ID and WEBQUAKE_DMDATA_CLIENT_SECRET first, then:

    python get_refresh.py

A browser opens for you to authorise the application. The refresh token it
prints is what WEBQUAKE_DMDATA_REFRESH_TOKEN expects. It is a long-lived
credential with full access to your paid subscription - treat it as a password,
and never commit it.
"""

import sys
import dmdws

if not dmdws.client_id or not dmdws.client_secret:
    sys.exit(
        "Set WEBQUAKE_DMDATA_CLIENT_ID and WEBQUAKE_DMDATA_CLIENT_SECRET first.\n"
        "Create an OAuth application at https://manager.dmdata.jp/ to get them."
    )

print(dmdws.Connection.get_refresh_token())
