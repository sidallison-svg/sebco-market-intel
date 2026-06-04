"""
Entry point for Sebco Market Intel dashboard.

Usage:
    python main.py
        or, if you'd rather drive Streamlit yourself:
    streamlit run app.py
"""

import subprocess
import sys


def main():
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.maxUploadSize", "50"],
        cwd=__import__("os").path.dirname(__import__("os").path.abspath(__file__)),
    )


if __name__ == "__main__":
    main()
