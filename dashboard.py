"""
Shim for the Streamlit Cloud deployment.

Cloud is configured to run `dashboard.py` (set in the share.streamlit.io
app settings before the v2 rebuild). The real entry point is `app.py`;
this file just forwards to it so the deploy keeps working without
touching the Cloud configuration.

If you update the Cloud setting's "Main file path" to `app.py`, this
shim can be safely deleted.
"""

import runpy

runpy.run_path("app.py", run_name="__main__")
