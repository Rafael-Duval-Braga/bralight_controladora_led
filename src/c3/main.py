# main.py - ESP32-C3 Super Mini (MicroPython)
# Receptor Art-Net (UDP) -> PWM RGB com intensidade, gamma e fade suave.
#
# O ESP32 padrao (AP + pagina web) deve enviar pacotes ArtDmx para o
# broadcast 192.168.4.255:6454 com 4 slots seguidos a partir do endereco DMX:
#     slot 1 = R, slot 2 = G, slot 3 = B, slot 4 = Intensidade (0..255)
# Reenvie o estado atual a ~10 Hz: este codigo nao guarda cor nenhuma e a
# luminaria sempre liga apagada ate chegar o primeiro pacote.

import json
import machine
import network
import socket
import time
from machine import Pin, PWM, WDT

# ======================= CONFIGURACAO =======================
SSID_REDE = "Bralight_AP"
SENHA_REDE = "bralight123"

ARTNET_PORT = 6454
UNIVERSO = 0          # pode ser sobrescrito por config.json
ENDERECO_DMX = 1      # 1-based; usa 4 canais: R, G, B, Intensidade

PINOS_RGB = (0, 1, 3)           # evite GPIO 2, 8 e 9 (strapping / LED / BOOT)
FREQ_PWM = 2000                 # Hz (o MicroPython escolhe a maior resolucao possivel)
GAMMA = 2.2
LIMITE_CANAL = (1.0, 1.0, 1.0)  # teto por canal (0..1) para proteger o estagio de potencia

SUAVIZACAO = 0.15      # fade exponencial: maior = mais rapido
PASSO_FADE_MS = 10

PERDA_SINAL_MS = 3000
PERDA_SINAL_ACAO = "manter"     # "manter" a ultima cor ou "apagar"

USAR_WDT = True        # em desenvolvimento, ponha False (o WDT nao pode ser parado depois de iniciado)
WDT_MS = 8000

RETRY_WIFI_MS = 3000
RESET_RADIO_MS = 30000

# ======================= CONFIG OPCIONAL =======================
def carregar_config():
    """Se existir config.json, ex.: {"universo": 0, "endereco": 5}"""
    global UNIVERSO, ENDERECO_DMX
    try:
        with open("config.json") as f:
            cfg = json.load(f)
        UNIVERSO = int(cfg.get("universo", UNIVERSO))
        ENDERECO_DMX = int(cfg.get("endereco", ENDERECO_DMX))
    except (OSError, ValueError):
        pass
    if not 1 <= ENDERECO_DMX <= 509:
        ENDERECO_DMX = 1


# ======================= SAIDA PWM =======================
pwm = [PWM(Pin(p), freq=FREQ_PWM, duty_u16=0) for p in PINOS_RGB]
alvo = [0.0, 0.0, 0.0]    # 0..1, linear, ja com intensidade aplicada
atual = [0.0, 0.0, 0.0]


def aplicar_comando(r, g, b, dim):
    # Intensidade multiplica os canais em luz linear; o gamma vem depois,
    # entao a proporcao R:G:B (a cor) se mantem ao mudar a intensidade.
    k = dim / 255
    alvo[0] = (r / 255) * k * LIMITE_CANAL[0]
    alvo[1] = (g / 255) * k * LIMITE_CANAL[1]
    alvo[2] = (b / 255) * k * LIMITE_CANAL[2]


def apagar():
    alvo[0] = alvo[1] = alvo[2] = 0.0


def atualizar_saida():
    for i in range(3):
        a = atual[i]
        t = alvo[i]
        if a == t:
            continue
        a += (t - a) * SUAVIZACAO
        if abs(t - a) < 0.0005:
            a = t
        atual[i] = a
        pwm[i].duty_u16(int((a ** GAMMA) * 65535))


# ======================= ART-NET =======================
ARTNET_ID = b"Art-Net\x00"


def processar_artnet(pkt):
    """Retorna True se for um ArtDmx valido para o nosso universo e enderecamento."""
    if len(pkt) < 18 or pkt[:8] != ARTNET_ID:
        return False
    if pkt[8] != 0x00 or pkt[9] != 0x50:          # opcode 0x5000 (ArtDmx), little-endian
        return False
    universo = pkt[14] | (pkt[15] << 8)           # SubUni + Net (15 bits)
    if universo != UNIVERSO:
        return False
    comp = (pkt[16] << 8) | pkt[17]
    dmx = pkt[18:18 + comp]
    i = ENDERECO_DMX - 1
    if len(dmx) < i + 4:
        return False
    aplicar_comando(dmx[i], dmx[i + 1], dmx[i + 2], dmx[i + 3])
    return True


def abrir_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        pass
    s.bind(("0.0.0.0", ARTNET_PORT))
    s.setblocking(False)
    return s


def ler_pacotes(s):
    """Esvazia a fila (ate 8 pacotes por volta). True se algum era valido."""
    valido = False
    for _ in range(8):
        try:
            pkt = s.recv(600)
        except OSError:        # EAGAIN: sem mais pacotes
            break
        if processar_artnet(pkt):
            valido = True
    return valido


# ======================= WI-FI =======================
def configurar_radio(sta):
    sta.active(True)
    try:
        sta.config(pm=network.WLAN.PM_NONE)   # sem economia de energia = menor latencia
    except Exception:
        pass
    try:
        network.hostname("bralight-c3")
    except Exception:
        pass


# ======================= PRINCIPAL =======================
def main():
    carregar_config()
    wdt = WDT(timeout=WDT_MS) if USAR_WDT else None

    sta = network.WLAN(network.STA_IF)
    configurar_radio(sta)

    sock = None
    agora = time.ticks_ms()
    t_tentativa = time.ticks_add(agora, -RETRY_WIFI_MS)   # tenta conectar ja na 1a volta
    t_conectado = agora
    t_fade = agora
    t_pacote = None
    sinal_ok = False

    while True:
        if wdt:
            wdt.feed()
        agora = time.ticks_ms()

        # --- Wi-Fi: reconecta sem travar o resto ---
        if sta.isconnected():
            t_conectado = agora
            if sock is None:
                sock = abrir_socket()
                print("Wi-Fi OK, IP:", sta.ifconfig()[0],
                      "| Art-Net porta", ARTNET_PORT,
                      "| universo", UNIVERSO, "| endereco", ENDERECO_DMX)
        else:
            if sock is not None:
                sock.close()
                sock = None
                print("Wi-Fi caiu")
            if time.ticks_diff(agora, t_conectado) > RESET_RADIO_MS:
                print("Reiniciando radio Wi-Fi")
                sta.active(False)
                time.sleep_ms(200)
                configurar_radio(sta)
                t_conectado = agora
                t_tentativa = time.ticks_add(agora, -RETRY_WIFI_MS)
            elif (sta.status() != network.STAT_CONNECTING
                  and time.ticks_diff(agora, t_tentativa) >= RETRY_WIFI_MS):
                sta.connect(SSID_REDE, SENHA_REDE)
                t_tentativa = agora

        # --- Recepcao ---
        if sock is not None and ler_pacotes(sock):
            t_pacote = agora
            if not sinal_ok:
                sinal_ok = True
                print("Sinal Art-Net recebido")

        # --- Perda de sinal ---
        if t_pacote is not None and time.ticks_diff(agora, t_pacote) > PERDA_SINAL_MS:
            if sinal_ok:
                sinal_ok = False
                print("Sinal perdido")
            if PERDA_SINAL_ACAO == "apagar":
                apagar()

        # --- Fade / PWM ---
        if time.ticks_diff(agora, t_fade) >= PASSO_FADE_MS:
            t_fade = agora
            atualizar_saida()

        time.sleep_ms(2)


try:
    main()
except Exception as e:
    print("Erro fatal:", e)
    apagar()
    for p in pwm:
        p.duty_u16(0)
    time.sleep(2)
    machine.reset()