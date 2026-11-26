import asyncio
import json
import network
import socket
import time

# --- CONFIGURAÇÕES DO ACCESS POINT E ART-NET ---
AP_SSID = "Bralight_AP"
AP_PASS = "bralight123"  # Mínimo 8 caracteres
AP_CHANNEL = 6
AP_MAX_CLIENTS = 4

ARTNET_IP = "192.168.4.255"
ARTNET_PORT = 6454
ARTNET_HZ = 10
ARTNET_INTERVAL_MS = 1000 // ARTNET_HZ

STATE_FILE = "rgb_state.json"
SAVE_DELAY_MS = 2000


class LuminariaState:
    def __init__(self, r=255, g=255, b=255, d=255):
        self.r = r
        self.g = g
        self.b = b
        self.d = d
        self.dirty = False
        self.last_change_ms = time.ticks_ms()

    def update(self, r=None, g=None, b=None, d=None):
        changed = False
        if r is not None:
            r = max(0, min(255, int(r)))
            if r != self.r:
                self.r = r
                changed = True
        if g is not None:
            g = max(0, min(255, int(g)))
            if g != self.g:
                self.g = g
                changed = True
        if b is not None:
            b = max(0, min(255, int(b)))
            if b != self.b:
                self.b = b
                changed = True
        if d is not None:
            d = max(0, min(255, int(d)))
            if d != self.d:
                self.d = d
                changed = True

        if changed:
            self.dirty = True
            self.last_change_ms = time.ticks_ms()

    def to_dict(self):
        return {"r": self.r, "g": self.g, "b": self.b, "d": self.d}

    def to_bytes(self):
        return bytes([self.r, self.g, self.b, self.d])

    def load(self):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            self.r = max(0, min(255, int(data.get("r", self.r))))
            self.g = max(0, min(255, int(data.get("g", self.g))))
            self.b = max(0, min(255, int(data.get("b", self.b))))
            self.d = max(0, min(255, int(data.get("d", self.d))))
            self.dirty = False
            print("Estado restaurado de", STATE_FILE, ":", self.to_dict())
        except OSError:
            print("Nenhum estado salvo encontrado; usando padrao:", self.to_dict())
        except Exception as e:
            print("Erro ao carregar estado:", e)

    def save_if_needed(self):
        if not self.dirty:
            return
        if time.ticks_diff(time.ticks_ms(), self.last_change_ms) >= SAVE_DELAY_MS:
            try:
                with open(STATE_FILE, "w") as f:
                    json.dump(self.to_dict(), f)
                self.dirty = False
                print("Estado salvo em", STATE_FILE, ":", self.to_dict())
            except Exception as e:
                print("Erro ao salvar estado:", e)


def iniciar_access_point():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(
        essid=AP_SSID,
        password=AP_PASS,
        authmode=network.AUTH_WPA_WPA2_PSK,
        channel=AP_CHANNEL,
        max_clients=AP_MAX_CLIENTS,
    )

    while not ap.active():
        time.sleep_ms(100)

    ip = ap.ifconfig()[0]
    print("\n--- Bralight Access Point Iniciado ---")
    print("SSID:", AP_SSID)
    print("Canal:", AP_CHANNEL, "| Max clientes:", AP_MAX_CLIENTS)
    print("IP local (Web):", ip)
    return ip


def parse_query_params(raw_target):
    if "?" not in raw_target:
        return {}
    query = raw_target.split("?", 1)[1]
    params = {}
    for pair in query.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
    return params


def content_type_for(path):
    if path.endswith(".html"):
        return "text/html; charset=utf-8"
    if path.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if path.endswith(".css"):
        return "text/css; charset=utf-8"
    if path.endswith(".json"):
        return "application/json; charset=utf-8"
    return "application/octet-stream"


async def send_file_chunked(writer, filepath):
    ctype = content_type_for(filepath)
    header = (
        "HTTP/1.1 200 OK\r\n"
        f"Content-Type: {ctype}\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8")
    writer.write(header)
    await writer.drain()

    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(512)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()


async def handle_client(reader, writer, state):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        if not request_line:
            return

        # Consome o restante dos cabeçalhos HTTP
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if not line or line == b"\r\n":
                break

        parts = request_line.decode("utf-8", "ignore").strip().split(" ")
        target = parts[1] if len(parts) >= 2 else "/"
        path = target.split("?", 1)[0]

        if path == "/":
            await send_file_chunked(writer, "index.html")

        elif path == "/site.js":
            await send_file_chunked(writer, "site.js")

        elif path == "/favicon.ico":
            writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
            await writer.drain()

        elif path == "/state":
            body = json.dumps(state.to_dict()).encode("utf-8")
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            writer.write(resp)
            await writer.drain()

        elif path == "/set":
            params = parse_query_params(target)
            try:
                r = int(params["r"]) if "r" in params else None
                g = int(params["g"]) if "g" in params else None
                b = int(params["b"]) if "b" in params else None
                d = int(params["d"]) if "d" in params else None
                state.update(r=r, g=g, b=b, d=d)
                writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except ValueError:
                writer.write(
                    b"HTTP/1.1 400 Bad Request\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Connection: close\r\n\r\nParametros invalidos"
                )
                await writer.drain()

        else:
            writer.write(
                b"HTTP/1.1 404 Not Found\r\n"
                b"Content-Type: text/plain\r\n"
                b"Connection: close\r\n\r\nNot found"
            )
            await writer.drain()

    except asyncio.TimeoutError:
        pass
    except OSError as e:
        print("Erro de socket HTTP:", e)
    except Exception as e:
        print("Erro na requisicao HTTP:", e)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def artnet_e_persistencia_task(state):
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    while True:
        try:
            udp.sendto(state.to_bytes(), (ARTNET_IP, ARTNET_PORT))
        except OSError as e:
            print("Erro ao enviar UDP Art-Net:", e)

        state.save_if_needed()
        await asyncio.sleep_ms(ARTNET_INTERVAL_MS)


async def main_async():
    state = LuminariaState()
    state.load()

    ip_local = iniciar_access_point()
    asyncio.create_task(artnet_e_persistencia_task(state))

    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, state),
        "0.0.0.0",
        80,
        backlog=4,
    )
    print(f"Servidor HTTP ativo em http://{ip_local}/")

    async with server:
        await server.serve_forever()


try:
    asyncio.run(main_async())
finally:
    asyncio.new_event_loop()