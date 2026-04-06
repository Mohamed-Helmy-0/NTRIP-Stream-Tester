"""
NTRIP Stream Tester — PyQt5
Install dependency once:
    pip install PyQt5

Run:
    python ntrip_qt.py
"""

import sys, socket, base64, time
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QFrame, QSpinBox, QDoubleSpinBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QPalette, QTextCharFormat, QTextCursor

# ── RTCM descriptions ─────────────────────────────────────────────────────────
RTCM_DESC = {
    1001:"L1-Only GPS RTK",        1002:"Extended L1 GPS RTK",
    1003:"L1/L2 GPS RTK",          1004:"Extended L1/L2 GPS RTK",
    1005:"Stationary RTK Ref",     1006:"Stationary RTK Ref (height)",
    1007:"Antenna Descriptor",     1008:"Antenna Serial No",
    1009:"L1 GLONASS RTK",         1010:"Extended L1 GLONASS RTK",
    1011:"L1/L2 GLONASS RTK",      1012:"Extended L1/L2 GLONASS RTK",
    1019:"GPS Ephemeris",           1020:"GLONASS Ephemeris",
    1033:"Receiver/Antenna Desc",  1074:"GPS MSM4",
    1075:"GPS MSM5",               1077:"GPS MSM7",
    1084:"GLONASS MSM4",           1085:"GLONASS MSM5",
    1087:"GLONASS MSM7",           1094:"Galileo MSM4",
    1095:"Galileo MSM5",           1097:"Galileo MSM7",
    1114:"QZSS MSM4",              1117:"QZSS MSM7",
    1124:"BeiDou MSM4",            1127:"BeiDou MSM7",
    1230:"GLONASS Code-Phase Bias",
}

# ── Worker thread ─────────────────────────────────────────────────────────────
class NtripWorker(QThread):
    sig_log    = pyqtSignal(str, str)   # (message, level)  level: info/ok/warn/err/rtcm/head
    sig_rtcm   = pyqtSignal(int, int)   # (msg_type, bytes)
    sig_status = pyqtSignal(str, str)   # (text, state)  state: idle/connecting/connected/error
    sig_done   = pyqtSignal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg      = cfg
        self._running = True

    def stop(self):
        self._running = False

    # ── helpers ───────────────────────────────────────────────────────────────
    def _build_request(self):
        c = self.cfg
        cred = base64.b64encode(f"{c['user']}:{c['pass']}".encode()).decode()
        return (
            f"GET /{c['mount']} HTTP/1.0\r\n"
            f"Host: {c['host']}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"User-Agent: NTRIP PyQt5Tester/1.0\r\n"
            f"Authorization: Basic {cred}\r\n\r\n"
        ).encode()

    def _build_gga(self):
        c = self.cfg
        lat, lon, alt = c['lat'], c['lon'], c['alt']
        def fmt(v, w): return f"{int(abs(v)):0{w}d}{(abs(v)-int(abs(v)))*60:08.5f}"
        utc  = time.strftime("%H%M%S.00", time.gmtime())
        body = (f"GPGGA,{utc},{fmt(lat,2)},{'N' if lat>=0 else 'S'},"
                f"{fmt(lon,3)},{'E' if lon>=0 else 'W'},1,08,1.0,{alt:.1f},M,0.0,M,,")
        chk  = 0
        for ch in body: chk ^= ord(ch)
        return f"${body}*{chk:02X}\r\n"

    def _parse_rtcm(self, data, offset):
        if offset + 5 > len(data): return None, offset
        if data[offset] != 0xD3:   return None, offset + 1
        length = ((data[offset+1] & 0x03) << 8) | data[offset+2]
        end    = offset + 3 + length + 3
        if end > len(data):        return None, offset
        mt = ((data[offset+3] << 4) | (data[offset+4] >> 4)) & 0x0FFF
        return mt, end

    # ── main loop ─────────────────────────────────────────────────────────────
    def run(self):
        c        = self.cfg
        duration = c['duration']

        self.sig_status.emit("Connecting…", "connecting")
        self.sig_log.emit(f"Connecting to {c['host']}:{c['port']} → /{c['mount']}", "info")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect((c['host'], int(c['port'])))
            sock.sendall(self._build_request())

            # read header
            header = b""
            while b"\r\n\r\n" not in header:
                chunk = sock.recv(1024)
                if not chunk: raise ConnectionError("Connection closed during header read")
                header += chunk

            header_text, _, leftover = header.partition(b"\r\n\r\n")
            for line in header_text.decode(errors='replace').splitlines():
                self.sig_log.emit(line, "head")

            first = header_text.decode(errors='replace').splitlines()[0]
            if "200" not in first and "ICY 200" not in first:
                self.sig_status.emit(f"❌ {first}", "error")
                self.sig_log.emit(f"Auth failed: {first}", "err")
                self.sig_done.emit("Auth failed — check username and password.")
                return

            # send GGA
            gga = self._build_gga()
            sock.sendall(gga.encode())
            self.sig_log.emit(f"📍 GGA sent: {gga.strip()}", "ok")
            self.sig_status.emit("Connected — streaming RTCM…", "connected")

            sock.settimeout(5)
            buffer = leftover
            start  = time.time()

            while self._running:
                if duration > 0 and (time.time() - start) >= duration:
                    self.sig_done.emit(f"Duration of {duration}s reached.")
                    break
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        self.sig_done.emit("Server closed connection.")
                        break
                    buffer += chunk
                    offset  = 0
                    while offset < len(buffer):
                        mt, new_off = self._parse_rtcm(buffer, offset)
                        if new_off == offset: break
                        if mt is not None:
                            self.sig_rtcm.emit(mt, len(chunk))
                            self.sig_log.emit(
                                f"RTCM {mt:4d} — {RTCM_DESC.get(mt,'Unknown/Proprietary')}",
                                "rtcm"
                            )
                        offset = new_off
                    buffer = buffer[offset:]
                except socket.timeout:
                    self.sig_log.emit("No data for 5s, still waiting…", "warn")
                except Exception as e:
                    self.sig_done.emit(str(e)); break

        except Exception as e:
            self.sig_status.emit(f"❌ {e}", "error")
            self.sig_log.emit(f"Error: {e}", "err")
            self.sig_done.emit(f"Failed: {e}")
        finally:
            sock.close()

# ── Credential tester thread ──────────────────────────────────────────────────
class CredentialTester(QThread):
    sig_result = pyqtSignal(bool, str)   # (success, message)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def run(self):
        c = self.cfg
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8)
        try:
            sock.connect((c['host'], int(c['port'])))
        except socket.timeout:
            self.sig_result.emit(False,
                f"❌ Test Failed — Could not reach {c['host']}:{c['port']} (timed out).\n"
                f"   → Double-check the IP address and port.\n"
                f"   → Make sure your internet connection is working.")
            return
        except ConnectionRefusedError:
            self.sig_result.emit(False,
                f"❌ Test Failed — Connection refused at {c['host']}:{c['port']}.\n"
                f"   → The IP is reachable but nothing is listening on port {c['port']}.\n"
                f"   → Verify the port number (default NTRIP port is 2101).")
            return
        except OSError as e:
            self.sig_result.emit(False,
                f"❌ Test Failed — Network error: {e}\n"
                f"   → Check that the IP address is correct and your network is online.")
            return

        try:
            cred = base64.b64encode(f"{c['user']}:{c['pass']}".encode()).decode()
            req  = (
                f"GET /{c['mount']} HTTP/1.0\r\n"
                f"Host: {c['host']}\r\n"
                f"Ntrip-Version: Ntrip/2.0\r\n"
                f"User-Agent: NTRIP CredTest/1.0\r\n"
                f"Authorization: Basic {cred}\r\n\r\n"
            ).encode()
            sock.sendall(req)

            response = b""
            sock.settimeout(5)
            while b"\r\n" not in response:
                chunk = sock.recv(512)
                if not chunk: break
                response += chunk

            first_line = response.split(b"\r\n")[0].decode(errors='replace')
            if "200" in first_line or "ICY 200" in first_line:
                self.sig_result.emit(True,  f"✅ Test Passed — Credentials valid! Server: {first_line}")
            elif "401" in first_line:
                self.sig_result.emit(False, f"❌ Test Failed — Invalid username or password (401 Unauthorized)")
            elif "403" in first_line:
                self.sig_result.emit(False, f"❌ Test Failed — Access forbidden (403). Check mountpoint or account permissions.")
            elif "404" in first_line:
                self.sig_result.emit(False, f"❌ Test Failed — Mountpoint '/{c['mount']}' not found (404).")
            else:
                self.sig_result.emit(False, f"❌ Test Failed — Unexpected response: {first_line}")
        except Exception as e:
            self.sig_result.emit(False, f"❌ Test Failed — {e}")
        finally:
            sock.close()


# ── Main window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🛰 NTRIP Stream Tester")
        self.setMinimumSize(900, 700)
        self.worker     = None
        self.msg_counts = {}
        self.total_bytes= 0
        self.total_msgs = 0
        self.start_time = None
        self.elapsed_timer = QTimer()
        self.elapsed_timer.timeout.connect(self._tick)
        self._build_ui()
        self._apply_dark()
        self._set_inputs(True)  # ensure correct button state on startup
        self._destroyed = False

    # ── UI builder ────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root    = QVBoxLayout(central); root.setSpacing(10); root.setContentsMargins(12,12,12,12)

        # ── Config group ──────────────────────────────────────────────────────
        cfg_group = QGroupBox("Connection Settings")
        cfg_lay   = QGridLayout(cfg_group)
        cfg_lay.setSpacing(8)

        def field(label, default='', pw=False):
            lbl = QLabel(label); f = QLineEdit(default)
            if pw: f.setEchoMode(QLineEdit.Password)
            return lbl, f

        lh, self.f_host  = field("Host / IP",     "13.56.117.10")
        lp, self.f_port  = field("Port",           "2101")
        lm, self.f_mount = field("Mountpoint",     "AUTO")
        lu, self.f_user  = field("Username",       "")
        lpw,self.f_pass  = field("Password",       "", pw=True)

        self.f_dur = QSpinBox(); self.f_dur.setRange(0,3600); self.f_dur.setValue(30)
        self.f_lat = QDoubleSpinBox(); self.f_lat.setRange(-90,90);   self.f_lat.setDecimals(6); self.f_lat.setValue(24.4539)
        self.f_lon = QDoubleSpinBox(); self.f_lon.setRange(-180,180); self.f_lon.setDecimals(6); self.f_lon.setValue(54.3773)
        self.f_alt = QDoubleSpinBox(); self.f_alt.setRange(-500,9000);self.f_alt.setDecimals(1); self.f_alt.setValue(10.0)

        r = 0
        for lbl, wid in [
            (lh, self.f_host),(lp, self.f_port),(lm, self.f_mount),
            (lu, self.f_user),(lpw,self.f_pass),
            (QLabel("Duration (s) — 0=unlimited"), self.f_dur),
            (QLabel("Approx Latitude"),  self.f_lat),
            (QLabel("Approx Longitude"), self.f_lon),
            (QLabel("Approx Altitude (m)"), self.f_alt),
        ]:
            col = (r % 3) * 2
            row =  r // 3
            cfg_lay.addWidget(lbl, row, col)
            cfg_lay.addWidget(wid, row, col+1)
            r += 1

        root.addWidget(cfg_group)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_connect = QPushButton("▶  Connect");  self.btn_connect.clicked.connect(self.start)
        self.btn_stop    = QPushButton("■  Stop");     self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        self.btn_connect.setEnabled(True)
        self.btn_test    = QPushButton("🔍  Test Credentials"); self.btn_test.clicked.connect(self.test_credentials)
        self.btn_clear   = QPushButton("🗑  Clear");   self.btn_clear.clicked.connect(self.clear)
        for b in (self.btn_connect, self.btn_stop, self.btn_test, self.btn_clear): btn_row.addWidget(b)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Status bar ────────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        self.status_lbl = QLabel("● Idle")
        self.status_lbl.setStyleSheet("color:#888; padding:4px 0;")
        self.test_result_lbl = QLabel("")
        self.test_result_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status_row.addWidget(self.status_lbl)
        status_row.addStretch()
        status_row.addWidget(self.test_result_lbl)
        root.addLayout(status_row)

        # ── Stats row ─────────────────────────────────────────────────────────
        stats_row = QHBoxLayout()
        self.stat_bytes   = self._stat_box("Bytes Received", "0")
        self.stat_msgs    = self._stat_box("RTCM Frames",    "0")
        self.stat_types   = self._stat_box("Unique Types",   "0")
        self.stat_elapsed = self._stat_box("Elapsed",        "0s")
        for w in (self.stat_bytes, self.stat_msgs, self.stat_types, self.stat_elapsed):
            stats_row.addWidget(w)
        root.addLayout(stats_row)

        # ── Splitter: log | RTCM table ────────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)

        self.log_box = QTextEdit(); self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Consolas", 9))
        splitter.addWidget(self._group("Log", self.log_box))

        self.rtcm_table = QTableWidget(0, 3)
        self.rtcm_table.setHorizontalHeaderLabels(["Type", "Count", "Description"])
        self.rtcm_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.rtcm_table.verticalHeader().setVisible(False)
        self.rtcm_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rtcm_table.setFont(QFont("Consolas", 9))
        splitter.addWidget(self._group("RTCM Message Breakdown", self.rtcm_table))

        splitter.setSizes([350, 200])
        root.addWidget(splitter)

    def _stat_box(self, label, val):
        frame = QFrame(); frame.setFrameShape(QFrame.StyledPanel)
        lay   = QVBoxLayout(frame); lay.setSpacing(2)
        v = QLabel(val); v.setAlignment(Qt.AlignCenter)
        v.setFont(QFont("Segoe UI", 16, QFont.Bold))
        v.setStyleSheet("color:#4a8be5")
        l = QLabel(label); l.setAlignment(Qt.AlignCenter)
        l.setStyleSheet("color:#777; font-size:10px")
        lay.addWidget(v); lay.addWidget(l)
        frame._val = v
        return frame

    def _group(self, title, widget):
        g = QGroupBox(title); l = QVBoxLayout(g); l.addWidget(widget); return g

    # ── Dark theme ────────────────────────────────────────────────────────────
    def _apply_dark(self):
        self.setStyleSheet("""
            QMainWindow, QWidget       { background:#0f1117; color:#e0e0e0; }
            QGroupBox                  { border:1px solid #2a2d3a; border-radius:6px; margin-top:8px;
                                         font-weight:600; color:#6c8ebf; padding:8px; }
            QGroupBox::title           { subcontrol-origin:margin; left:10px; }
            QLineEdit, QSpinBox, QDoubleSpinBox
                                       { background:#1a1d27; border:1px solid #2e3144;
                                         border-radius:4px; padding:4px 6px; color:#e0e0e0; }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus
                                       { border:1px solid #4a7ddb; }
            QPushButton                { border-radius:5px; padding:7px 18px; font-weight:600; }
            QPushButton#btn_connect, QPushButton
                                       { background:#3a7bd5; color:#fff; border:none; }
            QPushButton:hover          { background:#4a8be5; }
            QPushButton:disabled       { background:#2a2d3a; color:#555; }
            QTextEdit                  { background:#0a0c12; border:1px solid #2a2d3a;
                                         border-radius:4px; color:#ccc; }
            QTableWidget               { background:#0a0c12; border:1px solid #2a2d3a;
                                         gridline-color:#1e2130; }
            QHeaderView::section       { background:#1a1d27; color:#6c8ebf;
                                         border:none; padding:4px; font-weight:600; }
            QFrame[frameShape="1"]     { background:#1a1d27; border:1px solid #2a2d3a;
                                         border-radius:6px; }
            QScrollBar:vertical        { background:#0f1117; width:8px; }
            QScrollBar::handle:vertical{ background:#2a2d3a; border-radius:4px; }
            QSplitter::handle          { background:#2a2d3a; }
            QLabel                     { color:#aaa; }
        """)
        self.btn_stop.setStyleSheet("""
            QPushButton        { background:#c0392b; color:#fff; border:none; border-radius:5px; padding:7px 18px; font-weight:600; }
            QPushButton:hover  { background:#e74c3c; }
            QPushButton:disabled { background:#2a2d3a; color:#555; border:none; }
        """)
        self.btn_test.setStyleSheet("""
            QPushButton        { background:#6c3483; color:#fff; border:none; border-radius:5px; padding:7px 18px; font-weight:600; }
            QPushButton:hover  { background:#8e44ad; }
            QPushButton:disabled { background:#2a2d3a; color:#555; border:none; }
        """)
        self.btn_clear.setStyleSheet("""
            QPushButton        { background:#2a2d3a; color:#aaa; border:none; border-radius:5px; padding:7px 18px; font-weight:600; }
            QPushButton:hover  { background:#3a3d4a; }
        """)

    # ── Logging ───────────────────────────────────────────────────────────────
    COLORS = {
        "info":"#aaaaaa", "ok":"#2ecc71", "warn":"#f39c12",
        "err":"#e74c3c",  "rtcm":"#74b9ff","head":"#a29bfe",
    }

    def _log(self, text, level="info"):
        ts  = time.strftime("%H:%M:%S")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.COLORS.get(level,"#aaa")))
        cur = self.log_box.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(f"[{ts}] {text}\n", fmt)
        self.log_box.setTextCursor(cur)
        self.log_box.ensureCursorVisible()

    # ── Stats ─────────────────────────────────────────────────────────────────
    def _tick(self):
        if self.start_time:
            self.stat_elapsed._val.setText(f"{int(time.time()-self.start_time)}s")

    def _upd_stats(self):
        self.stat_bytes._val.setText(f"{self.total_bytes:,}")
        self.stat_msgs._val.setText(str(self.total_msgs))
        self.stat_types._val.setText(str(len(self.msg_counts)))

    def _upd_table(self):
        self.rtcm_table.setRowCount(len(self.msg_counts))
        for i, (mt, cnt) in enumerate(sorted(self.msg_counts.items())):
            self.rtcm_table.setItem(i, 0, QTableWidgetItem(f"RTCM {mt}"))
            self.rtcm_table.setItem(i, 1, QTableWidgetItem(str(cnt)))
            self.rtcm_table.setItem(i, 2, QTableWidgetItem(RTCM_DESC.get(mt,"Unknown / Proprietary")))

    # ── Close event ──────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._destroyed = True
        if self.worker:
            self.worker.stop()
            self.worker.wait()
            self.worker = None
        self.elapsed_timer.stop()
        event.accept()

    # ── Connect / Stop ────────────────────────────────────────────────────────
    def _cfg(self):
        return {
            'host':     self.f_host.text().strip(),
            'port':     self.f_port.text().strip(),
            'mount':    self.f_mount.text().strip(),
            'user':     self.f_user.text().strip(),
            'pass':     self.f_pass.text(),
            'lat':      self.f_lat.value(),
            'lon':      self.f_lon.value(),
            'alt':      self.f_alt.value(),
            'duration': self.f_dur.value(),
        }

    def test_credentials(self):
        cfg = self._cfg()
        if not cfg['host'] or not cfg['user'] or not cfg['pass']:
            self._log("⚠ Please fill in Host, Username and Password before testing.", "warn")
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText("🔍  Testing…")
        self.test_result_lbl.setText("Testing…")
        self.test_result_lbl.setStyleSheet("color:#f39c12; font-weight:600;")
        self._log(f"Testing credentials for {cfg['user']} @ {cfg['host']}:{cfg['port']}…", "info")

        self._tester = CredentialTester(cfg)
        self._tester.sig_result.connect(self._on_test_result)
        self._tester.start()

    def _on_test_result(self, success, message):
        self._log(message, "ok" if success else "err")
        if success:
            self.test_result_lbl.setText("✅ Credentials Valid")
            self.test_result_lbl.setStyleSheet("color:#2ecc71; font-weight:600;")
        else:
            self.test_result_lbl.setText("❌ Test Failed")
            self.test_result_lbl.setStyleSheet("color:#e74c3c; font-weight:600;")
        self.btn_test.setEnabled(True)
        self.btn_test.setText("🔍  Test Credentials")

    def start(self):
        cfg = self._cfg()
        if not cfg['host'] or not cfg['user'] or not cfg['pass']:
            self._log("⚠ Please fill in Host, Username and Password.", "warn"); return

        self.msg_counts = {}; self.total_bytes = 0; self.total_msgs = 0
        self.start_time = time.time()
        self._upd_stats(); self._upd_table()
        self.elapsed_timer.start(500)
        self._set_inputs(False)

        self.worker = NtripWorker(cfg)
        self.worker.sig_log.connect(self._log)
        self.worker.sig_status.connect(self._on_status)
        self.worker.sig_rtcm.connect(self._on_rtcm)
        self.worker.sig_done.connect(self._on_done)
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.worker.wait()   # wait for thread to fully finish
            self.worker = None
        self.elapsed_timer.stop()
        self._set_inputs(True)
        self._on_status("Idle", "idle")

    def clear(self):
        self.log_box.clear()
        self.rtcm_table.setRowCount(0)
        self.msg_counts = {}; self.total_bytes = 0; self.total_msgs = 0
        self.start_time = None
        self._upd_stats()
        self.stat_elapsed._val.setText("0s")

    def _set_inputs(self, enabled):
        for w in (self.f_host,self.f_port,self.f_mount,self.f_user,
                  self.f_pass,self.f_lat,self.f_lon,self.f_alt,self.f_dur):
            w.setEnabled(enabled)
        self.btn_connect.setEnabled(enabled)
        self.btn_stop.setEnabled(not enabled)

    # ── Signals ───────────────────────────────────────────────────────────────
    def _on_status(self, text, state):
        colors = {"connecting":"#f39c12","connected":"#2ecc71","error":"#e74c3c","idle":"#888"}
        col    = colors.get(state,"#888")
        self.status_lbl.setText(f"● {text}")
        self.status_lbl.setStyleSheet(f"color:{col}; padding:4px 0; font-weight:600;")

    def _on_rtcm(self, mt, nbytes):
        self.msg_counts[mt] = self.msg_counts.get(mt, 0) + 1
        self.total_bytes   += nbytes
        self.total_msgs    += 1
        self._upd_stats()
        self._upd_table()

    def _on_done(self, text):
        self._log(text, "ok")
        self.elapsed_timer.stop()
        self.worker = None
        self._set_inputs(True)
        self._on_status("Idle", "idle")

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())