import sys
import traceback

def main():
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtCore import Qt
        from core.exchange import ExchangeService
        from core.database import Store
        from ui.main_window import MainWindow
    except Exception as e:
        print("خطا در import ماژول‌ها:")
        traceback.print_exc()
        try:
            input("Enter را بزنید تا بسته شود...")
        except Exception:
            pass
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)

    try:
        service = ExchangeService()
        store = Store()
        window = MainWindow(service, store)
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        traceback.print_exc()
        try:
            QMessageBox.critical(
                None,
                "خطای راه‌اندازی",
                f"برنامه به خاطر خطا بسته شد:\n\n{type(e).__name__}: {e}\n\n"
                "جزئیات در پنجره کنسول (اگر از ترمینال اجرا کرده‌اید) چاپ شده است.",
            )
        except Exception:
            pass
        try:
            input("Enter را بزنید تا بسته شود...")
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
