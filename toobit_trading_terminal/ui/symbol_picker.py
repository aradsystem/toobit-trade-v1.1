"""دیالوگ انتخاب نماد با درصد تغییر، حجم و مرتب‌سازی ستونی"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QAbstractItemView, QLabel,
)
from PySide6.QtCore import Qt, QSortFilterProxyModel, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QColor, QBrush


class SymbolTableModel(QAbstractTableModel):
    HEADERS = ["نماد", "آخرین قیمت", "تغییر ۲۴h ٪", "حجم USDT"]

    def __init__(self, rows: List[Dict[str, Any]] = None):
        super().__init__()
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return r.get("symbol", "")
            if col == 1:
                v = r.get("last")
                return "—" if v is None else f"{v:.6g}"
            if col == 2:
                v = r.get("change")
                return "—" if v is None else f"{v:.2f}"
            if col == 3:
                v = r.get("quoteVolume")
                return "—" if v is None else f"{v:,.0f}"
        if role == Qt.ForegroundRole and col == 2:
            v = r.get("change")
            if isinstance(v, (int, float)):
                return QBrush(QColor("#22c55e" if v >= 0 else "#ef4444"))
        if role == Qt.TextAlignmentRole and col > 0:
            return Qt.AlignCenter
        if role == Qt.UserRole:
            return r
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        reverse = order == Qt.DescendingOrder
        keys = ["symbol", "last", "change", "quoteVolume"]
        key = keys[column]

        def k(r):
            v = r.get(key)
            if v is None:
                return float("-inf") if reverse else float("inf")
            if key == "symbol":
                return str(v)
            try:
                return float(v)
            except Exception:
                return float("-inf")

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=k, reverse=reverse)
        self.layoutChanged.emit()

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def row_at(self, row: int) -> Optional[Dict]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


class SymbolFilterProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._text = ""

    def set_filter_text(self, text: str):
        self._text = (text or "").strip().upper()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._text:
            return True
        model = self.sourceModel()
        idx = model.index(source_row, 0)
        sym = model.data(idx, Qt.DisplayRole) or ""
        return self._text in str(sym).upper()


class SymbolPickerDialog(QDialog):
    def __init__(self, rows: List[Dict[str, Any]], parent=None, title="انتخاب نماد"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 520)
        self.selected_symbol: Optional[str] = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("جستجوی نماد... مثلاً BTC یا DOGE")
        top.addWidget(QLabel("فیلتر:"))
        top.addWidget(self.search)
        layout.addLayout(top)

        self.model = SymbolTableModel(rows)
        self.proxy = SymbolFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.DisplayRole)

        from PySide6.QtWidgets import QTableView
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._accept_current)
        layout.addWidget(self.table)

        # پیش‌فرض مرتب بر اساس حجم نزولی
        self.table.sortByColumn(3, Qt.DescendingOrder)

        btns = QHBoxLayout()
        ok = QPushButton("انتخاب")
        cancel = QPushButton("انصراف")
        ok.clicked.connect(self._accept_current)
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addLayout(btns)

        self.search.textChanged.connect(self.proxy.set_filter_text)
        hint = QLabel("روی عنوان ستون کلیک کنید تا مرتب شود (نماد / قیمت / تغییر٪ / حجم)")
        hint.setStyleSheet("color:#91a2bb;")
        layout.addWidget(hint)

    def _accept_current(self):
        idx = self.table.currentIndex()
        if not idx.isValid():
            return
        src = self.proxy.mapToSource(idx)
        row = self.model.row_at(src.row())
        if row:
            self.selected_symbol = row.get("symbol")
            self.accept()
