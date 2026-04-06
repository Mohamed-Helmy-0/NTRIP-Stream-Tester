"""
NTRIP WebSocket Bridge — server.py
Bridges the HTML UI to the NTRIP caster via a local WebSocket on port 8765.

Install dependency once:
    pip install websockets

Run:
    python server.py
Then open index.html in your browser.
"""

import asyncio, socket, base64, time, json
import websockets

CLIENTS = set()
stop_flag = False

# ── NTRIP helpers ─────────────────────────────────────────────────────────────

def build_request(cfg):
    cred = base64.b64encode(f"{cfg['user']}:{cfg['pass']}".encode()).decode()
    return (
        f"GET /{cfg['mount']} HTTP/1.0\r\n"
        f"Host: {cfg['host']}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP WebTester/1.0\r\n"
        f"Authorization: Basic {cred}\r\n"
        f"\r\n"
    ).encode()

def build_gga(lat, lon, alt):
    def nmea_cksum(s):
        c = 0
        for ch in s: c ^= ord(ch)
        return f"{c:02X}"
    la, lo = abs(lat), abs(lon)
    ls = f"{int(la):02d}{(la-int(la))*60:08.5f}"
    os_ = f"{int(lo):03d}{(lo-int(lo))*60:08.5f}"
    utc = time.strftime("%H%M%S.00", time.gmtime())
    body = f"GPGGA,{utc},{ls},{'N' if lat>=0 else 'S'},{os_},{'E' if lon>=0 else 'W'},1,08,1.0,{alt:.1f},M,0.0,M,,"
    return f"${body}*{nmea_cksum(body)}\r\n"

def parse_rtcm(data, offset):
    if offset + 5 > len(data): return None, offset
    if data[offset] != 0xD3:   return None, offset + 1
    length = ((data[offset+1] & 0x03) << 8) | data[offset+2]
    end = offset + 3 + length + 3
    if end > len(data):        return None, offset   # incomplete
    msg_type = ((data[offset+3] << 4) | (data[offset+4] >> 4)) & 0x0FFF
    return msg_type, end

# ── WebSocket handler ─────────────────────────────────────────────────────────

async def handler(websocket):
    global stop_flag
    CLIENTS.add(websocket)
    ntrip_sock = None

    async def send(obj): await websocket.send(json.dumps(obj))

    try:
        async for raw in websocket:
            msg = json.loads(raw)

            if msg['type'] == 'stop':
                stop_flag = True
                break

            if msg['type'] != 'start':
                continue

            cfg      = msg['cfg']
            duration = cfg.get('duration', 30)
            stop_flag = False

            # ── Connect ───────────────────────────────────────────────────────
            await send({'type':'status','text':f"Connecting to {cfg['host']}:{cfg['port']}…",'state':'connecting'})

            loop = asyncio.get_event_loop()
            try:
                ntrip_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                ntrip_sock.settimeout(10)
                await loop.run_in_executor(None, lambda: ntrip_sock.connect((cfg['host'], int(cfg['port']))))
                ntrip_sock.sendall(build_request(cfg))
            except Exception as e:
                await send({'type':'status','text':f"❌ {e}",'state':'error','ok':False})
                break

            # ── Read HTTP header ──────────────────────────────────────────────
            header = b""
            try:
                while b"\r\n\r\n" not in header:
                    chunk = await loop.run_in_executor(None, lambda: ntrip_sock.recv(1024))
                    if not chunk: break
                    header += chunk
            except Exception as e:
                await send({'type':'status','text':f"❌ Header read error: {e}",'state':'error','ok':False})
                break

            header_text, _, leftover = header.partition(b"\r\n\r\n")
            lines = header_text.decode(errors='replace').splitlines()
            await send({'type':'header','lines': lines})

            first = lines[0] if lines else ""
            if "200" not in first and "ICY 200" not in first:
                await send({'type':'status','text':f"❌ {first}",'state':'error','ok':False})
                break

            # ── Send GGA ──────────────────────────────────────────────────────
            gga = build_gga(cfg['lat'], cfg['lon'], cfg['alt'])
            ntrip_sock.sendall(gga.encode())
            await send({'type':'status','text':'✅ Connected — streaming RTCM…','state':'connected','ok':True})

            # ── Stream ────────────────────────────────────────────────────────
            ntrip_sock.settimeout(5)
            buffer    = leftover
            start     = time.time()

            while not stop_flag:
                if duration > 0 and (time.time() - start) >= duration:
                    await send({'type':'done','text':f"Duration of {duration}s reached."})
                    break
                try:
                    chunk = await loop.run_in_executor(None, lambda: ntrip_sock.recv(4096))
                    if not chunk:
                        await send({'type':'done','text':'Server closed connection.'})
                        break
                    buffer += chunk

                    offset = 0
                    while offset < len(buffer):
                        mt, new_off = parse_rtcm(buffer, offset)
                        if new_off == offset: break
                        if mt is not None:
                            await send({'type':'rtcm','msg_type': mt,'bytes': len(chunk)})
                        offset = new_off
                    buffer = buffer[offset:]

                except socket.timeout:
                    await send({'type':'warn','text':'No data for 5s, still waiting…'})
                except Exception as e:
                    await send({'type':'done','text':str(e)})
                    break

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if ntrip_sock:
            try: ntrip_sock.close()
            except: pass
        CLIENTS.discard(websocket)

# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    print("NTRIP WebSocket bridge running on ws://localhost:8765")
    print("Open index.html in your browser to start.\n")
    async with websockets.serve(handler, "localhost", 8765):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
