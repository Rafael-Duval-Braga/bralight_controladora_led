import network
import time
import socket
import struct

# --- CONFIGURAÇÕES DO ACCESS POINT ---
AP_SSID = "Bralight_AP"
AP_PASS = "bralight123"  # Mínimo 8 caracteres

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
    print("IP do Broker MQTT:", ip)
    return ip

# --- MINI BROKER MQTT EM PYTHON PURO (Sem dependências externas) ---
class MiniMQTTBroker:
    def __init__(self, port=1883):
        self.port = port
        self.clients = []       # Lista de sockets conectados
        self.subscriptions = {} # {client_socket: [lista_de_topicos]}

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', self.port))
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

    def _publish_to_subscribers(self, topic, payload):
        topic_bytes = topic.encode()
        payload_bytes = payload.encode()
        var_header = struct.pack("!H", len(topic_bytes)) + topic_bytes
        remaining = var_header + payload_bytes
        packet = b"\x30" + self._encode_remaining_length(len(remaining)) + remaining

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
        # 1. Aceita novas conexões
        try:
            conn, addr = self.sock.accept()
            conn.setblocking(False)
            self.clients.append(conn)
            self.subscriptions[conn] = []
            print("Novo cliente conectado:", addr[0])
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
                    topic = data[2:2+topic_len].decode()
                    qos = (header[0] & 0x06) >> 1
                    idx = 2 + topic_len
                    if qos > 0:
                        pkt_id = data[idx:idx+2]
                        idx += 2
                        client.send(b"\x40\x02" + pkt_id) # PUBACK
                    payload = data[idx:].decode()
                    print(f"[{topic}]: {payload}")
                    self._publish_to_subscribers(topic, payload)

                # SUBSCRIBE (8) -> Registra tópico e responde SUBACK
                elif pkt_type == 8:
                    pkt_id = data[:2]
                    idx = 2
                    ack_codes = bytearray()
                    while idx < len(data):
                        t_len = struct.unpack("!H", data[idx:idx+2])[0]
                        topic = data[idx+2:idx+2+t_len].decode()
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
                # Sem dados disponíveis no socket não-bloqueante
                pass
            except Exception as e:
                print("Erro no cliente:", e)
                self._remove_client(client)

# --- EXECUÇÃO PRINCIPAL ---
iniciar_access_point()
broker = MiniMQTTBroker(port=1883)
broker.start()

while True:
    broker.loop()
    time.sleep_ms(10)