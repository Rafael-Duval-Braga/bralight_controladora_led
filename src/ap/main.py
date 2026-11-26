import network
import time
import socket
import struct

# --- CONFIGURAÇÕES DO ACCESS POINT ---
AP_SSID = "Bralight_AP"
AP_PASS = "bralight123"  # Mínimo 8 caracteres
MQTT_TOPIC_RGB = "casa/luminaria/rgb"


def iniciar_access_point():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASS, authmode=network.AUTH_WPA_WPA2_PSK)

    while not ap.active():
        time.sleep(0.1)

    ip = ap.ifconfig()[0]
    print("\n--- Bralight Access Point Iniciado ---")
    print("SSID:", AP_SSID)
    print("Senha:", AP_PASS)
    print("IP local (Web + Broker):", ip)
    return ip


# --- MINI BROKER MQTT EM PYTHON PURO ---
class MiniMQTTBroker:
    def __init__(self, port=1883):
        self.port = port
        self.clients = []        # Lista de sockets MQTT conectados
        self.subscriptions = {}  # {client_socket: [lista_de_topicos]}

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.listen(5)
        self.sock.setblocking(False)
        print(f"Broker MQTT escutando na porta {self.port}...")

    def _encode_remaining_length(self, length):
        encoded = bytearray()
        while True:
            digit = length % 128
            length //= 128
            if length > 0:
                digit |= 0x80
            encoded.append(digit)
            if length == 0:
                break
        return encoded

    def _decode_remaining_length(self, sock):
        multiplier = 1
        value = 0
        while True:
            b = sock.recv(1)
            if not b:
                return None
            digit = b[0]
            value += (digit & 127) * multiplier
            if (digit & 128) == 0:
                break
            multiplier *= 128
        return value

    def publish(self, topic, payload):
        """Publica uma mensagem diretamente para clientes MQTT inscritos (usado pela Web API)."""
        topic_bytes = topic.encode()
        payload_bytes = payload.encode()
        var_header = struct.pack("!H", len(topic_bytes)) + topic_bytes
        remaining = var_header + payload_bytes
        packet = b"\x30" + self._encode_remaining_length(len(remaining)) + remaining

        print(f"[WEB -> {topic}]: {payload}")
        for client, topics in list(self.subscriptions.items()):
            if topic in topics or "#" in topics:
                try:
                    client.send(packet)
                except Exception:
                    self._remove_client(client)

    def _remove_client(self, client):
        if client in self.clients:
            self.clients.remove(client)
        if client in self.subscriptions:
            del self.subscriptions[client]
        try:
            client.close()
        except Exception:
            pass
        print("Cliente MQTT desconectado.")

    def loop(self):
        # 1. Aceita novas conexões MQTT
        try:
            conn, addr = self.sock.accept()
            conn.setblocking(False)
            self.clients.append(conn)
            self.subscriptions[conn] = []
            print("Novo cliente MQTT conectado:", addr[0])
        except OSError:
            pass

        # 2. Processa pacotes dos clientes conectados
        for client in list(self.clients):
            try:
                header = client.recv(1)
                if not header:
                    self._remove_client(client)
                    continue

                pkt_type = header[0] >> 4
                rem_len = self._decode_remaining_length(client)
                if rem_len is None:
                    self._remove_client(client)
                    continue

                data = b""
                while len(data) < rem_len:
                    chunk = client.recv(rem_len - len(data))
                    if not chunk:
                        break
                    data += chunk

                # CONNECT (1) -> Responde CONNACK
                if pkt_type == 1:
                    client.send(b"\x20\x02\x00\x00")

                # PUBLISH (3) -> Encaminha para inscritos
                elif pkt_type == 3:
                    topic_len = struct.unpack("!H", data[:2])[0]
                    topic = data[2 : 2 + topic_len].decode()
                    qos = (header[0] & 0x06) >> 1
                    idx = 2 + topic_len
                    if qos > 0:
                        pkt_id = data[idx : idx + 2]
                        idx += 2
                        client.send(b"\x40\x02" + pkt_id)  # PUBACK
                    payload = data[idx:].decode()
                    print(f"[{topic}]: {payload}")
                    self.publish(topic, payload)

                # SUBSCRIBE (8) -> Registra tópico e responde SUBACK
                elif pkt_type == 8:
                    pkt_id = data[:2]
                    idx = 2
                    ack_codes = bytearray()
                    while idx < len(data):
                        t_len = struct.unpack("!H", data[idx : idx + 2])[0]
                        topic = data[idx + 2 : idx + 2 + t_len].decode()
                        self.subscriptions[client].append(topic)
                        print(f"Cliente inscrito em: {topic}")
                        ack_codes.append(0x00)
                        idx += 2 + t_len + 1
                    rem = pkt_id + ack_codes
                    client.send(b"\x90" + self._encode_remaining_length(len(rem)) + rem)

                # PINGREQ (12) -> Responde PINGRESP
                elif pkt_type == 12:
                    client.send(b"\xd0\x00")

                # DISCONNECT (14)
                elif pkt_type == 14:
                    self._remove_client(client)

            except OSError:
                pass
            except Exception as e:
                print("Erro no cliente MQTT:", e)
                self._remove_client(client)


# --- SERVIDOR WEB HTTP LEVE (PORTA 80) ---
HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Bralight RGB</title>
  <style>
    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: #121212;
      color: #f4f4f5;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      margin: 0;
    }
    .card {
      background: #1e1e24;
      padding: 24px;
      border-radius: 16px;
      width: 100%;
      max-width: 340px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
      text-align: center;
    }
    h1 { margin: 0 0 6px; font-size: 1.4rem; }
    p { margin: 0 0 20px; color: #a1a1aa; font-size: 0.9rem; }
    input[type="color"] {
      width: 120px;
      height: 120px;
      border: none;
      background: none;
      cursor: pointer;
      margin-bottom: 16px;
    }
    .rgb-readout {
      font-family: monospace;
      background: #121212;
      padding: 8px 12px;
      border-radius: 8px;
      margin-bottom: 18px;
      font-size: 0.95rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-bottom: 12px;
    }
    button {
      border: none;
      border-radius: 10px;
      padding: 10px 8px;
      font-weight: 600;
      cursor: pointer;
      color: #fff;
      background: #2e2e38;
    }
    button:active { transform: scale(0.97); }
    .off { width: 100%; background: #3f3f46; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Bralight Control</h1>
    <p>AP Local · Tópico: casa/luminaria/rgb</p>
    <input id="picker" type="color" value="#ff0000" />
    <div id="readout" class="rgb-readout">RGB: 255, 0, 0</div>
    <div class="grid">
      <button style="background:#dc2626" onclick="setRGB(255,0,0)">Vermelho</button>
      <button style="background:#16a34a" onclick="setRGB(0,255,0)">Verde</button>
      <button style="background:#2563eb" onclick="setRGB(0,0,255)">Azul</button>
    </div>
    <button class="off" onclick="setRGB(0,0,0)">Desligar (0,0,0)</button>
  </div>

  <script>
    const picker = document.getElementById("picker");
    const readout = document.getElementById("readout");
    let timer = null;

    function hexToRgb(hex) {
      const n = parseInt(hex.slice(1), 16);
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }

    function sendCommand(r, g, b) {
      readout.textContent = `RGB: ${r}, ${g}, ${b}`;
      fetch(`/rgb?r=${r}&g=${g}&b=${b}`).catch(() => {});
    }

    function setRGB(r, g, b) {
      const hex = "#" + [r, g, b].map(x => x.toString(16).padStart(2, "0")).join("");
      picker.value = hex;
      sendCommand(r, g, b);
    }

    picker.addEventListener("input", () => {
      const [r, g, b] = hexToRgb(picker.value);
      readout.textContent = `RGB: ${r}, ${g}, ${b}`;
      clearTimeout(timer);
      timer = setTimeout(() => sendCommand(r, g, b), 80);
    });
  </script>
</body>
</html>
"""


class MiniWebServer:
    def __init__(self, broker, port=80):
        self.broker = broker
        self.port = port

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.listen(4)
        self.sock.setblocking(False)
        print(f"Servidor Web escutando na porta {self.port}...")

    def _parse_rgb_query(self, path):
        # Ex.: /rgb?r=255&g=10&b=0
        if "?" not in path:
            return None
        query = path.split("?", 1)[1]
        params = {}
        for part in query.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v
        try:
            r = max(0, min(255, int(params.get("r", 0))))
            g = max(0, min(255, int(params.get("g", 0))))
            b = max(0, min(255, int(params.get("b", 0))))
            return r, g, b
        except ValueError:
            return None

    def loop(self):
        try:
            conn, addr = self.sock.accept()
            conn.settimeout(1.0)
        except OSError:
            return

        try:
            req = conn.recv(1024).decode("utf-8", "ignore")
            first_line = req.splitlines()[0] if req else ""
            parts = first_line.split(" ")
            path = parts[1] if len(parts) >= 2 else "/"

            if path.startswith("/rgb"):
                rgb = self._parse_rgb_query(path)
                if rgb is not None:
                    payload = f"{rgb[0]},{rgb[1]},{rgb[2]}"
                    self.broker.publish(MQTT_TOPIC_RGB, payload)
                    body = f'{{"status":"ok","rgb":"{payload}"}}'
                    resp = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: application/json\r\n"
                        "Connection: close\r\n\r\n" + body
                    )
                else:
                    resp = (
                        "HTTP/1.1 400 Bad Request\r\n"
                        "Content-Type: text/plain\r\n"
                        "Connection: close\r\n\r\nParametros r,g,b invalidos"
                    )
            else:
                body = HTML_PAGE.encode("utf-8")
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    b"Connection: close\r\n\r\n" + body
                )

            conn.sendall(resp if isinstance(resp, bytes) else resp.encode("utf-8"))
        except Exception as e:
            print("Erro na requisicao HTTP:", e)
        finally:
            try:
                conn.close()
            except Exception:
                pass


# --- EXECUÇÃO PRINCIPAL ---
ip_local = iniciar_access_point()
broker = MiniMQTTBroker(port=1883)
broker.start()

web = MiniWebServer(broker, port=80)
web.start()
print(f"Acesse a interface em: http://{ip_local}/")

while True:
    broker.loop()
    web.loop()
    time.sleep_ms(10)