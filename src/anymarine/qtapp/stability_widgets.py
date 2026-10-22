"""Presentation widgets only; no model or calculation services."""
from PySide6.QtCore import Qt,QSize,QRectF,QByteArray
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget,QComboBox,QSizePolicy

class LoadingSelector(QComboBox):
    """Human-facing labels with stable raw-case Python caller compatibility."""
    def __init__(self,parent=None):
        super().__init__(parent)
        for key,label in [('coupled','Floating equilibrium'),('upright','Upright tank loading'),('combined','Inclined loading'),('opposite','Opposite-heel loading'),('raked','Raked-end loading')]:
            self.addItem(label,key)
        self.setMinimumWidth(0)
        self.setMinimumContentsLength(8)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
    def setCurrentText(self,text):
        index=self.findData(text)
        if index>=0:self.setCurrentIndex(index)
        else:super().setCurrentText(text)
    def currentText(self):return self.currentData()

class ModelPreview(QWidget):
    """Fit-width by default, scrollable drawing; page-fit and bounded zoom."""
    def __init__(self,svg,parent=None):
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtWidgets import QVBoxLayout,QHBoxLayout,QPushButton,QScrollArea
        super().__init__(parent)
        self._renderer=QSvgRenderer(QByteArray(svg),self);self._mode='width';self._zoom=1.
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);bar=QHBoxLayout();bar.addStretch()
        for title,callback in [('Fit width',lambda:self.fit('width')),('Fit page',lambda:self.fit('page')),('-',lambda:self.zoom(.8)),('+',lambda:self.zoom(1.25))]:
            button=QPushButton(title);button.setMaximumWidth(85);button.clicked.connect(callback);bar.addWidget(button)
        layout.addLayout(bar);self.scroll=QScrollArea();self.scroll.setWidgetResizable(False);self.scroll.setMinimumSize(0,0)
        self.canvas=DrawingCanvas(self);self.scroll.setWidget(self.canvas);layout.addWidget(self.scroll,1)
        self.scroll.viewport().installEventFilter(self);self.setMinimumSize(0,0);self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
    def renderer(self):return self._renderer
    def sizeHint(self):return QSize(600,270)
    def minimumSizeHint(self):return QSize(0,0)
    def fit(self,mode):self._mode=mode;self._zoom=1.;self._layout_drawing()
    def zoom(self,factor):self._zoom=max(.25,min(4.,self._zoom*factor));self._layout_drawing()
    def eventFilter(self,watched,event):
        from PySide6.QtCore import QEvent
        if event.type()==QEvent.Type.Resize:self._layout_drawing()
        return super().eventFilter(watched,event)
    def resizeEvent(self,event):super().resizeEvent(event);self._layout_drawing()
    def _layout_drawing(self):
        natural=self._renderer.defaultSize();viewport=self.scroll.viewport().size()
        if natural.width()<=0 or natural.height()<=0:return
        factor=max(1,viewport.width()-2)/natural.width()
        if self._mode=='page':factor=min(factor,max(1,viewport.height()-2)/natural.height())
        self.canvas.setFixedSize(max(1,int(natural.width()*factor*self._zoom)),max(1,int(natural.height()*factor*self._zoom)));self.canvas.update()

class DrawingCanvas(QWidget):
    def __init__(self,preview):super().__init__();self.preview=preview
    def paintEvent(self,event):
        painter=QPainter(self);self.preview.renderer().render(painter,QRectF(self.rect()))
