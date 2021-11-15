# [start-snippet-1]
import Orange.data
from Orange.data.table import Table
from orangewidget.widget import OWBaseWidget, Input, Output
from orangewidget import gui

class IntuitiveDataSet(OWBaseWidget):
    name = "DataSet"
    description = "Intuitive DataSet"
    icon = "icons/DataSet.svg"
    priority = 10

    class Inputs:
        data = Input("Data", Orange.data.Table)
    class Outputs:
        DataSet = Output("DataSet", Orange.data.Table)

    want_main_area = False

    def __init__(self):
        super().__init__()

        # GUI
        box = gui.widgetBox(self.controlArea, "Info")
        self.infoa = gui.widgetLabel(box, 'No data on input yet, waiting to get something.')
        self.infob = gui.widgetLabel(box, '')
# [end-snippet-1]

# [start-snippet-2]
    @Inputs.data
    def set_data(self, dataset):
        if dataset is not None:
            self.infoa.setText('%d show DataSet info')
            self.Outputs.DataSet.send(dataset)
        else:
            self.infoa.setText('No data on input yet, waiting to get something.')
            self.infob.setText('')
            self.Outputs.DataSet.send(dataset)
# [end-snippet-2]
