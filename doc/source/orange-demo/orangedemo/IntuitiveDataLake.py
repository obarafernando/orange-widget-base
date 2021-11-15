# [start-snippet-1]
import Orange.data
from Orange.data.table import Table
from orangewidget.widget import OWBaseWidget, Input, Output
from orangewidget import gui

class IntuitiveDataLake(OWBaseWidget):
    name = "DataLake"
    description = "Intuitive DataLake"
    icon = "icons/DataLake.svg"
    priority = 10

    class Inputs:
        data = Input("Data", Orange.data.Table)
    class Outputs:
        DataLake = Output("DataLake", Orange.data.Table)

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
            self.infoa.setText('%d show DataLake info')
            self.Outputs.DataLake.send(dataset)
        else:
            self.infoa.setText('No data on input yet, waiting to get something.')
            self.infob.setText('')
            self.Outputs.DataLake.send(dataset)
# [end-snippet-2]
