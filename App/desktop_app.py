import threading
import time
import webview
import os

# Garante PATH correto quando o EVR Deluxe.app é aberto pelo Finder.
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

from app import app

HOST = "127.0.0.1"
PORT = 5050
URL = f"http://{HOST}:{PORT}"


def run_flask():
    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False
    )


if __name__ == "__main__":
    server_thread = threading.Thread(target=run_flask, daemon=True)
    server_thread.start()

    time.sleep(1.5)

    webview.create_window(
        title="EVR Deluxe",
        url=URL,
        width=1280,
        height=820,
        min_size=(1000, 700),
        resizable=True
    )

    webview.start()
