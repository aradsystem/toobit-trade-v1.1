"""نمودار pyqtgraph با خطوط سطح خوانا و قیمت لحظه برجسته"""
from __future__ import annotations

from typing import Optional, Dict, Any, List
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout
from PySide6.QtGui import QColor, QPicture, QPainter, QPen, QBrush, QFont
from PySide6.QtCore import QPointF, QRectF, Qt
import pyqtgraph as pg


class CandlestickItem(pg.GraphicsObject):
    def __init__(self, data, width=0.55):
        super().__init__()
        self.data = data or []
        self.width = width
        self.picture = None
        self.generatePicture()

    def generatePicture(self):
        self.picture = QPicture()
        if not self.data:
            return
        p = QPainter(self.picture)
        w = float(self.width)
        half = w / 2.0
        for x, o, c, low, high in self.data:
            x, o, c, low, high = map(float, (x, o, c, low, high))
            up = c >= o
            color = QColor("#0ecb81") if up else QColor("#f6465d")
            p.setPen(QPen(color, 1.2))
            p.drawLine(QPointF(x, low), QPointF(x, high))
            p.setBrush(QBrush(color))
            p.setPen(QPen(color, 0.6))
            top, bottom = max(o, c), min(o, c)
            h = max(top - bottom, abs(high - low) * 0.0015 + 1e-12)
            if abs(top - bottom) < 1e-14:
                p.drawLine(QPointF(x - half, o), QPointF(x + half, o))
            else:
                p.drawRect(QRectF(x - half, bottom, w, h))
        p.end()

    def paint(self, painter, option, widget):
        if self.picture:
            painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        if not self.data:
            return QRectF()
        xs = [float(d[0]) for d in self.data]
        lows = [float(d[3]) for d in self.data]
        highs = [float(d[4]) for d in self.data]
        pad = float(self.width)
        return QRectF(min(xs) - pad, min(lows), max(xs) - min(xs) + 2 * pad, max(highs) - min(lows) or 1e-8)


class ChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # نوار قیمت‌های مهم — همیشه خوانا بالای چارت
        self.banner = QLabel("قیمت‌ها پس از بارگذاری نمودار / پوزیشن نمایش داده می‌شوند")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            "background:#111c2e;color:#e2e8f0;padding:8px 12px;border-radius:8px;"
            "font-size:12px;border:1px solid #263449;"
        )
        root.addWidget(self.banner)

        pg.setConfigOptions(antialias=True)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("#0b1220")
        root.addWidget(self.glw, 1)

        self.price_plot = self.glw.addPlot(row=0, col=0)
        self.price_plot.showGrid(x=True, y=True, alpha=0.12)
        self.price_plot.setLabel("left", "قیمت")
        self.price_plot.addLegend(offset=(10, 10))
        self.price_plot.setClipToView(True)

        self.glw.nextRow()
        self.vol_plot = self.glw.addPlot(row=1, col=0)
        self.vol_plot.setMaximumHeight(90)
        self.vol_plot.showGrid(x=True, y=True, alpha=0.1)
        self.vol_plot.setLabel("left", "حجم")
        self.vol_plot.setXLink(self.price_plot)

        self._level_lines: List = []
        self._level_labels: List = []
        self._n = 0

        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#94a3b8", style=Qt.DashLine, width=1))
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#94a3b8", style=Qt.DashLine, width=1))
        self.price_plot.addItem(self.vLine, ignoreBounds=True)
        self.price_plot.addItem(self.hLine, ignoreBounds=True)
        self.proxy = pg.SignalProxy(
            self.price_plot.scene().sigMouseMoved, rateLimit=30, slot=self._mouse_moved
        )
        self.cursor_label = pg.TextItem(color="#f8fafc", anchor=(0, 1))
        self.price_plot.addItem(self.cursor_label)

    def _mouse_moved(self, evt):
        pos = evt[0]
        if not self.price_plot.sceneBoundingRect().contains(pos):
            return
        mouse = self.price_plot.vb.mapSceneToView(pos)
        self.vLine.setPos(mouse.x())
        self.hLine.setPos(mouse.y())
        self.cursor_label.setText(f"  {mouse.y():,.4f}")
        self.cursor_label.setPos(mouse.x(), mouse.y())

    def clear_levels(self):
        for ln in self._level_lines:
            try:
                self.price_plot.removeItem(ln)
            except Exception:
                pass
        for lb in self._level_labels:
            try:
                self.price_plot.removeItem(lb)
            except Exception:
                pass
        self._level_lines.clear()
        self._level_labels.clear()

    def add_level(self, price, color, label, style=Qt.SolidLine, width=2.5):
        if price is None:
            return
        try:
            price = float(price)
        except (TypeError, ValueError):
            return
        pen = pg.mkPen(color, width=width, style=style)
        line = pg.InfiniteLine(pos=price, angle=0, movable=False, pen=pen)
        self.price_plot.addItem(line)
        self._level_lines.append(line)
        # برچسب سمت چپ نمودار تا خوانا باشد
        txt = pg.TextItem(f"{label} {price:,.4f}", color=color, anchor=(0, 0.5))
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        txt.setFont(font)
        txt.setPos(0, price)
        self.price_plot.addItem(txt)
        self._level_labels.append(txt)

    def _update_banner(self, levels: Optional[Dict[str, Any]], last_close=None):
        parts = []
        mapping = [
            ("mark", "لحظه", "#f8fafc"),
            ("entry", "ورود", "#3b82f6"),
            ("stop_loss", "SL", "#f6465d"),
            ("take_profit", "TP", "#0ecb81"),
            ("liquidation", "لیکویید", "#f97316"),
        ]
        for key, title, color in mapping:
            v = (levels or {}).get(key)
            if v is not None:
                try:
                    parts.append(f'<span style="color:{color};font-weight:700">{title}: {float(v):,.4f}</span>')
                except Exception:
                    pass
        if last_close is not None and not (levels or {}).get("mark"):
            parts.insert(0, f'<span style="color:#f8fafc;font-weight:700">آخرین کندل: {float(last_close):,.4f}</span>')
        if not parts:
            self.banner.setText("بدون سطح پوزیشن — از تب پوزیشن‌ها «نمایش روی نمودار» را بزنید")
        else:
            self.banner.setText("  |  ".join(parts))

    def plot_ohlcv(self, df, levels: Optional[Dict[str, Any]] = None, title: str = ""):
        self.price_plot.clear()
        self.vol_plot.clear()
        self.clear_levels()
        self.price_plot.addItem(self.vLine, ignoreBounds=True)
        self.price_plot.addItem(self.hLine, ignoreBounds=True)
        self.price_plot.addItem(self.cursor_label)
        self.price_plot.addLegend(offset=(10, 10))

        if df is None or getattr(df, "empty", True):
            self._update_banner(levels)
            return

        n = len(df)
        self._n = n
        body_w = 0.5 if n > 100 else 0.6
        x = list(range(n))

        candles = [
            (i, float(r.open), float(r.close), float(r.low), float(r.high))
            for i, r in enumerate(df.itertuples())
        ]
        self.price_plot.addItem(CandlestickItem(candles, width=body_w))

        if "EMA20" in df.columns:
            self.price_plot.plot(x, df["EMA20"].tolist(), pen=pg.mkPen("#38bdf8", width=1.6), name="EMA20")
        if "EMA50" in df.columns:
            self.price_plot.plot(x, df["EMA50"].tolist(), pen=pg.mkPen("#f59e0b", width=1.6), name="EMA50")

        vols = df["volume"].astype(float).tolist()
        colors = [
            QColor("#0ecb81") if float(r.close) >= float(r.open) else QColor("#f6465d")
            for r in df.itertuples()
        ]
        self.vol_plot.addItem(pg.BarGraphItem(x=x, height=vols, width=body_w * 0.85, brushes=colors))

        lows = float(df["low"].min())
        highs = float(df["high"].max())
        extra = []
        if levels:
            for k in ("entry", "stop_loss", "take_profit", "liquidation", "mark"):
                v = levels.get(k)
                if v is not None:
                    try:
                        extra.append(float(v))
                    except Exception:
                        pass
        if extra:
            lows = min(lows, min(extra))
            highs = max(highs, max(extra))
        pad = (highs - lows) * 0.1 or highs * 0.01
        self.price_plot.setYRange(lows - pad, highs + pad, padding=0)
        self.price_plot.setXRange(-1, n + 1, padding=0)

        if levels:
            mapping = [
                ("liquidation", "#f97316", "Liq", Qt.DashDotLine, 3.0),
                ("stop_loss", "#f6465d", "SL", Qt.DashLine, 2.6),
                ("take_profit", "#0ecb81", "TP", Qt.DashLine, 2.6),
                ("entry", "#3b82f6", "Entry", Qt.SolidLine, 2.4),
                ("mark", "#ffffff", "Mark", Qt.SolidLine, 2.0),
            ]
            for key, color, label, style, width in mapping:
                self.add_level(levels.get(key), color, label, style=style, width=width)

        last_close = float(df.iloc[-1]["close"]) if n else None
        self._update_banner(levels, last_close)
        if title:
            self.price_plot.setTitle(title, color="#94a3b8", size="11pt")
