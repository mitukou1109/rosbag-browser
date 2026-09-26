from __future__ import annotations

import socket
import sys
from threading import Thread
import time
import traceback

import uvicorn


STARTUP_TIMEOUT_SECONDS = 10


def main() -> None:
    try:
        from app.main import APP_DIR, app

        server, server_thread, server_socket = _start_server(app)
        port = server_socket.getsockname()[1]
        try:
            _run_window(f"http://127.0.0.1:{port}", app_dir=APP_DIR)
        finally:
            server.should_exit = True
            server_thread.join(timeout=STARTUP_TIMEOUT_SECONDS)
            server_socket.close()
    except Exception as exc:
        traceback.print_exc()
        _show_startup_error(exc)
        raise


def _run_window(
    url: str, *, app_dir, close_after_ms: int | None = None
) -> None:
    from PyQt6 import sip
    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication, QMainWindow

    application = QApplication(sys.argv)
    application.setApplicationName("rosbag Browser")
    application.setDesktopFileName("rosbag-browser")
    application.setWindowIcon(QIcon(str(app_dir / "static" / "rosbag-browser.svg")))

    window = QMainWindow()
    window.setWindowTitle("rosbag Browser")
    window.resize(1280, 800)
    window.setMinimumSize(800, 600)

    browser = QWebEngineView(window)
    browser.setUrl(QUrl(url))
    window.setCentralWidget(browser)
    window.show()
    if close_after_ms is not None:
        QTimer.singleShot(close_after_ms, window.close)

    try:
        application.exec()
    finally:
        page = browser.page()
        if not sip.isdeleted(page):
            sip.delete(page)
        if not sip.isdeleted(browser):
            sip.delete(browser)
        if not sip.isdeleted(window):
            sip.delete(window)


def _start_server(app: object) -> tuple[uvicorn.Server, Thread, socket.socket]:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_thread = Thread(
        target=server.run,
        kwargs={"sockets": [server_socket]},
        name="rosbag-browser-server",
        daemon=True,
    )
    server_thread.start()

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if not server_thread.is_alive():
            server_socket.close()
            raise RuntimeError("Failed to start the rosbag Browser server")
        if time.monotonic() >= deadline:
            server.should_exit = True
            server_thread.join(timeout=1)
            server_socket.close()
            raise RuntimeError("Timed out while starting the rosbag Browser server")
        time.sleep(0.01)

    return server, server_thread, server_socket


def _show_startup_error(exc: Exception) -> None:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    application = QApplication.instance() or QApplication(sys.argv)
    message_box = QMessageBox()
    message_box.setIcon(QMessageBox.Icon.Critical)
    message_box.setWindowTitle("rosbag Browser の起動に失敗しました")
    message_box.setText("アプリケーションを起動できませんでした。")
    message_box.setInformativeText(f"{type(exc).__name__}: {exc}")
    message_box.exec()


if __name__ == "__main__":
    main()
