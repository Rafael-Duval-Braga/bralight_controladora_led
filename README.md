## Arquitetura AP Web + Art-Net (`feat/ap-web-artnet`)

A arquitetura foi dividida entre duas placas rodando MicroPython:

* **ESP32 padrão (`src/ap/main.py`, `src/ap/index.html`, `src/ap/site.js`):**
  * Cria o Access Point Wi-Fi (`Bralight_AP`, canal `6`, até `4` clientes) sem necessidade de internet ou broker MQTT local.
  * Serve a interface web em `<canvas>` (`/` e `/site.js`) lida em blocos a partir da flash, com roda de cores em brilho máximo (`r`, `g`, `b`) e controle separado de intensidade (`d`), usando *throttle* de ~15 Hz em `/set` e consulta inicial em `/state`.
  * Envia quadros UDP a 10 Hz para `192.168.4.255:6454` no formato `bytes([r, g, b, d])` e persiste o último estado em `rgb_state.json` após ~2 s sem alterações.
* **ESP32-C3 SuperMini (`src/c3/main.py`):**
  * Recebe os comandos e realiza a dimerização/correção gamma e o acionamento das saídas da luminária.