"""Obtain a refresh token for your DMDATA account: python get_refresh.py.

Set WEBQUAKE_DMDATA_CLIENT_ID and WEBQUAKE_DMDATA_CLIENT_SECRET first.
Browser authorisation prints a token for WEBQUAKE_DMDATA_REFRESH_TOKEN.
This long-lived token grants subscription access; keep it secret."""

import sys
import dmdws

if not dmdws.client_id or not dmdws.client_secret:
    sys.exit(
        "Set WEBQUAKE_DMDATA_CLIENT_ID and WEBQUAKE_DMDATA_CLIENT_SECRET first.\n"
        "Create an OAuth application at https://manager.dmdata.jp/ to get them."
    )

print(dmdws.Connection.get_refresh_token())
