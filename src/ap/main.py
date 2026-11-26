# main.py - ESP32 padrao (MicroPython)
# Access Point Wi-Fi + pagina web (roda de cores e intensidade) + transmissor Art-Net.
#
# Arquivos que precisam estar na flash desta placa, na raiz:
#     main.py   index.html   site.js
#
# Rotas HTTP:
#     /                  -> index.html
#     /site.js           -> site.js
#     /set?r=&g=&b=&d=   -> atualiza o estado (cor base em brilho maximo + intensidade), 204
#     /state             -> estado atual em JSON
#     /favicon.ico       -> 204
#
# Art-Net: a ~10 Hz, ArtDmx para o broadcast da rede do AP (192.168.4.255:6454)
# com 4 slots a partir do endereco DMX: R, G, B, Intensidade.
# O ESP32-C3 (c3/main.py) faz a mistura da intensidade, o gamma e o fade.

import asyncio
import json
import machine
import network
import os
import socket
import struct
import time
from machine import WDT

# ======================= CONFIGURACAO =======================
AP_SSID = "Bralight_AP"
AP_PASS = "bralight123"          # minimo 8 caracteres
AP_CHANNEL = 6
AP_MAX_CLIENTS = 4

HTTP_PORT = 80

ARTNET_PORT = 6454
UNIVERSO = 0
ENDERECO_DMX = 1                 # 1-based; tem que ser o mesmo do c3/main.py
ENVIOS_POR_SEGUNDO = 10

ARQUIVO_ESTADO = "rgb_state.json"
SALVAR_APOS_MS = 2000            # so grava na flash depois de 2 s sem mudancas

TIMEOUT_HTTP_S = 5
USAR_WDT = True                  # em desenvolvimento, ponha False (o WDT nao pode ser parado)
WDT_MS = 8000

# ======================= ESTADO =======================
CHAVES = ("r", "g", "b", "d")
estado = {"r": 255, "g": 0, "b": 0, "d": 255}
meta = {"t_alteracao": 0}


def _byte(valor):
    return max(0, min(255, int(valor)))


def carregar_estado():
    try:
        with open(ARQUIVO_ESTADO) as f:
            salvo = json.load(f)
        for k in CHAVES:
            if k in salvo:
                estado[k] = _byte(salvo[k])
    except (OSError, ValueError, TypeError):
        pass


async def tarefa_salvar_estado():
    ultimo_salvo = dict(estado)
    while True:
        await asyncio.sleep_ms(500)
        parado = time.ticks_diff(time.ticks_ms(), meta["t_alteracao"]) > SALVAR_APOS_MS
        if parado and estado != ultimo_salvo:
            try:
                with open(ARQUIVO_ESTADO, "w") as f:
                    json.dump(estado, f)
                ultimo_salvo = dict(estado)
            except OSError as e:
                print("Falha ao salvar estado:", e)


# ======================= ACCESS POINT =======================
def iniciar_ap():
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
    ip, mascara = ap.ifconfig()[0], ap.ifconfig()[1]
    print("--- Bralight AP ---")
    print("SSID:", AP_SSID, "| canal:", AP_CHANNEL, "| IP:", ip)
    return ip, mascara


def calcular_broadcast(ip, mascara):
    i = [int(x) for x in ip.split(".")]
    m = [int(x) for x in mascara.split(".")]
    return ".".join(str(i[k] | (255 - m[k])) for k in range(4))


# ======================= ART-NET =======================
def artdmx(seq, universo, slots):
    if len(slots) % 2:
        slots += b"\x00"
    return (
        b"Art-Net\x00\x00\x50\x00\x0e"
        + bytes([seq & 255, 0, universo & 255, (universo >> 8) & 127])
        + struct.pack(">H", len(slots))
        + slots
    )


async def tarefa_artnet(destino_ip):
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass    # nem todo build do MicroPython expoe SO_BROADCAST; o envio costuma funcionar assim mesmo
    seq = 0
    erro_anterior = None
    intervalo = 1000 // ENVIOS_POR_SEGUNDO
    while True:
        seq = (seq % 255) + 1
        slots = bytes(ENDERECO_DMX - 1) + bytes(
            (estado["r"], estado["g"], estado["b"], estado["d"])
        )
        try:
            udp.sendto(artdmx(seq, UNIVERSO, slots), (destino_ip, ARTNET_PORT))
            erro_anterior = None
        except OSError as e:
            if str(e) != erro_anterior:       # nao inunda o console
                print("Falha ao enviar Art-Net:", e)
                erro_anterior = str(e)
        await asyncio.sleep_ms(intervalo)


# ======================= SERVIDOR HTTP =======================
STATUS = {200: "OK", 204: "No Content", 400: "Bad Request", 404: "Not Found"}


def cabecalho(codigo, tipo=None, tamanho=0):
    h = "HTTP/1.1 %d %s\r\n" % (codigo, STATUS[codigo])
    if tipo:
        h += "Content-Type: %s\r\n" % tipo
    h += "Content-Length: %d\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n" % tamanho
    return h.encode()


async def responder(writer, codigo, tipo=None, corpo=b""):
    writer.write(cabecalho(codigo, tipo, len(corpo)))
    if corpo:
        writer.write(corpo)
    await writer.drain()


async def enviar_arquivo(writer, caminho, tipo):
    try:
        tamanho = os.stat(caminho)[6]
        f = open(caminho, "rb")
    except OSError:
        await responder(writer, 404, "text/plain", b"arquivo nao encontrado")
        return
    try:
        writer.write(cabecalho(200, tipo, tamanho))
        await writer.drain()
        while True:
            bloco = f.read(1024)
            if not bloco:
                break
            writer.write(bloco)
            await writer.drain()
    finally:
        f.close()


def parse_query(alvo):
    params = {}
    if "?" in alvo:
        for parte in alvo.split("?", 1)[1].split("&"):
            if "=" in parte:
                k, v = parte.split("=", 1)
                params[k] = v
    return params


async def tratar_cliente(reader, writer):
    try:
        linha = await asyncio.wait_for(reader.readline(), TIMEOUT_HTTP_S)
        partes = linha.split()
        if len(partes) < 2:
            return                      # conexao vazia (preconnect do navegador)
        while True:                     # descarta os cabecalhos
            h = await asyncio.wait_for(reader.readline(), TIMEOUT_HTTP_S)
            if not h or h == b"\r\n" or h == b"\n":
                break

        alvo = partes[1].decode()
        caminho = alvo.split("?", 1)[0]

        if caminho == "/" or caminho == "/index.html":
            await enviar_arquivo(writer, "index.html", "text/html; charset=utf-8")
        elif caminho == "/site.js":
            await enviar_arquivo(writer, "site.js", "application/javascript; charset=utf-8")
        elif caminho == "/set":
            params = parse_query(alvo)
            novo = dict(estado)
            try:
                for k in CHAVES:
                    if k in params:
                        novo[k] = _byte(params[k])
            except ValueError:
                await responder(writer, 400, "text/plain", b"parametros invalidos")
                return
            if novo != estado:
                estado.update(novo)
                meta["t_alteracao"] = time.ticks_ms()
            await responder(writer, 204)
        elif caminho == "/state":
            await responder(writer, 200, "application/json", json.dumps(estado).encode())
        elif caminho == "/favicon.ico":
            await responder(writer, 204)
        else:
            await responder(writer, 404, "text/plain", b"nao encontrado")
    except Exception as e:              # inclui timeout e cliente que desconectou
        if not isinstance(e, OSError):
            print("Erro HTTP:", repr(e))
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ======================= PRINCIPAL =======================
async def tarefa_wdt(wdt):
    while True:
        wdt.feed()
        await asyncio.sleep_ms(1000)


async def principal():
    carregar_estado()
    ip, mascara = iniciar_ap()
    broadcast = calcular_broadcast(ip, mascara)

    await asyncio.start_server(tratar_cliente, "0.0.0.0", HTTP_PORT)
    print("Pagina: http://%s/  | Art-Net ->" % ip, broadcast, ARTNET_PORT)

    asyncio.create_task(tarefa_artnet(broadcast))
    asyncio.create_task(tarefa_salvar_estado())
    if USAR_WDT:
        asyncio.create_task(tarefa_wdt(WDT(timeout=WDT_MS)))

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except Exception as e:
        print("Erro fatal:", e)
        time.sleep(2)
        machine.reset()