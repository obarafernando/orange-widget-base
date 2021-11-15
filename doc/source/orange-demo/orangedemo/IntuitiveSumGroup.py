# [start-snippet-1]
from orangewidget.settings import Setting
import Orange.data
from orangewidget.widget import OWBaseWidget, Input, Output
from orangewidget.utils.widgetpreview import WidgetPreview
from orangewidget import gui

class IntuitiveSumGroup(OWBaseWidget):
    name = "SumGroup" 
    description = "Intuitive Sum Group"
    icon = "icons/SumGroup.svg"
    priority = 10

    class Inputs:
        data = Input("Data", Orange.data.Table)

    class Outputs:
        SumGroup = Output("Sum Grouped Data", Orange.data.Table)

    want_main_area = False

    AtributoAgrupamento = Setting('')
    AtributoSoma = Setting('')

    def __init__(self):
        super().__init__()
        gui.lineEdit(self.controlArea,self,"AtributoAgrupamento", "Agrupado por", box="AtributoAgrupamento")
        gui.lineEdit(self.controlArea,self,"AtributoSoma", "Atributo a ser somado", box="AtributoSoma")

    @Inputs.data
    def SumGroup(self,data):
        self.Outputs.SumGroup.send(data)
# [end-snippet-2]

