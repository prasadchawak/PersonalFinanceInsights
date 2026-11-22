import os
import webbrowser
from threading import Timer

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PFI_PORT") or os.environ.get("TALLY_PORT") or "8765")
    url = f"http://127.0.0.1:{port}"
    if (os.environ.get("PFI_NO_BROWSER") or os.environ.get("TALLY_NO_BROWSER")) != "1":
        Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Personal Finance Insights is running at {url}  (only this computer can reach it). Press Ctrl+C to stop.")
    # 127.0.0.1 = loopback only. Nothing on your network can connect.
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")
