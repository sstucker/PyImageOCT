import os
import sys

from PyQt5 import uic
from PyQt5.QtWidgets import QMainWindow, QWidget, QStatusBar, QTabWidget
from fbs_runtime.application_context.PyQt5 import ApplicationContext

from tabs import TabOCTA

from NpyWriterProcess import *

class MainWindow(QMainWindow):

    def __init__(self):

        super(MainWindow, self).__init__()
        ui = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))+"\\ui\\MainWindow.ui"
        uic.loadUi(ui, self)

        # MainWindow elements
        self.statusBar = self.findChild(QStatusBar, "MainStatusBar")

        # Instantiate tabs
        self._tabs = []

        self._mainTabWidget = self.findChild(QTabWidget, "mainTabWidget")
        self._mainTabWidget.currentChanged.connect(self.tab_changed)

        tab_raster_placeholder = self.findChild(QWidget, "tabRaster")
        tab_raster_placeholder_layout = tab_raster_placeholder.parent().layout()
        self._tabRaster = TabOCTA(parent=self)
        tab_raster_placeholder_layout.replaceWidget(tab_raster_placeholder, self._tabRaster)
        self._tabs.append(self._tabRaster)

        # Init with tab 0 selected
        self._selected_tab = 0
        self._tabs[self._selected_tab].select()

        self.show()

    def closeEvent(self, event):
        for tab in self._tabs:
            tab.close()
        event.accept()
        print("MainWindow closed!")

    def tab_changed(self):
        self._tabs[self._selected_tab].deselect()  # Deselect previously selected tab
        print(self._selected_tab)
        try:
            self._tabs[self._mainTabWidget.currentIndex()].select()  # Select new tab
            self._selected_tab = self._mainTabWidget.currentIndex()
        except IndexError:
            print("Tab", self._selected_tab, "doesn\'t exist!")
            self._mainTabWidget.setCurrentIndex(self._selected_tab)

    def show_status(self, msg, timeout=3000):
        self.statusBar.showMessage(str(msg), timeout)

if __name__ == '__main__':
    appctxt = ApplicationContext()       # 1. Instantiate ApplicationContext
    window = MainWindow()
    # window.resize(860, 920)
    # window.setMinimumSize(860,920)
    # # window.setMaximumSize(860,920)
    window.show()
    exit_code = appctxt.app.exec_()      # 2. Invoke appctxt.app.exec_()
    sys.exit(exit_code)