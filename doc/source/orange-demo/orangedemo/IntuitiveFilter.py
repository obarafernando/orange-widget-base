from orangewidget.widget import OWBaseWidget, Output, Input
from orangewidget import gui
from orangewidget.settings import Setting
import Orange.data

class Intfilter(OWBaseWidget):
    # Widget's name as displayed in the canvas
    name = "FilterOrange3"
    # Short widget description
    description = "Lets the user input a filter"
    # An icon resource file path for this widget
    # (a path relative to the module where this widget is defined)
    icon = "icons/Filter.svg"

    # Widget's outputs; here, a single output named "filter", of type int
    class Outputs:
        filter0 = Output(f'Filter1', Orange.data.Table)
        filter1 = Output(f'Filter2', Orange.data.Table)
        filter2 = Output(f'Filter3', Orange.data.Table)
        filter3 = Output(f'Filter4', Orange.data.Table)
        filter4 = Output(f'Filter5', Orange.data.Table)

    class Inputs:
        data = Input('Data', Orange.data.Table)    

    want_main_area = False

    Filter1 = Setting('')
    Filter2 = Setting('')
    Filter3 = Setting('')
    Filter4 = Setting('')
    Filter5 = Setting('')


    def __init__(self):
        super().__init__()           

        gui.lineEdit(self.controlArea,self,"Filter1", f'Condicao do filtro 1')
        gui.lineEdit(self.controlArea,self,"Filter2", f'Condicao do filtro 2')
        gui.lineEdit(self.controlArea,self,"Filter3", f'Condicao do filtro 3')
        gui.lineEdit(self.controlArea,self,"Filter4", f'Condicao do filtro 4')
        gui.lineEdit(self.controlArea,self,"Filter5", f'Condicao do filtro 5')
    

    @Inputs.data
    def do_nothing(self,data):
        return