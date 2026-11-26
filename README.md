## Arquitetura AP Web + Art-Net (`feat/ap-web-artnet`)

A arquitetura substitui o broker MQTT local por uma rede autônoma em **Wi-Fi SoftAP (`802.11`)** com **HTTP/1.1 assíncrono (`uasyncio`)** no frontend e **UDP broadcast na porta `6454`** (porta padrão do protocolo **Art-Net**, enviando aqui quadros compactos de 4 bytes `bytes([r, g, b, d])` em vez do cabeçalho Art-Net completo de 18+ bytes) entre duas placas rodando **MicroPython**:

- **ESP32 padrão — Nó mestre / Access Point (`src/ap/main.py`, `src/ap/index.html`, `src/ap/site.js`):**
  - **Rede local autônoma:** atua como Access Point (`SSID: Bralight_AP`, senha `bralight123`, canal `6`, até `4` clientes simultâneos, IP `192.168.4.1`), eliminando dependência de roteador externo, internet ou broker MQTT.
  - **Servidor HTTP assíncrono (`uasyncio`):** escuta na porta `80` e entrega os arquivos estáticos (`/` → `index.html` e `/site.js`) lidos em blocos a partir da *flash* para reduzir o consumo de RAM.
  - **API HTTP de controle e estado:**
    - `GET /state`: retorna o estado atual (`r`, `g`, `b`, `d` de `0` a `255`) na inicialização da página.
    - `GET /set?r=...&g=...&b=...&d=...`: atualiza os valores em memória e responde `204 No Content`.
  - **Interface web (`<canvas>`):** renderiza uma roda de cores em brilho máximo (`r`, `g`, `b`) com cache de *canvas* para manter fluidez ao toque e controle independente de intensidade (`d`), aplicando *throttle* de ~`15 Hz` nas requisições `/set` e controle de concorrência para não empilhar chamadas HTTP.
  - **Transmissão UDP (~`10 Hz`):** tarefa assíncrona dedicada envia pacotes UDP de 4 bytes `bytes([r, g, b, d])` em *broadcast* para `192.168.4.255:6454`, garantindo atualização periódica mesmo sem novos toques na interface.
  - **Persistência em *flash*:** salva o último estado em `rgb_state.json` após ~`2 s` sem alterações (evitando escritas excessivas durante o arraste contínuo da roda/dimmer) e restaura os valores no *boot*.

- **ESP32-C3 SuperMini — Nó escravo / Receptor & Dimmer (`src/c3/main.py`):**
  - **Conexão e recepção UDP:** conecta-se ao Wi-Fi `Bralight_AP` e escuta pacotes UDP na porta `6454`, interpretando os 4 bytes recebidos (`r`, `g`, `b`, `d`) como canais de cor e intensidade global.
  - **Processamento e acionamento PWM:** aplica correção *gamma* (para linearizar a percepção luminosa), escala pela intensidade `d`, executa *fade* suave entre estados e aciona as saídas PWM da luminária.
  - **Resiliência:** lida com reconexão Wi-Fi, perda de sinal/timeout de pacotes e supervisão por *watchdog* (`WDT`) quando habilitado.

### Fluxo de comunicação

```text
[Celular / PC no Bralight_AP]
       │  HTTP GET /state  |  HTTP GET /set?r=&g=&b=&d= (~15 Hz)
       ▼
[ESP32 Padrão - 192.168.4.1:80]
       │  Salva rgb_state.json (~2 s após mudança)
       │  UDP Broadcast bytes([r, g, b, d]) -> 192.168.4.255:6454 (~10 Hz)
       ▼
[ESP32-C3 SuperMini - Porta UDP 6454]
       │  Gamma + Intensidade (d) + Fade suave
       ▼
[Saídas PWM / Luminária RGB]