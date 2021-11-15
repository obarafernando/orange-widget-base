from PyQt5.QtWidgets import QPlainTextEdit
from orangewidget.settings import Setting
from orangewidget.utils.signals import Output
from orangewidget.widget import OWBaseWidget, Input
from orangewidget import gui
import Orange

class IntuitiveFilterCondition(OWBaseWidget):
    name = "FilterConditionOrange3"
    description = "Write filter condition"
    icon = "icons/Filter.svg"

    class Inputs:
        filter = Input("Filter", str)

    class Outputs:
        filtered = Output("Filtered Data", Orange.data.Table)        

    want_main_area = False

    filter = Setting('')

    def __init__(self):
        super().__init__()
        gui.lineEdit(self.controlArea,self,"filter", "enter a filter condition", box="Filter")
        

    @Inputs.filter
    def set_filter(self, str):
        gui.lineEdit(self.controlArea,self,"filter", "enter a filter condition", box="Filter")

            