# DECTroller

**Turn a telephone keypad into a network controller with Python.**

DECTroller is an experimental Python project that translates DTMF keypad tones from a compatible analog telephone/cordless-phone setup into commands for a Roku device on the same local network.

The current prototype calls a SIP-capable analog telephone adapter (ATA), receives RFC 4733 telephone-event DTMF packets over RTP, and sends Roku External Control Protocol (ECP) HTTP requests. It does not require Asterisk or `tcpdump`.

> **Compatibility note:** This is not a way to connect any telephone directly to a computer. You need a compatible SIP-capable ATA, an analog telephone connected to it, network access between the ATA and computer, and a Roku model/software configuration that accepts ECP commands. The original prototype was tested with a Cisco SPA2102 and a Panasonic cordless-phone system; other hardware may behave differently.

## Features

- Uses Python's standard library; no third-party Python packages are required.
- Reads telephone keypad DTMF events carried over RTP.
- Sends commands to Roku ECP over your local network.
- Directional keys use `keydown`/`keyup`; action keys use `keypress`.
- Does not require Asterisk or packet-capture tools.

## Key mapping

| Telephone key | Roku action |
| --- | --- |
| `2` | Up (held while the key is held) |
| `8` | Down (held while the key is held) |
| `4` | Left (held while the key is held) |
| `6` | Right (held while the key is held) |
| `5` | Select |
| `0` | Back |
| `*` | Home |

Mappings are currently defined in `dectroller.py` near the top of the file.

## Requirements

- Python 3.9 or newer is recommended.
- A Linux computer with a fixed/reserved LAN address or a known LAN address.
- A SIP-capable analog telephone adapter configured to call the phone line/extension without requiring authentication that this prototype does not implement.
- An analog phone or cordless-phone base connected to that ATA.
- A Roku reachable on the same trusted LAN, with **Settings → System → Advanced system settings → Control by mobile apps** enabled.
- The adapter must send DTMF using RTP telephone-event (RFC 4733), with a payload type matching the configured value (default `101`).

This is a prototype, not a general-purpose SIP stack. SIP behavior, RTP negotiation, adapter settings, firewall rules, and network interfaces vary. It may need changes for other equipment.

## Setup

1. Connect the analog phone/base to the ATA's phone port and connect the ATA and computer to your LAN.
2. On the Roku, enable **Control by mobile apps** in the system settings.
3. Find the LAN IP addresses for the ATA, computer, and Roku. The computer address passed as `--pc-ip` must be reachable by the ATA; it is used for SIP/RTP binding and SDP negotiation.
4. Confirm the ATA's SIP port, line/extension User ID, and the DTMF payload type. The defaults in this prototype are SIP port `5060`, Roku ECP port `8060`, and DTMF payload type `101`.
5. Save `dectroller.py` on the computer.

No external Python packages are needed.

## Run

Replace the example addresses and IDs below with your own local settings:

```bash
python3 dectroller.py \
  --ata-ip 192.168.1.28 \
  --pc-ip 192.168.1.33 \
  --extension 102 \
  --caller-id 101 \
  --roku-ip 192.168.1.10
```

Optional arguments:

```text
--sip-port PORT             ATA SIP port (default: 5060)
--roku-port PORT            Roku ECP port (default: 8060)
--ring-timeout SECONDS      Wait for phone answer (default: 30)
--dtmf-payload-type NUMBER  RTP telephone-event payload type (default: 101)
```

The program calls the configured phone line. Answer the handset when it rings. Use the keypad mapping above to control the Roku. To stop, press `Ctrl+C` in the terminal; the script attempts to release held keys and end the SIP call.

If it cannot bind to the supplied computer IP, check that the address belongs to the computer and is on the active interface. If the Roku returns `403 Forbidden`, check the Roku's **Control by mobile apps** setting. If no keypad events are received, verify the ATA's RTP/DTMF configuration and any local firewall rules.

## Network and security notes

- **Use only on devices and networks you own or are authorized to administer.**
- This prototype is intended for a trusted local network. Do not port-forward or expose the ATA's SIP/RTP ports or Roku ECP port `8060` to the public internet.
- Roku ECP commands are sent over plain HTTP on the LAN. Anyone who can reach an enabled ECP interface may be able to control that Roku; isolate trusted devices appropriately.
- This prototype does not implement SIP authentication, TLS, SRTP, a complete SIP state machine, or robust call-state recovery. Configure your ATA and LAN accordingly, and do not treat the script as a secure public-facing telephony service.
- The script uses IP addresses and phone identifiers supplied by the person running it; do not publish credentials or private configuration in issue reports.

## Scope and limitations

This repository currently contains a proof-of-concept Roku controller. It does not include the separate mouse-control/game controller unless that script is added and documented separately. The script's SIP/RTP handling is deliberately small and may not work with every ATA, firmware, or DTMF transport mode.

## License

This project is licensed under the **GNU General Public License v3.0 or later**.

## Trademarks and affiliation

Roku is a trademark of its respective owner. This is an independent community project and is not affiliated with, endorsed by, or sponsored by Roku, Cisco, Panasonic, or any other hardware manufacturer. Product names are used only to identify compatible equipment.

## Contributions

Bug reports and compatibility notes are welcome. Please do not include passwords, SIP credentials, public IP addresses, or other sensitive network information in issues or logs.

**Note:** On Roku OS 14.1 and later, enable **Control by mobile apps** under *Settings → System → Advanced system settings* for network control commands to work.

*Concept and hardware testing by the maintainer; Python implementation developed with assistance from OpenAI's ChatGPT.*
