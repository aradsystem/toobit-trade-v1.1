from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QPushButton, QComboBox, QLineEdit,
    QDoubleSpinBox, QSpinBox, QFormLayout, QMessageBox, QPlainTextEdit,
    QHeaderView, QAbstractItemView, QCompleter, QCheckBox, QGroupBox, QSlider,
)
from PySide6.QtCore import QThreadPool, QTimer, Qt, QStringListModel
from PySide6.QtGui import QColor, QBrush

from core.scanner import scan_from_tickers, scan_period
from core.indicators import enrich_ohlcv, snapshot_from_df
from core.trade_settings import TradeSettings, calc_stop_prices
from core.position_monitor import PositionStateTracker, evaluate_position, position_roe_percent
from ui.workers import Worker
from ui.chart_widget import ChartWidget
from ui.symbol_picker import SymbolPickerDialog
from config import ENABLE_LIVE_TRADING, API_KEY
from ui.theme import STYLE


class Metric(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("Panel")
        lay = QVBoxLayout(self)
        t = QLabel(title)
        t.setObjectName("Muted")
        self.value = QLabel("—")
        self.value.setObjectName("Metric")
        lay.addWidget(t)
        lay.addWidget(self.value)


class MainWindow(QMainWindow):
    def __init__(self, service, store):
        super().__init__()
        self.service = service
        self.store = store
        self.pool = QThreadPool.globalInstance()
        self._workers = []
        self.symbol_list: list = []
        self.current_positions = []
        self.settings = TradeSettings.load()
        self.tracker = PositionStateTracker()
        self.chart_levels = {}  # برای refresh خودکار خطوط

        self.setWindowTitle("Toobit | Trading Terminal")
        self.resize(1520, 940)
        self.setStyleSheet(STYLE)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_dashboard()
        self._build_scanner()
        self._build_positions()
        self._build_trade()
        self._build_chart()
        self._build_settings()
        self._build_log()

        self.statusBar().showMessage("آماده | اتصال بازار عمومی بدون کلید API ممکن است")

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_account)
        self.refresh_timer.start(12000)

        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.run_position_monitor)
        self._restart_monitor_timer()

        self._load_symbols_async()
        self.refresh_account()

    # ------------------------------------------------------------------ helpers
    def panel(self):
        f = QFrame()
        f.setObjectName("Panel")
        return f

    def log(self, msg: str):
        self.log_box.appendPlainText(str(msg))

    def run_worker(self, fn, success, *args, **kwargs):
        worker = Worker(fn, *args, **kwargs)
        self._workers.append(worker)

        def _cleanup():
            try:
                self._workers.remove(worker)
            except ValueError:
                pass

        worker.signals.result.connect(success)
        worker.signals.error.connect(
            lambda e: (self.log(e), self.statusBar().showMessage(e, 10000))
        )
        worker.signals.finished.connect(_cleanup)
        self.pool.start(worker)

    def _restart_monitor_timer(self):
        self.monitor_timer.stop()
        if self.settings.monitor_enabled:
            sec = max(5, int(self.settings.monitor_interval_sec))
            self.monitor_timer.start(sec * 1000)

    # ------------------------------------------------------------------ UI
    def _build_dashboard(self):
        w = QWidget()
        root = QVBoxLayout(w)
        head = QHBoxLayout()
        mapping = {
            "کل موجودی": "total_metric",
            "موجودی آزاد": "free_metric",
            "مارجین مصرف‌شده": "used_metric",
            "تعداد پوزیشن": "count_metric",
        }
        for title, attr in mapping.items():
            m = Metric(title)
            head.addWidget(m)
            setattr(self, attr, m.value)
        root.addLayout(head)
        info = self.panel()
        il = QVBoxLayout(info)
        il.addWidget(QLabel("وضعیت سامانه"))
        self.connection_label = QLabel("در حال بررسی اتصال...")
        il.addWidget(self.connection_label)
        note = QLabel(
            "ورود دستی است. بعد از باز شدن پوزیشن، SL/TP و مانیتور طبق «تنظیمات ترید» اعمال می‌شود."
        )
        note.setWordWrap(True)
        note.setObjectName("Muted")
        il.addWidget(note)
        root.addWidget(info)
        root.addStretch()
        self.tabs.addTab(w, "داشبورد")

    def _build_scanner(self):
        w = QWidget()
        root = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.scan_btn = QPushButton("اسکن بازار")
        self.scan_btn.clicked.connect(self.refresh_scanner)
        self.scan_period = QComboBox()
        for text, data in [
            ("۲۴ ساعت (سریع)", "24h"),
            ("۱ ساعت", "1h"),
            ("۴ ساعت", "4h"),
            ("۱۲ ساعت", "12h"),
            ("۱ هفته", "1w"),
        ]:
            self.scan_period.addItem(text, data)
        self.scan_sort = QComboBox()
        for text, data in [
            ("بیشترین رشد ٪", "change_desc"),
            ("کمترین رشد / بیشترین ریزش ٪", "change_asc"),
            ("بیشترین حجم USDT", "volume_desc"),
            ("کمترین حجم", "volume_asc"),
            ("بیشترین مومنتوم", "momentum_desc"),
            ("کمترین مومنتوم", "momentum_asc"),
        ]:
            self.scan_sort.addItem(text, data)
        self.scan_limit = QSpinBox()
        self.scan_limit.setRange(10, 300)
        self.scan_limit.setValue(50)
        bar.addWidget(self.scan_btn)
        bar.addWidget(QLabel("دوره:"))
        bar.addWidget(self.scan_period)
        bar.addWidget(QLabel("مرتب‌سازی:"))
        bar.addWidget(self.scan_sort)
        bar.addWidget(QLabel("تعداد:"))
        bar.addWidget(self.scan_limit)
        bar.addStretch()
        root.addLayout(bar)
        hint = QLabel(
            "۲۴ساعته سریع است. دوره‌های دیگر روی نمادهای پرحجم از OHLCV محاسبه می‌شوند."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.scan_table = QTableWidget(0, 8)
        self.scan_table.setHorizontalHeaderLabels(
            ["نماد", "آخرین قیمت", "تغییر ٪", "مومنتوم ٪", "حجم USDT", "بالاترین", "پایین‌ترین", "منبع"]
        )
        self.scan_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.scan_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.scan_table.setAlternatingRowColors(True)
        self.scan_table.cellDoubleClicked.connect(self.scanner_selected)
        root.addWidget(self.scan_table)
        self.tabs.addTab(w, "اسکنر بازار")

    def _build_positions(self):
        w = QWidget()
        root = QVBoxLayout(w)
        bar = QHBoxLayout()
        refresh = QPushButton("به‌روزرسانی")
        refresh.clicked.connect(self.refresh_account)
        self.chart_pos_btn = QPushButton("نمایش روی نمودار")
        self.chart_pos_btn.clicked.connect(self.show_selected_on_chart)
        self.close_btn = QPushButton("بستن پوزیشن انتخاب‌شده")
        self.close_btn.clicked.connect(self.close_selected)
        self.sl_btn = QPushButton("تنظیم SL / TP")
        self.sl_btn.clicked.connect(self.set_selected_stops)
        self.margin_btn = QPushButton("افزودن مارجین / Cross راهنما")
        self.margin_btn.clicked.connect(self.add_margin)
        self.scale_in_btn = QPushButton("افزودن به پوزیشن")
        self.scale_in_btn.clicked.connect(lambda: self.scale_in_manual())
        for b in (refresh, self.chart_pos_btn, self.close_btn, self.sl_btn, self.margin_btn, self.scale_in_btn):
            bar.addWidget(b)
        bar.addStretch()
        root.addLayout(bar)
        self.pos_table = QTableWidget(0, 12)
        self.pos_table.setHorizontalHeaderLabels(
            [
                "نماد", "جهت", "قرارداد", "ورود", "قیمت لحظه", "PnL",
                "ROE %", "اهرم", "مارجین", "لیکویید", "فاصله تا لیکویید %", "",
            ]
        )
        self.pos_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pos_table.horizontalHeader().setStretchLastSection(True)
        self.pos_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pos_table.cellDoubleClicked.connect(lambda r, c: self.show_selected_on_chart())
        root.addWidget(self.pos_table)
        note = QLabel(
            "دابل‌کلیک یا دکمه «نمایش روی نمودار» → چارت همان نماد با خطوط ورود / SL / TP / لیکویید / قیمت لحظه."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)
        self.tabs.addTab(w, "پوزیشن‌ها")

    def _build_trade(self):
        w = QWidget()
        root = QVBoxLayout(w)
        panel = self.panel()
        form = QFormLayout(panel)

        sym_row = QHBoxLayout()
        self.trade_symbol = QComboBox()
        self.trade_symbol.setEditable(True)
        self.trade_symbol.setInsertPolicy(QComboBox.NoInsert)
        self.trade_symbol.setMinimumWidth(200)
        completer = QCompleter()
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.trade_symbol.setCompleter(completer)
        self._trade_completer = completer
        pick_btn = QPushButton("لیست بازار…")
        pick_btn.clicked.connect(lambda: self.open_symbol_picker("trade"))
        sym_row.addWidget(self.trade_symbol, 1)
        sym_row.addWidget(pick_btn)
        form.addRow("نماد", sym_row)

        self.trade_side = QComboBox()
        self.trade_side.addItems(["BUY / Long", "SELL / Short"])
        self.trade_leverage = QSpinBox()
        self.trade_leverage.setRange(1, 200)
        self.trade_leverage.setValue(10)

        # اسلایدر درصد از حداکثر قابل باز شدن
        self.trade_pct = QSlider(Qt.Horizontal)
        self.trade_pct.setRange(1, 100)
        self.trade_pct.setValue(25)
        self.trade_pct_label = QLabel("۲۵٪ از حداکثر")
        pct_row = QHBoxLayout()
        pct_row.addWidget(self.trade_pct, 1)
        pct_row.addWidget(self.trade_pct_label)

        self.trade_qty = QDoubleSpinBox()
        self.trade_qty.setDecimals(6)
        self.trade_qty.setRange(0.000001, 1e12)
        self.trade_qty.setValue(0.001)
        self.trade_qty.setToolTip("مقدار واحد پایه؛ با اسلایدر هم پر می‌شود")

        self.trade_meta_label = QLabel("حداکثر قابل باز شدن بعد از انتخاب نماد و اهرم محاسبه می‌شود")
        self.trade_meta_label.setObjectName("Muted")
        self.trade_meta_label.setWordWrap(True)

        self._max_openable_cache = None

        form.addRow("جهت", self.trade_side)
        form.addRow("اهرم", self.trade_leverage)
        form.addRow("درصد از حداکثر", pct_row)
        form.addRow("مقدار (واحد پایه)", self.trade_qty)
        form.addRow(self.trade_meta_label)
        root.addWidget(panel)

        refresh_max_btn = QPushButton("محاسبه مجدد حداکثر (موجودی × اهرم)")
        refresh_max_btn.clicked.connect(self.refresh_max_openable)
        root.addWidget(refresh_max_btn)

        self.trade_symbol.currentTextChanged.connect(self._on_trade_symbol_changed)
        self.trade_leverage.valueChanged.connect(lambda *_: self.refresh_max_openable())
        self.trade_pct.valueChanged.connect(self._on_trade_pct_changed)
        self.trade_qty.valueChanged.connect(lambda *_: self._refresh_qty_hint())

        self.open_trade_btn = QPushButton("ارسال سفارش Market + اعمال تنظیمات")
        self.open_trade_btn.clicked.connect(self.open_manual_trade)
        root.addWidget(self.open_trade_btn)
        warning = QLabel(
            "اسلایدر مثل سایت عمل می‌کند: حداکثر از روی موجودی آزاد USDT × اهرم و سقف بازار همان نماد "
            "حساب می‌شود؛ درصد انتخابی از همان حداکثر به مقدار پایه تبدیل می‌گردد."
        )
        warning.setWordWrap(True)
        warning.setObjectName("Muted")
        root.addWidget(warning)
        root.addStretch()
        self.tabs.addTab(w, "ورود دستی")

    def _build_chart(self):
        w = QWidget()
        root = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.chart_symbol = QComboBox()
        self.chart_symbol.setEditable(True)
        self.chart_symbol.setInsertPolicy(QComboBox.NoInsert)
        self.chart_symbol.setMinimumWidth(200)
        chart_completer = QCompleter()
        chart_completer.setCaseSensitivity(Qt.CaseInsensitive)
        chart_completer.setFilterMode(Qt.MatchContains)
        self.chart_symbol.setCompleter(chart_completer)
        self._chart_completer = chart_completer
        pick_btn = QPushButton("لیست بازار…")
        pick_btn.clicked.connect(lambda: self.open_symbol_picker("chart"))
        self.tf = QComboBox()
        self.tf.addItems(["1m", "5m", "15m", "1h", "4h", "1d"])
        btn = QPushButton("بارگذاری نمودار")
        btn.clicked.connect(lambda: self.load_chart())
        bar.addWidget(QLabel("نماد:"))
        bar.addWidget(self.chart_symbol)
        bar.addWidget(pick_btn)
        bar.addWidget(QLabel("تایم‌فریم:"))
        bar.addWidget(self.tf)
        bar.addWidget(btn)
        bar.addStretch()
        root.addLayout(bar)
        self.level_info = QLabel("")
        self.level_info.setObjectName("Muted")
        self.level_info.setWordWrap(True)
        root.addWidget(self.level_info)
        self.chart = ChartWidget()
        root.addWidget(self.chart)
        self.tabs.addTab(w, "نمودار")

    def _build_settings(self):
        w = QWidget()
        root = QVBoxLayout(w)
        s = self.settings

        g1 = QGroupBox("حد ضرر / حد سود هنگام باز شدن پوزیشن")
        f1 = QFormLayout(g1)
        self.chk_auto_stops = QCheckBox("ست خودکار SL/TP بعد از ورود")
        self.chk_auto_stops.setChecked(s.auto_set_stops)
        self.sl_pct = QDoubleSpinBox()
        self.sl_pct.setRange(0.05, 50)
        self.sl_pct.setDecimals(2)
        self.sl_pct.setValue(s.sl_percent)
        self.tp_pct = QDoubleSpinBox()
        self.tp_pct.setRange(0.05, 200)
        self.tp_pct.setDecimals(2)
        self.tp_pct.setValue(s.tp_percent)
        f1.addRow(self.chk_auto_stops)
        f1.addRow("SL فاصله قیمتی ٪", self.sl_pct)
        f1.addRow("TP فاصله قیمتی ٪", self.tp_pct)
        root.addWidget(g1)

        g2 = QGroupBox("انتقال استاپ به سربه‌سر")
        f2 = QFormLayout(g2)
        self.chk_be = QCheckBox("فعال")
        self.chk_be.setChecked(s.move_sl_to_be)
        self.be_trigger = QDoubleSpinBox()
        self.be_trigger.setRange(0.1, 100)
        self.be_trigger.setValue(s.be_trigger_percent)
        self.be_fee = QDoubleSpinBox()
        self.be_fee.setRange(0, 2)
        self.be_fee.setDecimals(3)
        self.be_fee.setValue(s.be_fee_buffer_percent)
        f2.addRow(self.chk_be)
        f2.addRow("بعد از ٪ حرکت قیمت به نفع پوزیشن", self.be_trigger)
        f2.addRow("بافر کارمزد ٪ روی قیمت ورود", self.be_fee)
        root.addWidget(g2)

        g3 = QGroupBox("محافظ سود قله (Peak Protect)")
        f3 = QFormLayout(g3)
        self.chk_peak = QCheckBox("اگر سود زیاد شد و بعد نصف شد → بستن")
        self.chk_peak.setChecked(s.peak_protect)
        self.peak_min = QDoubleSpinBox()
        self.peak_min.setRange(10, 1000)
        self.peak_min.setValue(s.peak_min_roe_percent)
        self.peak_ratio = QDoubleSpinBox()
        self.peak_ratio.setRange(0.1, 0.95)
        self.peak_ratio.setSingleStep(0.05)
        self.peak_ratio.setDecimals(2)
        self.peak_ratio.setValue(s.peak_drawdown_ratio)
        f3.addRow(self.chk_peak)
        f3.addRow("حداقل ROE قله ٪ برای فعال شدن", self.peak_min)
        f3.addRow("نسبت بستن (مثلاً 0.5 = نصف قله)", self.peak_ratio)
        root.addWidget(g3)

        g4 = QGroupBox("مانیتور")
        f4 = QFormLayout(g4)
        self.chk_monitor = QCheckBox("مانیتور خودکار پوزیشن‌ها")
        self.chk_monitor.setChecked(s.monitor_enabled)
        self.mon_interval = QSpinBox()
        self.mon_interval.setRange(5, 120)
        self.mon_interval.setValue(s.monitor_interval_sec)
        self.chk_snapshot = QCheckBox("ذخیره اسنپ‌شات RSI/MACD/BB هنگام ورود دستی")
        self.chk_snapshot.setChecked(s.log_entry_snapshot)
        f4.addRow(self.chk_monitor)
        f4.addRow("فاصله بررسی (ثانیه)", self.mon_interval)
        f4.addRow(self.chk_snapshot)
        root.addWidget(g4)


        g5 = QGroupBox("افزایش پوزیشن (Scale-in)")
        f5 = QFormLayout(g5)
        self.chk_scale_in = QCheckBox("افزایش خودکار پس از حرکت به نفع پوزیشن")
        self.chk_scale_in.setChecked(s.scale_in_enabled)
        self.scale_trigger = QDoubleSpinBox()
        self.scale_trigger.setRange(0.1, 50)
        self.scale_trigger.setValue(s.scale_in_trigger_percent)
        self.scale_stages = QSpinBox()
        self.scale_stages.setRange(1, 10)
        self.scale_stages.setValue(s.scale_in_stages)
        self.scale_size = QDoubleSpinBox()
        self.scale_size.setRange(5, 200)
        self.scale_size.setValue(s.scale_in_size_percent)
        f5.addRow(self.chk_scale_in)
        f5.addRow("هر مرحله بعد از ٪ حرکت قیمت", self.scale_trigger)
        f5.addRow("تعداد مراحل", self.scale_stages)
        f5.addRow("هر مرحله ٪ از حجم اولیه", self.scale_size)
        root.addWidget(g5)

        save_btn = QPushButton("ذخیره تنظیمات")
        save_btn.clicked.connect(self.save_settings_ui)
        root.addWidget(save_btn)
        root.addStretch()
        self.tabs.addTab(w, "تنظیمات ترید")

    def _build_log(self):
        w = QWidget()
        root = QVBoxLayout(w)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        root.addWidget(self.log_box)
        self.tabs.addTab(w, "گزارش رخدادها")

    def save_settings_ui(self):
        s = self.settings
        s.auto_set_stops = self.chk_auto_stops.isChecked()
        s.sl_percent = float(self.sl_pct.value())
        s.tp_percent = float(self.tp_pct.value())
        s.move_sl_to_be = self.chk_be.isChecked()
        s.be_trigger_percent = float(self.be_trigger.value())
        s.be_fee_buffer_percent = float(self.be_fee.value())
        s.peak_protect = self.chk_peak.isChecked()
        s.peak_min_roe_percent = float(self.peak_min.value())
        s.peak_drawdown_ratio = float(self.peak_ratio.value())
        s.monitor_enabled = self.chk_monitor.isChecked()
        s.monitor_interval_sec = int(self.mon_interval.value())
        s.log_entry_snapshot = self.chk_snapshot.isChecked()
        s.scale_in_enabled = self.chk_scale_in.isChecked()
        s.scale_in_trigger_percent = float(self.scale_trigger.value())
        s.scale_in_stages = int(self.scale_stages.value())
        s.scale_in_size_percent = float(self.scale_size.value())
        s.save()
        self._restart_monitor_timer()
        self.log("تنظیمات ترید ذخیره شد.")
        QMessageBox.information(self, "ذخیره", "تنظیمات ذخیره شد.")

    # ------------------------------------------------------------------ symbols
    def _load_symbols_async(self):
        def task():
            markets = self.service.load_markets()
            symbols = [
                s
                for s, m in markets.items()
                if m.get("swap") and m.get("quote") == "USDT" and m.get("active", True)
            ]
            symbols.sort()
            return symbols

        def done(symbols):
            self.symbol_list = symbols
            model = QStringListModel(symbols)
            self._trade_completer.setModel(model)
            self._chart_completer.setModel(model)
            self.trade_symbol.clear()
            self.chart_symbol.clear()
            self.trade_symbol.addItems(symbols)
            self.chart_symbol.addItems(symbols)
            idx = self.trade_symbol.findText("BTC/USDT:USDT")
            if idx >= 0:
                self.trade_symbol.setCurrentIndex(idx)
                self.chart_symbol.setCurrentIndex(idx)
            self.statusBar().showMessage(f"{len(symbols)} نماد USDT-M بارگذاری شد", 5000)

        self.run_worker(task, done)

    # ------------------------------------------------------------------ scanner
    def refresh_scanner(self):
        self.scan_btn.setEnabled(False)
        period = self.scan_period.currentData()
        sort_by = self.scan_sort.currentData()
        limit = self.scan_limit.value()

        def task():
            if period == "24h" and sort_by in (
                "change_desc", "change_asc", "volume_desc", "volume_asc",
            ):
                return scan_from_tickers(self.service, sort_by=sort_by, limit=limit)
            return scan_period(
                self.service,
                period=period,
                sort_by=sort_by,
                limit=limit,
                universe_size=min(100, max(40, limit * 2)),
            )

        def done(rows):
            self.scan_btn.setEnabled(True)
            self.scan_table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                vals = [
                    r.get("symbol"), r.get("last"), r.get("change"), r.get("momentum"),
                    r.get("quoteVolume"), r.get("high"), r.get("low"),
                    r.get("source") or r.get("period"),
                ]
                for j, v in enumerate(vals):
                    if v is None:
                        text = "—"
                    elif isinstance(v, float):
                        if j in (2, 3):
                            text = f"{v:.2f}"
                        elif j == 4:
                            text = f"{v:,.0f}"
                        else:
                            text = f"{v:.6g}"
                    else:
                        text = str(v)
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignCenter)
                    if j == 2 and isinstance(v, (int, float)):
                        item.setForeground(QBrush(QColor("#22c55e" if v > 0 else "#ef4444")))
                    self.scan_table.setItem(i, j, item)
            self.statusBar().showMessage(f"{len(rows)} نتیجه اسکن", 6000)

        self.run_worker(task, done)

    def scanner_selected(self, row, col):
        item = self.scan_table.item(row, 0)
        if not item:
            return
        sym = item.text()
        self._set_symbol_combos(sym)
        self.tabs.setCurrentIndex(4)
        self.load_chart()

    def _set_symbol_combos(self, sym: str):
        for combo in (self.chart_symbol, self.trade_symbol):
            idx = combo.findText(sym)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setEditText(sym)

    # ------------------------------------------------------------------ account
    def open_symbol_picker(self, target: str = "trade"):
        """لیست نماد با تغییر٪ و حجم و مرتب‌سازی ستونی"""
        def task():
            return scan_from_tickers(self.service, sort_by="volume_desc", limit=500)

        def done(rows):
            dlg = SymbolPickerDialog(rows, self, title="انتخاب نماد از بازار")
            if dlg.exec() and dlg.selected_symbol:
                self._set_symbol_combos(dlg.selected_symbol)
                if target == "chart":
                    self.load_chart()
                elif target == "trade":
                    self._on_trade_symbol_changed(dlg.selected_symbol)

        self.run_worker(task, done)



    def refresh_max_openable(self):
        symbol = (self.trade_symbol.currentText() or "").strip()
        if not symbol or "/" not in symbol:
            return
        lev = int(self.trade_leverage.value())

        def task():
            return self.service.max_openable(symbol, lev)

        def done(info):
            self._max_openable_cache = info
            base = info.get("base") or ""
            mx = float(info.get("max_base") or 0)
            free = float(info.get("free_usdt") or 0)
            last = info.get("last")
            cs = float(info.get("contract_size") or 1)
            self.trade_meta_label.setText(
                f"موجودی آزاد ≈ {free:.4g} USDT  |  قیمت ≈ {last}  |  "
                f"حداکثر قابل باز ≈ {mx:.6g} {base} "
                f"(≈ {mx/cs:.4g} قرارداد) با اهرم {lev}x — "
                f"اگر ۰ است، موجودی/کلید API را چک کنید"
            )
            self._on_trade_pct_changed(self.trade_pct.value())
            # limits for spinbox
            mn = float(info.get("min_base") or cs)
            self.trade_qty.setMinimum(mn)
            if mx > 0:
                self.trade_qty.setMaximum(mx)

        self.run_worker(task, done)

    def _on_trade_pct_changed(self, value: int):
        self.trade_pct_label.setText(f"{int(value)}٪ از حداکثر")
        info = getattr(self, "_max_openable_cache", None)
        if not info:
            return
        mx = float(info.get("max_base") or 0)
        if mx <= 0:
            return
        qty = mx * (int(value) / 100.0)
        mn = float(info.get("min_base") or 0)
        if qty < mn and value > 0:
            qty = mn
        # جلوگیری از سیگنال حلقه‌ای
        self.trade_qty.blockSignals(True)
        self.trade_qty.setValue(qty)
        self.trade_qty.blockSignals(False)
        self._refresh_qty_hint()

    def _refresh_qty_hint(self):
        symbol = (self.trade_symbol.currentText() or "").strip()
        if not symbol or "/" not in symbol:
            return
        try:
            meta = self.service.market_meta(symbol)
            base = meta.get("base") or ""
            cs = float(meta.get("contract_size") or 1)
            mn = meta.get("min_base")
            qty = float(self.trade_qty.value())
            contracts = qty / cs if cs else qty
            mx_lev = meta.get("max_leverage") or 200
            self.trade_meta_label.setText(
                f"حداقل ≈ {mn} {base}  |  هر ۱ قرارداد = {cs} {base}  |  "
                f"اکنون: {qty} {base} ≈ {contracts:.4g} قرارداد  |  سقف اهرم: {mx_lev}x"
            )
        except Exception:
            pass

    def _on_trade_symbol_changed(self, symbol: str = None):
        symbol = (symbol or self.trade_symbol.currentText() or "").strip()
        if not symbol or "/" not in symbol:
            return

        def task():
            return self.service.market_meta(symbol)

        def done(meta):
            mx_lev = meta.get("max_leverage") or 200
            self.trade_leverage.setMaximum(int(mx_lev))
            if self.trade_leverage.value() > mx_lev:
                self.trade_leverage.setValue(int(mx_lev))
            cs = float(meta.get("contract_size") or 0.001)
            self.trade_qty.setSingleStep(cs)
            self.trade_qty.setDecimals(6)
            self.refresh_max_openable()

        self.run_worker(task, done)


    def refresh_account(self):
        if not API_KEY:
            self.connection_label.setText("بازار عمومی: آماده | حساب خصوصی: کلید API تنظیم نشده")
            return

        def done(data):
            balance, positions = data
            total = (balance.get("total") or {}).get("USDT", "—")
            free = (balance.get("free") or {}).get("USDT", "—")
            used = (balance.get("used") or {}).get("USDT", "—")
            self.total_metric.setText(str(total))
            self.free_metric.setText(str(free))
            self.used_metric.setText(str(used))
            active = [p for p in positions if float(p.get("contracts") or 0) != 0]
            self.count_metric.setText(str(len(active)))
            self._show_positions(active)
            self.store.snapshot(active)
            self.connection_label.setText("اتصال خصوصی Toobit برقرار است")
            # اگر چارت روی یک پوزیشن است، خطوط را تازه کن
            if self.chart_levels.get("symbol"):
                self._refresh_levels_from_positions(active)

        self.run_worker(lambda: (self.service.balance(), self.service.positions()), done)

    def _show_positions(self, positions):
        self.current_positions = positions
        self.pos_table.setRowCount(len(positions))
        for i, p in enumerate(positions):
            info = p.get("info") or {}
            entry = p.get("entryPrice") or info.get("avgPrice")
            mark = p.get("markPrice") or info.get("markPrice")
            liq = (
                p.get("liquidationPrice")
                or info.get("liqPrice")
                or info.get("liquidationPrice")
                or info.get("liquidatePrice")
            )
            side = (p.get("side") or "").lower()
            dist = None
            try:
                if entry and mark and liq:
                    e, m, l = float(entry), float(mark), float(liq)
                    if side in ("long", "buy"):
                        # فاصله نسبی تا لیکویید از قیمت فعلی
                        if m != l:
                            dist = abs((m - l) / m) * 100.0
                    else:
                        if m != l:
                            dist = abs((l - m) / m) * 100.0
            except Exception:
                dist = None
            roe = position_roe_percent(p)
            vals = [
                p.get("symbol"), p.get("side"), p.get("contracts"), entry, mark,
                p.get("unrealizedPnl"), f"{roe:.2f}" if roe is not None else "—",
                p.get("leverage"), p.get("initialMargin"), liq,
                f"{dist:.2f}" if dist is not None else "—", "",
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem("—" if v is None else str(v))
                if j == 6 and roe is not None:
                    item.setForeground(QBrush(QColor("#22c55e" if roe >= 0 else "#ef4444")))
                if j == 10 and dist is not None and dist < 5:
                    item.setForeground(QBrush(QColor("#f97316")))
                self.pos_table.setItem(i, j, item)

    def selected_position(self):
        row = self.pos_table.currentRow()
        if row < 0 or row >= len(getattr(self, "current_positions", [])):
            QMessageBox.warning(self, "انتخاب پوزیشن", "ابتدا یک پوزیشن را انتخاب کنید.")
            return None
        return self.current_positions[row]

    def show_selected_on_chart(self):
        p = self.selected_position()
        if not p:
            return
        sym = p.get("symbol")
        if not sym:
            return
        info = p.get("info") or {}
        def pick(*keys):
            for k in keys:
                v = p.get(k)
                if v not in (None, "", 0, "0"):
                    return v
                v = info.get(k)
                if v not in (None, "", 0, "0"):
                    return v
            return None
        levels = {
            "symbol": sym,
            "entry": pick("entryPrice", "avgPrice", "entry_price"),
            "mark": pick("markPrice", "mark_price", "lastPrice"),
            "liquidation": pick("liquidationPrice", "liqPrice", "liquidation_price", "liquidatePrice"),
            "stop_loss": pick("stopLossPrice", "stopLoss", "sl", "stop_loss"),
            "take_profit": pick("takeProfitPrice", "takeProfit", "tp", "take_profit"),
        }
        self.chart_levels = levels
        self._set_symbol_combos(sym)
        # تایم‌فریم پیشنهادی 5m برای مانیتور نزدیک لیکویید
        idx = self.tf.findText("5m")
        if idx >= 0:
            self.tf.setCurrentIndex(idx)
        self.tabs.setCurrentIndex(4)
        self.load_chart(levels=levels)

    def _refresh_levels_from_positions(self, positions):
        sym = self.chart_levels.get("symbol")
        if not sym:
            return
        for p in positions:
            if p.get("symbol") == sym:
                self.chart_levels.update(
                    {
                        "entry": p.get("entryPrice"),
                        "mark": p.get("markPrice"),
                        "liquidation": p.get("liquidationPrice"),
                    }
                )
                # اگر چارت همین نماد است، فقط خطوط را دوباره نکشیم مگر کاربر بخواهد
                break

    def close_selected(self):
        if not ENABLE_LIVE_TRADING:
            QMessageBox.warning(
                self, "معاملات غیرفعال",
                "برای فعال‌سازی، ENABLE_LIVE_TRADING=true را در .env تنظیم کنید.",
            )
            return
        p = self.selected_position()
        if not p:
            return
        total = float(p.get("contracts") or 0)
        if total <= 0:
            QMessageBox.warning(self, "پوزیشن", "حجم پوزیشن نامعتبر است.")
            return

        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        import math

        # گام قرارداد از بازار
        try:
            meta = self.service.market_meta(p.get("symbol"))
            step = float(meta.get("step_contracts") or 1)
        except Exception:
            step = 1.0

        d = QDialog(self)
        d.setWindowTitle("بستن جزئی / کامل پوزیشن")
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel(f"{p.get('symbol')} — حجم کل (قرارداد): {total}"))
        pct = QSlider(Qt.Horizontal)
        pct.setRange(1, 100)
        pct.setValue(100)
        pct_lbl = QLabel("۱۰۰٪")
        amt_lbl = QLabel("")
        lay.addWidget(pct)
        lay.addWidget(pct_lbl)
        lay.addWidget(amt_lbl)

        def round_contracts(raw):
            if step >= 1:
                v = math.floor(raw + 1e-9)
                return float(max(1, v)) if raw >= 1 else (1.0 if raw > 0 else 0.0)
            precision = max(0, int(round(-math.log10(step)))) if 0 < step < 1 else 0
            return float(f"{raw:.{precision}f}")

        def on_pct(v):
            raw = total * (v / 100.0)
            part = round_contracts(raw)
            if v >= 100:
                part = total  # بستن کامل بدون گرد کردن ناقص
            pct_lbl.setText(f"{v}٪")
            amt_lbl.setText(f"ارسال به صرافی: {part} قرارداد از {total}")
        pct.valueChanged.connect(on_pct)
        on_pct(100)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay.addWidget(buttons)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        if d.exec() != QDialog.Accepted:
            return

        v = pct.value()
        raw = total * (v / 100.0)
        part = total if v >= 100 else round_contracts(raw)
        if part <= 0:
            QMessageBox.warning(self, "حجم", "بعد از گرد کردن، مقدار بستن صفر شد. درصد را بالاتر ببرید.")
            return
        side = "sell" if p.get("side") == "long" else "buy"
        ok = QMessageBox.question(
            self, "تأیید بستن",
            f"بستن {v}% از {p.get('symbol')}؟\nارسال واقعی: {part} قرارداد از {total}",
        )
        if ok != QMessageBox.Yes:
            return
        self.run_worker(
            lambda: self.service.close_position_market(p["symbol"], side, float(part)),
            lambda r: (self.log(f"Close sent={part} resp={r}"), self.refresh_account()),
        )


    def set_selected_stops(self):
        if not ENABLE_LIVE_TRADING:
            QMessageBox.warning(
                self, "معاملات غیرفعال",
                "برای فعال‌سازی، ENABLE_LIVE_TRADING=true را در .env تنظیم کنید.",
            )
            return
        p = self.selected_position()
        if not p:
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        d = QDialog(self)
        d.setWindowTitle("تنظیم حد ضرر و حد سود")
        form = QFormLayout(d)
        sl = QDoubleSpinBox()
        sl.setDecimals(10)
        sl.setMaximum(1e12)
        sl.setValue(float(p.get("stopLossPrice") or 0))
        tp = QDoubleSpinBox()
        tp.setDecimals(10)
        tp.setMaximum(1e12)
        tp.setValue(float(p.get("takeProfitPrice") or 0))
        form.addRow("Stop Loss (صفر=بدون تغییر)", sl)
        form.addRow("Take Profit (صفر=بدون تغییر)", tp)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        if d.exec():
            side = "LONG" if p.get("side") == "long" else "SHORT"
            self.run_worker(
                lambda: self.service.set_trading_stop(
                    p["symbol"], side, sl.value() or None, tp.value() or None
                ),
                lambda r: (self.log(str(r)), self.refresh_account()),
            )

    def add_margin(self):
        """در Isolated: افزودن مارجین ایزوله. در Cross: پیشنهاد افزایش حجم پوزیشن."""
        if not ENABLE_LIVE_TRADING:
            QMessageBox.warning(
                self, "معاملات غیرفعال",
                "برای فعال‌سازی، ENABLE_LIVE_TRADING=true را در .env تنظیم کنید.",
            )
            return
        p = self.selected_position()
        if not p:
            return
        mode = (p.get("marginMode") or "").lower()
        if mode == "isolated":
            from PySide6.QtWidgets import QInputDialog
            amount, ok = QInputDialog.getDouble(
                self, "افزودن مارجین ایزوله", "مقدار USDT:", 1, 0.01, 100000, 2
            )
            if ok:
                side = "LONG" if p.get("side") == "long" else "SHORT"
                self.run_worker(
                    lambda: self.service.add_isolated_margin(p["symbol"], side, amount),
                    lambda r: (self.log(str(r)), self.refresh_account()),
                )
            return

        # Cross: مارجین مشترک حساب است — افزایش = باز کردن حجم بیشتر
        ans = QMessageBox.question(
            self,
            "حالت Cross",
            "در حالت Cross مارجین کل حساب مشترک است و «افزودن مارجین ایزوله» معنا ندارد.\n\n"
            "آیا می‌خواهید به پوزیشن حجم اضافه کنید (Scale-in دستی)؟",
        )
        if ans == QMessageBox.Yes:
            self.scale_in_manual(p)

    def scale_in_manual(self, p=None):
        if not ENABLE_LIVE_TRADING:
            QMessageBox.warning(self, "معاملات غیرفعال", "ENABLE_LIVE_TRADING=true لازم است.")
            return
        if p is None:
            p = self.selected_position()
        if not p:
            return
        from PySide6.QtWidgets import QInputDialog
        total = float(p.get("contracts") or 0)
        pct, ok = QInputDialog.getDouble(
            self, "افزودن به پوزیشن",
            f"چند درصد از حجم فعلی ({total} قرارداد) اضافه شود؟",
            50, 1, 500, 1,
        )
        if not ok:
            return
        add = total * (pct / 100.0)
        side = "buy" if p.get("side") == "long" else "sell"
        ok2 = QMessageBox.question(
            self, "تأیید Scale-in",
            f"{p.get('symbol')}\nافزودن ≈ {add:.6g} قرارداد ({pct}% از حجم فعلی)\nجهت: {side}",
        )
        if ok2 != QMessageBox.Yes:
            return
        self.run_worker(
            lambda: self.service.open_additional(p["symbol"], side, add),
            lambda r: (self.log(f"Scale-in: {r}"), self.refresh_account()),
        )


    def open_manual_trade(self):
        if not ENABLE_LIVE_TRADING:
            QMessageBox.warning(
                self, "ارسال سفارش غیرفعال",
                "برای فعال‌سازی، ENABLE_LIVE_TRADING=true را در فایل .env تنظیم کنید.",
            )
            return
        if not API_KEY:
            QMessageBox.warning(self, "API Key", "ابتدا کلید API را در فایل .env وارد کنید.")
            return
        symbol = self.trade_symbol.currentText().strip()
        if not symbol:
            QMessageBox.warning(self, "نماد", "یک نماد انتخاب کنید.")
            return
        side = "buy" if self.trade_side.currentIndex() == 0 else "sell"
        qty = float(self.trade_qty.value())
        try:
            meta = self.service.market_meta(symbol)
            cs = float(meta.get("contract_size") or 1)
            contracts_est = qty / cs
            base_name = meta.get("base") or ""
            conv_line = f"≈ {contracts_est:.6g} قرارداد (هر قرارداد = {cs} {base_name})"
        except Exception:
            conv_line = ""
        answer = QMessageBox.question(
            self, "تأیید سفارش",
            f"سفارش Market؟\n{symbol}\n{side.upper()}\nمقدار پایه: {qty}\n{conv_line}\nاهرم: {self.trade_leverage.value()}x\n\nعملیات واقعی است.",
        )
        if answer != QMessageBox.Yes:
            return
        self.open_trade_btn.setEnabled(False)
        settings = self.settings
        log_snap = settings.log_entry_snapshot

        def task():
            snap = {}
            if log_snap:
                try:
                    for tf in ("1m", "5m", "15m"):
                        rows = self.service.ohlcv(symbol, tf, 120)
                        df = enrich_ohlcv(rows)
                        snap[tf] = snapshot_from_df(df)
                except Exception as e:
                    snap["error"] = str(e)

            self.service.set_leverage(symbol, self.trade_leverage.value())
            order = self.service.create_market_order(symbol, side, qty)

            stop_result = None
            if settings.auto_set_stops:
                # قیمت ورود را از order یا ticker بگیر
                entry = None
                try:
                    entry = float((order or {}).get("average") or (order or {}).get("price") or 0) or None
                except Exception:
                    entry = None
                if entry is None:
                    try:
                        t = self.service.public_tickers().get(symbol) or {}
                        entry = float(t.get("last") or 0) or None
                    except Exception:
                        entry = None
                pos_side = "long" if side == "buy" else "short"
                sl, tp = calc_stop_prices(pos_side, entry or 0, settings)
                if entry and (sl or tp):
                    api_side = "LONG" if pos_side == "long" else "SHORT"
                    stop_result = self.service.set_trading_stop(symbol, api_side, sl, tp)

            return {"order": order, "stops": stop_result, "snapshot": snap, "symbol": symbol}

        def done(result):
            self.open_trade_btn.setEnabled(True)
            self.store.event(symbol, "manual_market_order", result.get("order"))
            if result.get("snapshot"):
                self.store.event(symbol, "entry_snapshot", result["snapshot"])
                self.log(f"Entry snapshot saved for {symbol}")
            if result.get("stops"):
                self.log(f"Auto stops: {result['stops']}")
            self.log(f"Order: {result.get('order')}")
            self.refresh_account()

        worker = Worker(task)
        self._workers.append(worker)

        def _cleanup():
            try:
                self._workers.remove(worker)
            except ValueError:
                pass

        worker.signals.result.connect(done)
        worker.signals.error.connect(
            lambda e: (
                self.open_trade_btn.setEnabled(True),
                self.log(e),
                QMessageBox.critical(self, "خطای سفارش", e),
            )
        )
        worker.signals.finished.connect(_cleanup)
        self.pool.start(worker)

    # ------------------------------------------------------------------ chart
    def load_chart(self, levels=None):
        symbol = self.chart_symbol.currentText().strip()
        timeframe = self.tf.currentText()
        if not symbol:
            QMessageBox.warning(self, "نماد", "نماد را انتخاب کنید.")
            return
        if levels is None:
            levels = dict(self.chart_levels) if self.chart_levels.get("symbol") == symbol else {}

        def done(rows):
            if not rows:
                self.log(f"داده‌ای برای {symbol} دریافت نشد")
                return
            self.store.save_candles(symbol, timeframe, rows)
            df = enrich_ohlcv(rows)
            self.chart.plot_ohlcv(df, levels=levels or None, title=f"{symbol} · {timeframe}")

            parts = []
            if levels:
                for k, title in [
                    ("entry", "ورود"),
                    ("stop_loss", "SL"),
                    ("take_profit", "TP"),
                    ("liquidation", "لیکویید"),
                    ("mark", "لحظه"),
                ]:
                    v = levels.get(k)
                    if v is not None:
                        parts.append(f"{title}={v}")
            self.level_info.setText(" | ".join(parts) if parts else "بدون خطوط سطح پوزیشن")

            if len(df):
                last = df.iloc[-1]
                rsi = last.get("RSI14")
                rsi_txt = f"{float(rsi):.1f}" if rsi == rsi else "—"
                self.log(f"{symbol} {timeframe} | RSI={rsi_txt} | close={last['close']}")
            self.statusBar().showMessage(f"نمودار {symbol} ({timeframe})", 5000)

        self.run_worker(lambda: self.service.ohlcv(symbol, timeframe, 250), done)

    # ------------------------------------------------------------------ monitor
    def run_position_monitor(self):
        if not self.settings.monitor_enabled or not API_KEY or not ENABLE_LIVE_TRADING:
            return

        def task():
            positions = self.service.positions()
            active = [p for p in positions if float(p.get("contracts") or 0) != 0]
            actions = []
            keys = set()
            for p in active:
                keys.add(self.tracker.key(p))
                actions.extend(evaluate_position(p, self.settings, self.tracker))
            self.tracker.cleanup(keys)
            results = []
            for a in actions:
                try:
                    if a["action"] == "close":
                        side = "sell" if a["side"] in ("long", "buy") else "buy"
                        r = self.service.close_position_market(
                            a["symbol"], side, float(a["contracts"])
                        )
                        results.append({"ok": True, "action": a, "result": r})
                    elif a["action"] == "move_sl":
                        api_side = "LONG" if a["side"] in ("long", "buy") else "SHORT"
                        r = self.service.set_trading_stop(
                            a["symbol"], api_side, a.get("stop_loss"), None
                        )
                        results.append({"ok": True, "action": a, "result": r})
                    elif a["action"] == "scale_in":
                        r = self.service.open_additional(
                            a["symbol"], a.get("base_side") or "buy", float(a["contracts"])
                        )
                        results.append({"ok": True, "action": a, "result": r})
                except Exception as e:
                    results.append({"ok": False, "action": a, "error": str(e)})
            return results

        def done(results):
            for r in results or []:
                a = r.get("action") or {}
                if r.get("ok"):
                    self.log(f"[Monitor] {a.get('action')} {a.get('symbol')}: {a.get('reason')}")
                    self.store.event(a.get("symbol") or "", f"monitor_{a.get('action')}", r)
                else:
                    self.log(f"[Monitor ERROR] {a}: {r.get('error')}")
            if results:
                self.refresh_account()

        self.run_worker(task, done)
