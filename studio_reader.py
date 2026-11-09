"""A local film issue reader with paper turns and timestamped video playback."""

import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("QT_MEDIA_BACKEND", "windows")

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QTimer, QUrl, QVariantAnimation
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QKeySequence, QLinearGradient, QPainter, QPixmap, QPolygonF, QShortcut, QTransform
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton, QSlider,
    QSizePolicy, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from config import APP_ICON, ASSETS_DIR


PALETTE = """
QWidget { background: #151518; color: #f2eae0; font: 14px 'Microsoft YaHei UI'; }
QLabel { background: transparent; }
QLabel[kicker='true'] { color: #efb275; font-size: 12px; font-weight: 650; }
QPushButton, QComboBox { border: 1px solid #514844; background: #292729; color: #f2eae0;
    border-radius: 8px; padding: 8px 12px; }
QPushButton:hover { background: #403732; }
QPushButton:focus, QComboBox:focus { border: 2px solid #76d1c4; padding: 7px 11px; }
QPushButton:disabled { color: #9e958e; border-color: #3a3431; background: #222023; }
QPushButton[primary='true'] { background: #efb275; border-color: #efb275; color: #241a13; font-weight: 700; }
QPushButton[primary='true']:hover { background: #ffd3a6; }
QFrame#paper { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b9a28d,
    stop:0.045 #e7dac9, stop:0.13 #f0e7d9, stop:0.97 #f0e7d9, stop:1 #dac5ad);
    border-top-right-radius: 12px; border-bottom-right-radius: 12px; }
QFrame#paper QLabel { color: #3a2c27; }
QFrame#paper QLabel#chapterTitle { color: #201915; font-size: 28px; font-weight: 700; }
QFrame#paper QLabel#chapterMeta { color: #6b4c36; font-size: 12px; font-weight: 700; }
QFrame#paper QLabel[paperNote='true'] { color: #70543f; font-size: 12px; }
QFrame#paper QFrame[rule='true'] { background: #cbb8a4; }
QFrame#attribution { background: transparent; border-top: 1px solid #cbb8a4; }
QFrame#paper QTextBrowser, QFrame#paper QTextBrowser > QWidget { background: transparent;
    color: #3a2c27; border: 0; font-size: 17px; }
QFrame#paper QPushButton { background: #ede3d5; color: #65472f; border-color: #a88b72; }
QFrame#paper QPushButton:hover { background: #e0cdb7; }
QLabel#notice { color: #efb275; }
QSlider::groove:horizontal { height: 4px; background: #574c45; }
QSlider::handle:horizontal { width: 13px; margin: -5px 0; border-radius: 6px; background: #efb275; }
"""


def timecode(milliseconds):
    seconds = max(0, milliseconds) // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def plain_label(text=""):
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)
    return label


class FrameArt(QWidget):
    def __init__(self, chapters, duration):
        super().__init__()
        self.picture = QPixmap()
        self.caption = ""
        self.chapters = chapters
        self.duration = max(duration, max(chapters, default=0))
        self.active = None
        self.setMinimumSize(210, 190)

    def set_image(self, candidates, caption, active):
        self.picture = QPixmap()
        self.caption = caption
        self.active = active
        for path in candidates:
            picture = QPixmap(str(path)) if path else QPixmap()
            if not picture.isNull():
                self.picture = picture
                break
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        matte = QLinearGradient(0, 0, self.width(), self.height())
        matte.setColorAt(0, QColor("#302926"))
        matte.setColorAt(1, QColor("#18181b"))
        painter.fillRect(self.rect(), matte)
        area = self.rect().adjusted(22, 40, -22, -41)
        painter.fillRect(area, QColor("#141417"))
        picture = self.picture.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if not picture.isNull():
            x = area.center().x() - picture.width() // 2
            y = area.center().y() - picture.height() // 2
            painter.fillRect(QRect(x-1, y-1, picture.width()+2, picture.height()+2), QColor("#6b5847"))
            painter.drawPixmap(x, y, picture)
        painter.setPen(QColor("#c39a6d"))
        for x, dx in ((area.left(), 14), (area.right(), -14)):
            for y, dy in ((area.top(), 14), (area.bottom(), -14)):
                painter.drawLine(x, y, x+dx, y)
                painter.drawLine(x, y, x, y+dy)
        painter.setPen(QColor("#d8bea4"))
        painter.drawText(QRect(22, 0, self.width()-44, 40), Qt.AlignmentFlag.AlignVCenter, self.caption)
        if self.duration:
            y = self.height() - 21
            painter.setPen(QColor("#66584f"))
            painter.drawLine(area.left(), y, area.right(), y)
            for start in self.chapters:
                x = area.left() + round((area.width()-1) * start / self.duration)
                painter.setPen(QColor("#efb275" if start == self.active else "#938171"))
                painter.drawLine(x, y-5, x, y+5)
        seam = QLinearGradient(self.width()-16, 0, self.width(), 0)
        seam.setColorAt(0, QColor(0, 0, 0, 0))
        seam.setColorAt(1, QColor(0, 0, 0, 155))
        painter.fillRect(self.width()-16, 0, 16, self.height(), seam)


class VideoSurface(QWidget):
    def __init__(self):
        super().__init__()
        self.sink = QVideoSink(self)
        self.image = QImage()
        self.sink.videoFrameChanged.connect(self.present)

    def present(self, frame):
        if frame.isValid():
            picture = frame.toImage()
            if not picture.isNull():
                self.image = picture
                self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#100f11"))
        if not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRect(0, 0, size.width(), size.height())
            rect.moveCenter(self.rect().center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(rect, self.image)


class PageTurn(QWidget):
    def __init__(self, parent, before, after, forward):
        super().__init__(parent)
        self.before, self.after, self.forward = before, after, forward
        self.progress = 0.0
        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.animation = QVariantAnimation(self, startValue=0., endValue=1., duration=650,
                                           easingCurve=QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(self.step)
        self.animation.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.animation.start()

    def step(self, value):
        self.progress = value
        self.update()

    def paintEvent(self, event):
        from math import cos, pi, sin

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        mid = w // 2
        painter.drawPixmap(self.rect(), self.before)
        reveal = QRect(mid, 0, w-mid, h) if self.forward else QRect(0, 0, mid, h)
        painter.save()
        painter.setClipRect(reveal)
        painter.drawPixmap(self.rect(), self.after)
        painter.restore()
        front = self.progress < .5
        right = self.forward == front
        direction = 1 if right else -1
        width = w-mid if right else mid
        angle = pi * self.progress
        lift = sin(angle)
        span = max(2, width * abs(cos(angle)))
        tip = mid + direction * span
        image = self.before if front else self.after
        shade_end = tip + direction * (18 + 45 * lift)
        shadow = QLinearGradient(tip, 0, shade_end, 0)
        shadow.setColorAt(0, QColor(0, 0, 0, round(163*lift)))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(min(tip, shade_end), 0, abs(tip-shade_end), h), shadow)
        ratio = image.devicePixelRatio()
        strips = 12
        strip_width = width / strips
        curve = min(span * .78, width * .34 * lift)
        height = min(52, h * .12)

        def edge(t):
            x = mid + direction * (span * t + curve * t * (1-t))
            return QPointF(x, lift * height * (t*t + .26*sin(pi*t))), QPointF(x, h - lift * height * .42*t)

        original = QPolygonF([QPointF(0, 0), QPointF(strip_width, 0), QPointF(strip_width, h), QPointF(0, h)])
        for i in range(strips):
            a, b = i/strips, (i+1)/strips
            left, right_edge = (a, b) if right else (b, a)
            lt, lb = edge(left)
            rt, rb = edge(right_edge)
            transform = QTransform()
            if QTransform.quadToQuad(original, QPolygonF([lt, rt, rb, lb]), transform):
                source_x = (mid + a*width) if right else (1-b)*width
                target = QRectF(0, 0, strip_width + .3, h)
                painter.setTransform(transform)
                painter.drawPixmap(target, image, QRectF(source_x * ratio, 0, strip_width * ratio, image.height()))
                shade = round((42 + 54*a*a) * lift)
                painter.fillRect(target, QColor(42, 26, 17, shade))
        painter.resetTransform()
        top, bottom = edge(1)
        painter.setPen(QColor(242, 210, 173, round(180 * lift)))
        painter.drawLine(top, bottom)


class IssueReader(QMainWindow):
    def __init__(self, issue):
        super().__init__()
        self.issue = issue
        self.video_path = Path(issue["video"])
        self.pages = [page for page in issue.get("reading_pages", []) if isinstance(page, dict)
                      and isinstance(page.get("start_ms"), int) and page["start_ms"] >= 0]
        self.duration_ms = round((issue.get("duration") or 0) * 1000)
        self.index = 0
        self.motion = True
        self.turn = None
        self.pending_seek = None
        self.muted_before_seek = None
        self.seek_timeout = QTimer(self)
        self.seek_timeout.setSingleShot(True)
        self.seek_timeout.timeout.connect(self.seek_failed)
        self.seek_after_start = QTimer(self)
        self.seek_after_start.setSingleShot(True)
        self.seek_after_start.timeout.connect(self.apply_seek)
        self.setWindowTitle(f"热采放映室 · {issue['title']}")
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.setMinimumSize(730, 510)
        available = self.screen().availableGeometry().size()
        self.resize(min(1100, int(available.width() * .94)), min(710, int(available.height() * .92)))
        self.setStyleSheet(PALETTE)
        root = QWidget()
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(24, 18, 24, 18)
        shell.setSpacing(12)
        header = QHBoxLayout()
        identity = plain_label("热采放映室   /   影像刊物")
        identity.setProperty("kicker", True)
        header.addWidget(identity)
        header.addStretch()
        platform_label = plain_label(issue["platform"])
        header.addWidget(platform_label)
        self.motion_button = QPushButton("动效开")
        self.motion_button.setCheckable(True)
        self.motion_button.setChecked(True)
        self.motion_button.toggled.connect(self.toggle_motion)
        header.addWidget(self.motion_button)
        shell.addLayout(header)
        self.stack = QStackedWidget()
        shell.addWidget(self.stack, 1)
        self.spread = QWidget()
        sheets = QHBoxLayout(self.spread)
        sheets.setContentsMargins(0, 0, 0, 0)
        sheets.setSpacing(0)
        self.art = FrameArt([page["start_ms"] for page in self.pages], self.duration_ms)
        sheets.addWidget(self.art, 1)
        paper = QFrame()
        paper.setObjectName("paper")
        words = QVBoxLayout(paper)
        words.setContentsMargins(29, 23, 25, 20)
        words.setSpacing(11)
        self.meta = plain_label()
        self.meta.setObjectName("chapterMeta")
        words.addWidget(self.meta)
        self.title = plain_label()
        self.title.setObjectName("chapterTitle")
        self.title.setWordWrap(True)
        words.addWidget(self.title)
        rule = QFrame()
        rule.setProperty("rule", True)
        rule.setFixedHeight(1)
        words.addWidget(rule)
        self.reading_label = plain_label()
        self.reading_label.setProperty("paperNote", True)
        words.addWidget(self.reading_label)
        self.excerpt = QTextBrowser()
        self.excerpt.setOpenLinks(False)
        self.excerpt.setFrameShape(QFrame.Shape.NoFrame)
        self.excerpt.document().setDocumentMargin(0)
        words.addWidget(self.excerpt, 1)
        self.source_row = QFrame()
        self.source_row.setObjectName("attribution")
        attribution = QHBoxLayout(self.source_row)
        attribution.setContentsMargins(0, 10, 0, 0)
        self.source_label = plain_label()
        self.source_label.setProperty("paperNote", True)
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        attribution.addWidget(self.source_label, 1)
        self.source_button = QPushButton()
        self.source_button.clicked.connect(self.open_source)
        attribution.addWidget(self.source_button)
        words.addWidget(self.source_row)
        sheets.addWidget(paper, 1)
        self.stack.addWidget(self.spread)

        player_page = QWidget()
        player_layout = QVBoxLayout(player_page)
        player_layout.setContentsMargins(0, 0, 0, 0)
        self.video_stage = QStackedWidget()
        self.video = VideoSurface()
        self.video_stage.addWidget(self.video)
        self.loading = plain_label("正在定位视频…")
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_stage.addWidget(self.loading)
        player_layout.addWidget(self.video_stage, 1)
        controls = QHBoxLayout()
        back = QPushButton("返回刊物")
        back.clicked.connect(self.show_spread)
        controls.addWidget(back)
        self.pause = QPushButton("暂停 / 继续")
        self.pause.clicked.connect(self.toggle_playback)
        controls.addWidget(self.pause)
        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setAccessibleName("视频进度")
        self.progress.sliderReleased.connect(lambda: self.player.setPosition(self.progress.value()))
        controls.addWidget(self.progress, 1)
        self.play_time = plain_label("00:00")
        controls.addWidget(self.play_time)
        player_layout.addLayout(controls)
        self.stack.addWidget(player_page)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.video.sink)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(lambda duration: self.progress.setRange(0, duration))
        self.player.errorOccurred.connect(self.playback_error)

        bottom = QHBoxLayout()
        self.prev = QPushButton("‹  上一页")
        self.prev.clicked.connect(lambda: self.show_page(self.index-1))
        bottom.addWidget(self.prev)
        self.toc = QComboBox()
        self.toc.setAccessibleName("章节目录")
        self.toc.setMinimumContentsLength(7)
        self.toc.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.toc.addItem("目录 · 封面")
        for i, page in enumerate(self.pages, 1):
            self.toc.addItem(f"{i:02d}  {page.get('title', '')[:24]}")
        self.toc.activated.connect(self.show_page)
        bottom.addWidget(self.toc)
        self.folio = plain_label()
        bottom.addWidget(self.folio)
        self.next = QPushButton("下一页  ›")
        self.next.clicked.connect(lambda: self.show_page(self.index+1))
        bottom.addWidget(self.next)
        bottom.addStretch()
        self.play = QPushButton("播放整片")
        self.play.setProperty("primary", True)
        self.play.clicked.connect(self.play_page)
        bottom.addWidget(self.play)
        shell.addLayout(bottom)
        self.notice = plain_label()
        self.notice.setWordWrap(True)
        self.notice.setObjectName("notice")
        self.notice.hide()
        shell.addWidget(self.notice)
        for key, direction in (("Left", -1), ("Right", 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda delta=direction: self.show_page(self.index+delta))
        chapter = issue.get("chapter", 0)
        self.show_page(chapter if isinstance(chapter, int) and 0 <= chapter <= len(self.pages) else 0, animate=False)
        if issue.get("autoplay"):
            self.play_page()

    def toggle_motion(self, enabled):
        self.motion = enabled
        self.motion_button.setText("动效开" if enabled else "动效关")
        if not enabled:
            self.clear_turn()

    def clear_turn(self):
        if self.turn is not None:
            self.turn.animation.stop()
            self.turn.hide()
            self.turn.deleteLater()
            self.turn = None

    def show_page(self, index, *, animate=True):
        if not 0 <= index <= len(self.pages):
            return
        self.clear_turn()
        animate = animate and self.motion and self.stack.currentWidget() == self.spread and self.index != index
        old_index = self.index
        before = self.spread.grab() if animate else None
        self.show_spread()
        self.index = index
        self.toc.setCurrentIndex(index)
        self.prev.setEnabled(index > 0)
        self.next.setEnabled(index < len(self.pages))
        self.folio.setText(f"{index+1:02d} / {len(self.pages)+1:02d}")
        self.play.setText("播放本章" if index else "播放整片")
        self.play.setEnabled(self.video_path.is_file())
        self.source_url = QUrl()
        self.source_links = []
        source = ""
        active = None
        caption = "封面   /   COVER"
        viewfinder = ASSETS_DIR / "screening" / "viewfinder.png"
        frames = [self.issue.get("cover"), self.video_path.with_name("slide_00.png"), viewfinder]
        if index:
            page = self.pages[index-1]
            active = page["start_ms"]
            stamp = timecode(active)
            self.meta.setText(f"第 {index:02d} 章   /   {stamp}")
            caption = f"FRAME {index:02d}   /   {stamp}"
            self.title.setText(page.get("title", ""))
            self.reading_label.setText("口播摘录")
            self.excerpt.setPlainText(page.get("excerpt", "") or "本章没有口播。")
            name = page.get("frame")
            frames = [viewfinder]
            if name and Path(name).name == name:
                frames.insert(0, self.video_path.with_name(name))
            self.source_url = QUrl(page.get("source_url") or "")
            source = page.get("source", "")
            self.source_links = [(report["title"], QUrl(report["url"])) for report in page.get("sources", [])
                                 if QUrl(report["url"]).scheme() in {"http", "https"}]
        else:
            self.meta.setText(f"封面   /   {self.issue['platform']}   /   {timecode(self.duration_ms)}")
            self.title.setText(self.issue["title"])
            self.reading_label.setText(f"本期目录   /   {len(self.pages)} 章" if self.pages else "成片")
            chapters = [f"{timecode(page['start_ms'])}    {page.get('title', '')}"
                        for page in self.pages[:6]]
            if len(self.pages) > len(chapters):
                chapters.append(f"另有 {len(self.pages)-len(chapters)} 章，可从目录跳转。")
            self.excerpt.setPlainText("\n\n".join(chapters) if chapters else "这份成片可从封面直接播放。")
        has_link = self.source_url.scheme() in {"http", "https"}
        self.source_label.setText(f"报道来源  ·  {source}" if source else "报道来源")
        self.source_label.setToolTip(source or "")
        self.source_row.setVisible(bool(source or has_link))
        self.source_button.setText(f"查看 {len(self.source_links)} 篇原文" if len(self.source_links) > 1 else "查看原文 ↗")
        self.source_button.setVisible(has_link or bool(self.source_links))
        self.art.set_image(frames, caption, active)
        if before:
            self.spread.layout().activate()
            self.turn = PageTurn(self.spread, before, self.spread.grab(), index > old_index)
            self.turn.animation.finished.connect(self.finish_turn)

    def finish_turn(self):
        self.turn = None

    def show_spread(self):
        self.player.pause()
        self.finish_seek()
        self.stack.setCurrentWidget(self.spread)

    def open_source(self):
        if len(self.source_links) > 1:
            menu = QMenu(self.source_button)
            for title, url in self.source_links:
                menu.addAction(title, lambda checked=False, target=url: QDesktopServices.openUrl(target))
            menu.exec(self.source_button.mapToGlobal(self.source_button.rect().bottomLeft()))
            return
        if self.source_url.scheme() in {"http", "https"}:
            QDesktopServices.openUrl(self.source_url)

    def play_page(self):
        if not self.video_path.is_file():
            self.playback_error()
            return
        if not self.player.isAvailable():
            self.show_spread()
            self.notice.setText("本机的 Windows 多媒体组件不可用，仍可翻阅章节内容。")
            self.notice.show()
            return
        self.clear_turn()
        self.notice.hide()
        self.stack.setCurrentIndex(1)
        self.video_stage.setCurrentWidget(self.loading)
        self.pause.setEnabled(False)
        self.progress.setEnabled(False)
        self.pending_seek = self.pages[self.index-1]["start_ms"] if self.index else 0
        if self.muted_before_seek is None:
            self.muted_before_seek = self.audio.isMuted()
        self.audio.setMuted(True)
        source = QUrl.fromLocalFile(str(self.video_path.resolve()))
        self.seek_timeout.start(5000)
        if self.player.source() != source or self.player.mediaStatus() == QMediaPlayer.MediaStatus.InvalidMedia:
            self.player.setSource(source)
        self.player.play()

    def finish_seek(self):
        self.seek_timeout.stop()
        self.seek_after_start.stop()
        self.pending_seek = None
        if self.muted_before_seek is not None:
            self.audio.setMuted(self.muted_before_seek)
            self.muted_before_seek = None

    def seek_failed(self):
        self.show_spread()
        self.notice.setText("本机播放器未能定位。可以继续翻阅，或从封面播放整片。")
        self.notice.show()

    def apply_seek(self):
        if self.pending_seek is None:
            return
        target, self.pending_seek = self.pending_seek, None
        if self.player.isSeekable():
            self.player.setPosition(target)
        self.finish_seek()
        self.video_stage.setCurrentWidget(self.video)
        self.pause.setEnabled(True)
        self.progress.setEnabled(True)

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.pending_seek is None:
            self.player.play()

    def update_position(self, position):
        if self.pending_seek is not None:
            # Let the Windows backend settle after startup and seek outside its position callback.
            if position >= 450 and (self.player.isSeekable() or not self.pending_seek) and not self.seek_after_start.isActive():
                self.seek_after_start.start(30)
            return
        if not self.progress.isSliderDown():
            self.progress.setValue(position)
        self.play_time.setText(timecode(position))

    def playback_error(self, *_):
        self.show_spread()
        self.notice.setText("无法打开这份成片。请检查本地视频文件。")
        self.notice.show()


def reader_main(payload):
    app = QApplication([sys.argv[0]])
    window = IssueReader(json.loads(payload))
    window.show()
    return app.exec()
