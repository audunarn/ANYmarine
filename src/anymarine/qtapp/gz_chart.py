"""Native Qt display of retained GZ samples; no interpolation across failed gaps."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget, QToolTip

class GZChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 220)
        self.setMouseTracking(True)
        self.data = {}
        self._hits = []
        self.setAccessibleName('GZ curve. Heel in degrees; righting lever in metres.')

    def set_result(self, data):
        self.data = data or {}
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('white'))
        plot = QRectF(62, 24, max(1, self.width()-90), max(1, self.height()-78))
        p.setPen(QColor('#52667a'))
        p.drawText(QRectF(4, 0, self.width()-8, 22), Qt.AlignmentFlag.AlignCenter,
                   'Righting lever GZ [m] · retained engineering samples')
        p.drawText(QRectF(0, self.height()-24, self.width(), 22), Qt.AlignmentFlag.AlignCenter, 'Heel [degrees]')
        points = [x for x in self.data.get('points', []) if isinstance(x, dict)
                  and x.get('heel_rad') is not None and x.get('gz_m') is not None]
        self._hits = []
        if not points:
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, 'No accepted GZ points. Run Preview or open a retained result.')
            return
        xvalues = [math.degrees(x['heel_rad']) for x in points]
        yvalues = [x['gz_m'] for x in points]+[0]
        xmin, xmax = min(xvalues), max(xvalues)
        ymin, ymax = min(yvalues), max(yvalues)
        if xmax == xmin: xmin, xmax = xmin-1, xmax+1
        if ymax == ymin: ymin, ymax = ymin-.01, ymax+.01
        pad = (ymax-ymin)*.12
        ymin -= pad; ymax += pad
        def position(x, y):
            return QPointF(plot.left()+(x-xmin)/(xmax-xmin)*plot.width(),
                           plot.bottom()-(y-ymin)/(ymax-ymin)*plot.height())
        p.setPen(QPen(QColor('#dce4ed'), 1))
        p.drawRect(plot)
        p.drawLine(position(xmin, 0), position(xmax, 0))
        for i in range(5):
            x = xmin+(xmax-xmin)*i/4
            y = ymin+(ymax-ymin)*i/4
            p.setPen(QColor('#52667a'))
            p.drawText(QRectF(plot.left()-59, position(xmin,y).y()-9, 54, 18), Qt.AlignmentFlag.AlignRight, f'{y:.3g}')
            p.drawText(QRectF(position(x,0).x()-30, plot.bottom()+4, 60, 20), Qt.AlignmentFlag.AlignCenter, f'{x:.3g}')
        branches = {}
        metadata=self.data.get('branches',{})
        indexed={p.get('identity'):p for p in points if p.get('identity')}
        if isinstance(metadata,dict) and any(b.get('point_identities') for b in metadata.values() if isinstance(b,dict)):
            for name,branch in metadata.items():
                if isinstance(branch,dict):branches[name]=[indexed[i] for i in branch.get('point_identities',[]) if i in indexed]
        else:
            for point in points:branches.setdefault(str(point.get('branch', 'retained')), []).append(point)
        for index, (branch, samples) in enumerate(sorted(branches.items())):
            color = QColor(('#17698b', '#a44377', '#54804b')[index%3])
            previous = None
            # Owner supplies unresolved flags/branch segmentation. Never join different branches.
            for point in sorted(samples, key=lambda x:x['heel_rad']):
                xy = position(math.degrees(point['heel_rad']), point['gz_m'])
                p.setPen(QPen(color, 2))
                blocked = point.get('gap_before', False) or point.get('resolved') is False
                intervals=metadata.get(branch,{}).get('intervals',[]) if isinstance(metadata,dict) else []
                resolved=any(i.get('status')=='engineering_estimate_resolved' and
                    min(i['from_heel_rad'],i['to_heel_rad'])-1e-12<=point['heel_rad']<=max(i['from_heel_rad'],i['to_heel_rad'])+1e-12 and
                    min(i['from_heel_rad'],i['to_heel_rad'])-1e-12<=previous_heel<=max(i['from_heel_rad'],i['to_heel_rad'])+1e-12 for i in intervals) if previous is not None else False
                if previous is not None and not blocked and resolved:
                    p.drawLine(previous, xy)
                p.setBrush(color); p.drawEllipse(xy, 3.5, 3.5)
                self._hits.append((xy, point))
                previous = None if point.get('gap_after', False) else xy
                previous_heel=point['heel_rad']
            p.drawText(QRectF(plot.left()+8, plot.top()+index*18, 200, 18), 'Branch '+branch)
        # Opening immersion markers are owner-retained events, never inferred here.
        events = list(self.data.get('opening_events', []))
        for point in points:
            for opening in point.get('openings',[]):
                if opening.get('immersed') and opening.get('limits_intact') and opening.get('closing_status')!='closed':events.append({'heel_rad':point['heel_rad'],'gz_m':point['gz_m'],**opening})
        for branch in self.data.get('branches', {}).values() if isinstance(self.data.get('branches'), dict) else []:
            if isinstance(branch, dict): events = events+branch.get('opening_events', [])
        for opening in events:
            if not isinstance(opening, dict) or opening.get('heel_rad') is None: continue
            xy = position(math.degrees(opening['heel_rad']), opening.get('gz_m') or 0)
            p.setBrush(QColor('#c87821')); p.setPen(QColor('#c87821'))
            p.drawPolygon(QPolygonF([xy+QPointF(0,-7),xy+QPointF(-6,5),xy+QPointF(6,5)]))
        if self.data.get('status') != 'complete' or self.data.get('pending'):
            p.setPen(QColor('#9b542a'))
            p.drawText(QRectF(plot.left()+5, plot.bottom()-22, plot.width()-10, 20), 'Partial / unresolved intervals retained; no stability rule PASS')

    def mouseMoveEvent(self, event):
        if not self._hits: return
        xy, point = min(self._hits, key=lambda row:(row[0]-event.position()).manhattanLength())
        if (xy-event.position()).manhattanLength() > 30: return
        def value(name, unit):
            val = point.get(name)
            return 'unavailable' if val is None else f'{val:.7g} {unit}'
        QToolTip.showText(event.globalPosition().toPoint(),
            f"Heel {math.degrees(point['heel_rad']):.4g}°\nGZ {value('gz_m','m')}\n"
            f"Trim {value('trim_rad','rad')}\nRighting moment {value('righting_moment_Nm','Nm')}\n"
            f"Branch {point.get('branch', 'retained')}\nIdentity {point.get('identity','unavailable')}", self)
