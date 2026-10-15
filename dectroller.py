#!/usr/bin/env python3
# DECTroller - telephone keypad to Roku network controller
# Copyright (C) 2026 DECTroller contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Use a compatible SIP analog telephone adapter and phone keypad to control Roku.

Calls the configured SIP analog telephone adapter directly, receives RFC 4733
telephone-event DTMF packets over RTP, and sends Roku ECP HTTP requests.
No Asterisk or tcpdump is required. The prototype was tested with a Cisco
SPA2102; other adapters may require configuration changes.
"""
import argparse
import queue
import random
import re
import socket
import string
import sys
import threading
import time
import urllib.error
import urllib.request

# ---------------- Configuration ----------------
# Supply device/network details as command-line arguments. Do not commit your
# own LAN addresses or telephony configuration to a public repository.
PHONE_IP = None
PHONE_PORT = 5060
PC_IP = None
EXTENSION = None
CALLER_ID = None
RING_TIMEOUT = 30
ROKU_IP = None
ROKU_PORT = 8060
DTMF_PT = 101

# Phone keys: directional keys are held until the phone sends DTMF end.
# Other mapped keys are one-shot presses.
DIRECTION_KEYS = {"2": "Up", "8": "Down", "4": "Left", "6": "Right"}
PRESS_KEYS = {"5": "Select", "0": "Back", "*": "Home"}

# ---------------- SIP helpers ----------------
def token(n=12):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def parse_sip(packet):
    text = packet.decode("utf-8", errors="replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return (lines[0] if lines else ""), headers, body


def make_sdp(rtp_port):
    return (
        "v=0\r\n"
        f"o=- 1 1 IN IP4 {PC_IP}\r\n"
        "s=Python Roku phone controller\r\n"
        f"c=IN IP4 {PC_IP}\r\n"
        "t=0 0\r\n"
        f"m=audio {rtp_port} RTP/AVP 0 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-15\r\n"
        "a=ptime:20\r\n"
        "a=sendrecv\r\n"
    )


def make_invite(sip_port, rtp_port, call_id, from_tag, branch):
    sdp = make_sdp(rtp_port)
    return (
        f"INVITE sip:{EXTENSION}@{PHONE_IP}:{PHONE_PORT} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {PC_IP}:{sip_port};branch={branch};rport\r\n"
        "Max-Forwards: 70\r\n"
        f"From: <sip:{CALLER_ID}@{PC_IP}>;tag={from_tag}\r\n"
        f"To: <sip:{EXTENSION}@{PHONE_IP}>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 INVITE\r\n"
        f"Contact: <sip:{CALLER_ID}@{PC_IP}:{sip_port}>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp.encode())}\r\n\r\n{sdp}"
    ).encode()


def cancel_call(sip, addr, call_id, from_header, to_header, invite_via):
    cancel = (
        f"CANCEL sip:{EXTENSION}@{PHONE_IP}:{PHONE_PORT} SIP/2.0\r\n"
        f"Via: {invite_via}\r\nMax-Forwards: 70\r\n"
        f"From: {from_header}\r\nTo: {to_header}\r\n"
        f"Call-ID: {call_id}\r\nCSeq: 1 CANCEL\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    sip.sendto(cancel, addr)

# ---------------- DTMF/RTP parsing ----------------
DIGIT_MAP = {i: str(i) for i in range(10)}
DIGIT_MAP.update({10: "*", 11: "#", 12: "A", 13: "B", 14: "C", 15: "D"})


def parse_rtp_dtmf(packet):
    """Return (digit, ended) for RFC 4733 telephone-event packets, else None."""
    if len(packet) < 12 or packet[0] >> 6 != 2:
        return None
    if (packet[1] & 0x7F) != DTMF_PT:
        return None
    offset = 12 + (packet[0] & 0x0F) * 4
    if len(packet) < offset:
        return None
    if packet[0] & 0x10:
        if len(packet) < offset + 4:
            return None
        words = int.from_bytes(packet[offset + 2:offset + 4], "big")
        offset += 4 + words * 4
    if len(packet) < offset + 4:
        return None
    digit = DIGIT_MAP.get(packet[offset])
    if digit is None:
        return None
    return digit, bool(packet[offset + 1] & 0x80)

# ---------------- Roku controller ----------------
class RokuController:
    def __init__(self):
        self.jobs = queue.Queue()
        self.active_dtmf = set()
        self.held = set()
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def _send(self, action, key):
        url = f"http://{ROKU_IP}:{ROKU_PORT}/{action}/{key}"
        request = urllib.request.Request(url, data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status != 200:
                    print(f"Roku returned HTTP {response.status} for {action}/{key}", flush=True)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"Roku command failed ({action}/{key}): {exc}", file=sys.stderr, flush=True)

    def _worker(self):
        while True:
            job = self.jobs.get()
            if job is None:
                self.jobs.task_done()
                return
            action, key = job
            self._send(action, key)
            self.jobs.task_done()

    def _enqueue(self, action, key):
        self.jobs.put((action, key))

    def handle_dtmf(self, digit, ended):
        with self.lock:
            if not ended:
                if digit in self.active_dtmf:
                    return  # RTP repeats each DTMF event; act only on its first packet.
                self.active_dtmf.add(digit)
                if digit in DIRECTION_KEYS:
                    key = DIRECTION_KEYS[digit]
                    self.held.add(key)
                    action = ("keydown", key)
                elif digit in PRESS_KEYS:
                    key = PRESS_KEYS[digit]
                    action = ("keypress", key)
                else:
                    print(f"Unmapped phone key: {digit}", flush=True)
                    return
                print(f"Phone key {digit} → Roku {action[0]}/{key}", flush=True)
                self._enqueue(*action)
            elif digit in self.active_dtmf:
                self.active_dtmf.discard(digit)
                if digit in DIRECTION_KEYS:
                    key = DIRECTION_KEYS[digit]
                    self.held.discard(key)
                    print(f"Phone key {digit} released → Roku keyup/{key}", flush=True)
                    self._enqueue("keyup", key)

    def release_all(self):
        with self.lock:
            for key in list(self.held):
                self._enqueue("keyup", key)
            self.held.clear()
            self.active_dtmf.clear()

    def close(self):
        self.release_all()
        self.jobs.join()
        self.jobs.put(None)
        self.worker.join(timeout=2)

# ---------------- SIP call and RTP receive ----------------
def run_call(controller):
    sip_port = random.randint(40000, 45000)
    rtp_port = random.randrange(46000, 50000, 2)
    call_id = token(20) + "@" + PC_IP
    from_tag = token()
    branch = "z9hG4bK" + token()
    invite_via = f"SIP/2.0/UDP {PC_IP}:{sip_port};branch={branch};rport"
    from_header = f"<sip:{CALLER_ID}@{PC_IP}>;tag={from_tag}"
    original_to = f"<sip:{EXTENSION}@{PHONE_IP}>"

    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip.bind((PC_IP, sip_port))
    rtp.bind((PC_IP, rtp_port))
    sip.settimeout(0.4)
    response_addr = (PHONE_IP, PHONE_PORT)
    remote_uri = f"sip:{EXTENSION}@{PHONE_IP}:{PHONE_PORT}"
    remote_to = None
    call_established = False

    try:
        print(f"Calling extension {EXTENSION} on SPA2102 {PHONE_IP}…", flush=True)
        sip.sendto(make_invite(sip_port, rtp_port, call_id, from_tag, branch), response_addr)
        deadline = time.monotonic() + RING_TIMEOUT
        answer = None
        while time.monotonic() < deadline:
            try:
                packet, addr = sip.recvfrom(65535)
            except socket.timeout:
                continue
            status, headers, body = parse_sip(packet)
            if not status.startswith("SIP/2.0 "):
                continue
            print(status, flush=True)
            if status.startswith("SIP/2.0 180"):
                print("Phone is ringing; answer it.", flush=True)
            elif status.startswith("SIP/2.0 200") and "invite" not in headers.get("cseq", "").lower():
                continue
            elif status.startswith("SIP/2.0 200"):
                answer = (headers, body, addr)
                break
            elif status.startswith("SIP/2.0 100") or status.startswith("SIP/2.0 183"):
                continue
            elif status.startswith("SIP/2.0 ") and not status.startswith("SIP/2.0 1"):
                print("Call rejected or failed.", flush=True)
                return

        if answer is None:
            print("No answer; cancelling call.", flush=True)
            cancel_call(sip, response_addr, call_id, from_header, original_to, invite_via)
            return

        headers, body, response_addr = answer
        remote_to = headers.get("to")
        contact = headers.get("contact", "")
        if not remote_to:
            raise RuntimeError("200 OK did not include a To header.")
        match = re.search(r"<(sip:[^>]+)>", contact)
        if match:
            remote_uri = match.group(1)

        ack = (
            f"ACK {remote_uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {PC_IP}:{sip_port};branch=z9hG4bK{token()};rport\r\n"
            "Max-Forwards: 70\r\n"
            f"From: {from_header}\r\nTo: {remote_to}\r\n"
            f"Call-ID: {call_id}\r\nCSeq: 1 ACK\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        sip.sendto(ack.encode(), response_addr)
        call_established = True

        remote_ip = PHONE_IP
        remote_rtp_port = None
        for line in body.splitlines():
            if line.startswith("c=IN IP4 "):
                remote_ip = line.split()[-1]
            elif line.startswith("m=audio "):
                fields = line.split()
                if len(fields) >= 4 and fields[2] == "RTP/AVP" and "101" in fields[3:]:
                    remote_rtp_port = int(fields[1])
        if not remote_rtp_port:
            raise RuntimeError("Phone SDP did not offer telephone-event payload 101.")

        print(f"RTP listener: {PC_IP}:{rtp_port}; phone RTP: {remote_ip}:{remote_rtp_port}", flush=True)
        print(f"Roku target: http://{ROKU_IP}:{ROKU_PORT}", flush=True)
        print("Keys: 2 Up, 8 Down, 4 Left, 6 Right, 5 Select, 0 Back, * Home.", flush=True)
        print("Hang up when finished, then press Ctrl+C here to stop and release keys.", flush=True)
        rtp.settimeout(0.5)
        while True:
            try:
                packet, source = rtp.recvfrom(4096)
            except socket.timeout:
                continue
            if source[0] != remote_ip:
                continue
            parsed = parse_rtp_dtmf(packet)
            if parsed:
                digit, ended = parsed
                if digit in DIRECTION_KEYS or digit in PRESS_KEYS:
                    controller.handle_dtmf(digit, ended)

    except KeyboardInterrupt:
        print("\nStopping controller…", flush=True)
    finally:
        controller.release_all()
        if call_established and remote_to:
            try:
                bye = (
                    f"BYE {remote_uri} SIP/2.0\r\n"
                    f"Via: SIP/2.0/UDP {PC_IP}:{sip_port};branch=z9hG4bK{token()};rport\r\n"
                    "Max-Forwards: 70\r\n"
                    f"From: {from_header}\r\nTo: {remote_to}\r\n"
                    f"Call-ID: {call_id}\r\nCSeq: 2 BYE\r\n"
                    "Content-Length: 0\r\n\r\n"
                )
                sip.sendto(bye.encode(), response_addr)
                print("Sent BYE to end the call.", flush=True)
            except Exception as exc:
                print(f"Could not send BYE: {exc}", file=sys.stderr)
        sip.close()
        rtp.close()


def main():
    global PHONE_IP, PHONE_PORT, PC_IP, EXTENSION, CALLER_ID
    global RING_TIMEOUT, ROKU_IP, ROKU_PORT, DTMF_PT

    parser = argparse.ArgumentParser(
        description="Use a compatible SIP analog telephone adapter and phone keypad to control a Roku on your local network."
    )
    parser.add_argument("--ata-ip", required=True, help="LAN IPv4 address of your SIP analog telephone adapter")
    parser.add_argument("--pc-ip", required=True, help="LAN IPv4 address of this computer, reachable by the adapter")
    parser.add_argument("--extension", required=True, help="Phone/line User ID configured on the adapter")
    parser.add_argument("--caller-id", required=True, help="Numeric caller ID used for the SIP call")
    parser.add_argument("--roku-ip", required=True, help="LAN IPv4 address of your Roku")
    parser.add_argument("--sip-port", type=int, default=5060, help="Adapter SIP port (default: 5060)")
    parser.add_argument("--roku-port", type=int, default=8060, help="Roku ECP port (default: 8060)")
    parser.add_argument("--ring-timeout", type=int, default=30, help="Seconds to wait for the phone to be answered (default: 30)")
    parser.add_argument("--dtmf-payload-type", type=int, default=101, help="RTP telephone-event payload type (default: 101)")
    args = parser.parse_args()

    PHONE_IP = args.ata_ip
    PHONE_PORT = args.sip_port
    PC_IP = args.pc_ip
    EXTENSION = args.extension
    CALLER_ID = args.caller_id
    RING_TIMEOUT = args.ring_timeout
    ROKU_IP = args.roku_ip
    ROKU_PORT = args.roku_port
    DTMF_PT = args.dtmf_payload_type

    if not (1 <= PHONE_PORT <= 65535 and 1 <= ROKU_PORT <= 65535):
        parser.error("ports must be between 1 and 65535")
    if not (1 <= DTMF_PT <= 127):
        parser.error("DTMF payload type must be between 1 and 127")
    if RING_TIMEOUT < 1:
        parser.error("ring timeout must be at least 1 second")

    controller = RokuController()
    try:
        run_call(controller)
    except OSError as exc:
        sys.exit(f"Network setup failed: {exc}")
    finally:
        controller.close()


if __name__ == "__main__":
    main()