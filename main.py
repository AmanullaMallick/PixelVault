"""Entry point for `flet build apk` (Flet looks for main.py in the project root).

The real app is in android/main.py. Importing it starts the Flet app.
"""

import android.main  # noqa: F401
