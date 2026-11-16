import machine
import network
import time
from machine import Pin, PWM
from umqtt.simple import MQTTClient

# --- CONFIGURAÇÕES DE REDE ---
SSID_REDE = "GEDRE"
SENHA_REDE = "gedre100"

SSID_AP = "bralight_contro Fallback Hotspot"
SENHA_AP = "raySafbR1Jli"

# --- CONFIGURAÇÕES MQTT ---
MQTT_SERVER = "192.168.0.159"
MQTT_CLIENT_ID = "bralight_control_board_1"
MQTT_TOPIC_RGB = b"casa/luminaria/rgb"


def configurar_wifi():
  sta_if = network.WLAN(network.STA_IF)
  sta_if.active(True)
  sta_if.connect(SSID_REDE, SENHA_REDE)

  inicio = time.ticks_ms()
  while not sta_if.isconnected():
    if time.ticks_diff(time.ticks_ms(), inicio) > 10000:
      print("Falha ao conectar no Wi-Fi. Iniciando Access Point (Fallback)...")
      ap_if = network.WLAN(network.AP_IF)
      ap_if.active(True)
      ap_if.config(essid=SSID_AP, password=SENHA_AP)
      print("AP criado:", SSID_AP)
      return False
    time.sleep_ms(500)

  print("Conectado ao Wi-Fi!", sta_if.ifconfig())
  return True


# --- CONFIGURAÇÃO DO PWM (Canais RGB) ---
# Ajuste os números dos GPIOs conforme os pinos físicos da sua placa
PINO_RED = 0
PINO_GREEN = 1
PINO_BLUE = 3

pwm_r = PWM(Pin(PINO_RED), freq=1000)
pwm_g = PWM(Pin(PINO_GREEN), freq=1000)
pwm_b = PWM(Pin(PINO_BLUE), freq=1000)


def definir_cor(r, g, b):
  # Converte de 0-255 para 0-1023 (escala de 10 bits do PWM)
  duty_r = int((max(0, min(255, r)) / 255) * 1023)
  duty_g = int((max(0, min(255, g)) / 255) * 1023)
  duty_b = int((max(0, min(255, b)) / 255) * 1023)

  pwm_r.duty(duty_r)
  pwm_g.duty(duty_g)
  pwm_b.duty(duty_b)


# Inicia com os LEDs apagados
definir_cor(0, 0, 0)


# --- CALLBACK MQTT ---
def sub_cb(topic, msg):
  print(f"Comando recebido [{topic.decode()}]: {msg.decode()}")
  try:
    partes = msg.decode().strip().split(",")
    if len(partes) == 3:
      r = int(partes[0])
      g = int(partes[1])
      b = int(partes[2])
      definir_cor(r, g, b)
  except Exception as e:
    print("Erro ao interpretar valores RGB:", e)


# --- EXECUÇÃO PRINCIPAL ---
conectado = configurar_wifi()

if conectado:
  try:
    client = MQTTClient(MQTT_CLIENT_ID, MQTT_SERVER)
    client.set_callback(sub_cb)
    client.connect()
    client.subscribe(MQTT_TOPIC_RGB)
    print(f"Conectado ao Broker MQTT ({MQTT_SERVER})")
    print(f"Escutando tópico: {MQTT_TOPIC_RGB.decode()}")

    while True:
      client.check_msg()
      time.sleep_ms(50)

  except Exception as e:
    print("Erro na conexão MQTT:", e)
    print("Reiniciando placa em 5 segundos...")
    time.sleep(5)
    machine.reset()