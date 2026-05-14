"""
Entry point for Sebco Market Intel dashboard.

Usage:
    streamlit run main.py
"""

import subprocess
import sys


def main():
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "dashboard.py",
         "--server.maxUploadSize", "50"],
        cwd=__import__("os").path.dirname(__import__("os").path.abspath(__file__)),
    )


if __name__ == "__main__":
    main()
