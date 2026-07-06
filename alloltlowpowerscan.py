import firebase_admin
from firebase_admin import credentials, db
from pysnmp.hlapi import *
import re
import time
from datetime import datetime
import socket

# ======================================================
# 🔥 FIREBASE INITIALIZATION (ONLY ONCE)
# ======================================================
cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://oltmgmt-default-rtdb.asia-southeast1.firebasedatabase.app/'
})

# ======================================================
# 🧠 OLT CONFIGURATION
# ======================================================
OLT_LIST = [
    {
        "name": "KUNJABAN-OLT",
        "ip": "10.210.27.15",
        "community": "public",
        "firebase_dataset": "kjnoltdata",
        "key_prefix": "kjn",
        "username": "admin",
        "password": "Alpha@2810",
        "enablepassword": "Alpha@2810"
    },
    {
        "name": "DHALESWAR-OLT",
        "ip": "10.215.158.64",
        "community": "public",
        "firebase_dataset": "dlsoltdata",
        "key_prefix": "dls",
        "username": "admin",
        "password": "Alpha@2810",
        "enablepassword": "Alpha@2810"
    },
    {
        "name": "BANERJEEPARA-OLT",
        "ip": "10.210.27.17",
        "community": "public",
        "firebase_dataset": "bproltdata",
        "key_prefix": "bpr",
	    "username": "admin",
        "password": "Alpha@2810",
        "enablepassword": "Alpha@2810"


    },
     {
        "name": "GB-OLT",
        "ip": "10.215.158.65",
        "community": "public",
        "firebase_dataset": "gboltdata",
        "key_prefix": "gb",
	    "username": "admin",
        "password": "Alpha@2810",
        "enablepassword": "Alpha@2810"
    },
          
     {
        "name": "KAMAN CHOWMUHANI-OLT",
        "ip": "10.215.158.55",
        "community": "public",
        "firebase_dataset": "kcwoltdata",
        "key_prefix": "kcw",
	    "username": "admin",
        "password": "Alpha@2810",
        "enablepassword": "Alpha@2810"
    },
     {
        "name": "USHABAZAR-OLT",
        "ip": "10.215.158.49",
        "community": "public",
        "firebase_dataset": "aptoltdata",
        "key_prefix": "apt",
	    "username": "admin",
        "password": "Alpha@2810",
        "enablepassword": "Alpha@2810"},
 {
        "name": "ONGC-OLT",
        "ip": "10.215.158.69",
        "community": "public",
        "firebase_dataset": "ongcoltdata",
        "key_prefix": "ongc",
	    "username": "admin",
        "password": "Alpha@2810",
        "enablepassword": "Alpha@2810"}
]


def poll_onu_rx_power(olt):
    olt_ip = olt["ip"]
    username = olt["username"]
    password = olt["password"]
    enablepassword = olt["enablepassword"]
    dataset = olt["firebase_dataset"]
    prefix = olt["key_prefix"]

    print(f"\n📡 RX Polling → {olt['name']} ({olt_ip})")
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(20)
        sock.connect((olt_ip, 23))
        
        # Read until prompts and send credentials
        def read_until(ends_with, timeout=10):
            data = b""
            start = time.time()
            while time.time() - start < timeout:
                try:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                    if ends_with in data:
                        return data
                except:
                    pass
            return data
        
        # Login
        read_until(b"Username:", 5)
        sock.send(f"{username}\r\n".encode())
        time.sleep(1)
        
        read_until(b"Password:", 5)
        sock.send(f"{password}\r\n".encode())
        time.sleep(1)
        
        # Enable
        sock.send(b"enable\r\n")
        time.sleep(1)
        read_until(b"Password:", 5)
        sock.send(f"{enablepassword}\r\n".encode())
        time.sleep(1)
        
        # Commands
        cmds = [
            b"config terminal\r\n",
            b"terminal length 0\r\n", 
            b"show onu opm-diag all\r\n",
            b"exit\r\n",
            b"exit\r\n"
        ]
        
        for cmd in cmds:
            sock.send(cmd)
            time.sleep(1)
        
        time.sleep(4)
        
        # Read output
        output = ""
        start = time.time()
        while time.time() - start < 10:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                output += data.decode(errors="ignore")
            except:
                break
        
        sock.close()
        
        parse_and_update_rx_power(output, dataset, prefix)
        print("✅ RX Polling Completed")
        
    except Exception as e:
        print(f"❌ Telnet error on {olt_ip}: {e}")

# ======================================================
# 🔍 RX POWER PARSER
# ======================================================

def parse_and_update_rx_power(output, dataset, prefix):

    fb_ref = db.reference(dataset)

    all_nodes = fb_ref.get()

    if all_nodes:
        for node_name in all_nodes:
            print(f"Clearing value in {node_name}")

            fb_ref.child(node_name).child("rxpower").set("")




    pattern = re.compile(
        r"EPON0/(\d+):(\d+)\s+[\d\.]+\s+[\d\.]+\s+[\d\.]+\s+[\d\.]+\s+(-?[\d\.]+)"
    )

    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            pon_no = match.group(1)
            onu_id = match.group(2)
            rx_power = float(match.group(3))
            rx_power_str = str(rx_power)

            fb_key = f"{prefix}pon{pon_no}onu{onu_id}"

            fb_ref.child(fb_key).update({
                "rxpower": rx_power_str,
                
            })

            print(f"📊 {fb_key} → {rx_power} dBm")


# Keep your main loop SIMPLE - NO CHANGES NEEDED:
if __name__ == "__main__":
    while True:
        print(f"\n⏰ Scan started at {datetime.now().strftime('%H:%M:%S')}")
        for olt in OLT_LIST:
     
            poll_onu_rx_power(olt)  
        print("\n💤 Waiting 600 seconds...\n")  # Fixed 30s
        time.sleep(600)




