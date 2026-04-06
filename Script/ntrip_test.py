import socket
import base64
import time

# ── Defaults (press Enter to use these, or type a new value) ─────────────────
DEFAULTS = {
    "host":       "13.56.117.10",
    "port":       "2101",
    "mountpoint": "AUTO",
    "username":   "",
    "password":   "",
    "lat":        "24.4539",
    "lon":        "54.3773",
    "alt":        "10.0",
    "duration":   "30",
}
# ─────────────────────────────────────────────────────────────────────────────

def prompt(label, key, secret=False):
    default = DEFAULTS[key]
    if secret and default:
        display = "*" * len(default)
    else:
        display = default
    hint = f" [{display}]" if display else ""
    val = input(f"  {label}{hint}: ").strip()
    if not val:
        if not default:
            print(f"  ⚠ No value entered for {label}. Please enter a value.")
            return prompt(label, key, secret)
        return default
    return val

def confirm_settings(cfg):
    print("\n─────────────────────────────────────────────────")
    print("  Settings Summary")
    print("─────────────────────────────────────────────────")
    print(f"  Host       : {cfg['host']}")
    print(f"  Port       : {cfg['port']}")
    print(f"  Mountpoint : {cfg['mountpoint']}")
    print(f"  Username   : {cfg['username']}")
    print(f"  Password   : {'*' * len(cfg['password'])}")
    print(f"  Latitude   : {cfg['lat']}")
    print(f"  Longitude  : {cfg['lon']}")
    print(f"  Altitude   : {cfg['alt']} m")
    print(f"  Duration   : {cfg['duration']} s")
    print("─────────────────────────────────────────────────")
    choice = input("\n  Press Enter / type OK to connect, or R to re-enter values: ").strip().upper()
    return choice in ("", "OK", "O")

def get_config():
    while True:
        print("\n═════════════════════════════════════════════════")
        print("         NTRIP Stream Tester — Configuration")
        print("═════════════════════════════════════════════════")
        print("  Press Enter to keep the default value shown in [brackets].\n")

        cfg = {
            "host":       prompt("Host / IP     ", "host"),
            "port":       prompt("Port          ", "port"),
            "mountpoint": prompt("Mountpoint    ", "mountpoint"),
            "username":   prompt("Username      ", "username"),
            "password":   prompt("Password      ", "password", secret=True),
            "lat":        prompt("Approx Lat    ", "lat"),
            "lon":        prompt("Approx Lon    ", "lon"),
            "alt":        prompt("Approx Alt (m)", "alt"),
            "duration":   prompt("Duration (s)  ", "duration"),
        }

        if confirm_settings(cfg):
            return cfg
        print("\n  Re-entering values...\n")

# ── NTRIP helpers ─────────────────────────────────────────────────────────────

def build_request(cfg):
    credentials = base64.b64encode(f"{cfg['username']}:{cfg['password']}".encode()).decode()
    return (
        f"GET /{cfg['mountpoint']} HTTP/1.0\r\n"
        f"Host: {cfg['host']}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP PythonTester/1.0\r\n"
        f"Authorization: Basic {credentials}\r\n"
        f"\r\n"
    ).encode()

def nmea_checksum(sentence):
    chk = 0
    for c in sentence:
        chk ^= ord(c)
    return f"{chk:02X}"

def build_gga(lat, lon, alt):
    lat_abs = abs(lat)
    lat_deg = int(lat_abs)
    lat_min = (lat_abs - lat_deg) * 60
    lat_str = f"{lat_deg:02d}{lat_min:08.5f}"
    lat_hem = "N" if lat >= 0 else "S"

    lon_abs = abs(lon)
    lon_deg = int(lon_abs)
    lon_min = (lon_abs - lon_deg) * 60
    lon_str = f"{lon_deg:03d}{lon_min:08.5f}"
    lon_hem = "E" if lon >= 0 else "W"

    utc = time.strftime("%H%M%S.00", time.gmtime())
    body = f"GPGGA,{utc},{lat_str},{lat_hem},{lon_str},{lon_hem},1,08,1.0,{alt:.1f},M,0.0,M,,"
    return f"${body}*{nmea_checksum(body)}\r\n"

def parse_rtcm_type(data, offset):
    if offset + 3 > len(data):
        return None, offset + 1
    if data[offset] != 0xD3:
        return None, offset + 1
    length = ((data[offset + 1] & 0x03) << 8) | data[offset + 2]
    if offset + 3 + length + 3 > len(data):
        return None, offset
    msg_type = ((data[offset + 3] << 4) | (data[offset + 4] >> 4)) & 0x0FFF
    return msg_type, offset + 3 + length + 3

# ── Main stream ───────────────────────────────────────────────────────────────

def stream_ntrip(cfg):
    host     = cfg["host"]
    port     = int(cfg["port"])
    duration = int(cfg["duration"])
    lat      = float(cfg["lat"])
    lon      = float(cfg["lon"])
    alt      = float(cfg["alt"])

    print(f"\nConnecting to {host}:{port} → /{cfg['mountpoint']} ...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)

    msg_counts = {}
    byte_total = 0

    try:
        sock.connect((host, port))
        sock.sendall(build_request(cfg))

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = sock.recv(1024)
            if not chunk:
                raise ConnectionError("Connection closed before header received.")
            header += chunk

        header_text, _, leftover = header.partition(b"\r\n\r\n")
        print("\n── Server Response ──────────────────────────────")
        print(header_text.decode(errors="replace"))
        print("─────────────────────────────────────────────────\n")

        first_line = header_text.split(b"\r\n")[0].decode()
        if "200" not in first_line and "ICY 200" not in first_line:
            print(f"❌ Auth/connection failed: {first_line}")
            return

        gga = build_gga(lat, lon, alt)
        sock.sendall(gga.encode())
        print(f"📍 Sent GGA: {gga.strip()}")
        print("✅ Connected! Streaming RTCM data...\n")

        sock.settimeout(5)
        buffer     = leftover
        byte_total = len(leftover)
        start      = time.time()

        while True:
            if duration > 0 and (time.time() - start) >= duration:
                break
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("Connection closed by server.")
                    break
                buffer     += chunk
                byte_total += len(chunk)

                offset = 0
                while offset < len(buffer):
                    msg_type, new_offset = parse_rtcm_type(buffer, offset)
                    if new_offset == offset:
                        break
                    if msg_type is not None:
                        msg_counts[msg_type] = msg_counts.get(msg_type, 0) + 1
                        elapsed = time.time() - start
                        print(f"  [{elapsed:6.1f}s] RTCM {msg_type:4d}  "
                              f"(total: {msg_counts[msg_type]:4d})  |  "
                              f"bytes received: {byte_total:,}")
                    offset = new_offset
                buffer = buffer[offset:]

            except socket.timeout:
                print("  (no data for 5s, still waiting...)")
            except KeyboardInterrupt:
                break

    except socket.timeout:
        print("❌ Connection timed out. Check host/port.")
    except ConnectionRefusedError:
        print("❌ Connection refused. Check host/port.")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        sock.close()
        print("\n── Summary ──────────────────────────────────────")
        if msg_counts:
            for mt, cnt in sorted(msg_counts.items()):
                print(f"  RTCM {mt:4d} : {cnt} messages")
        else:
            print("  No RTCM messages decoded.")
        print(f"  Total bytes received: {byte_total:,}")
        print("─────────────────────────────────────────────────")

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = get_config()
    stream_ntrip(cfg)
    input("\nPress Enter to exit...")