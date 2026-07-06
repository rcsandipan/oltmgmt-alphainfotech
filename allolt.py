import firebase_admin
from firebase_admin import credentials, db
from pysnmp.hlapi import *
import re
import time
from datetime import datetime

# ======================================================
# 🔥 FIREBASE INITIALIZATION (ONLY ONCE)
# ======================================================
cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://oltmgmt-default-rtdb.asia-southeast1.firebasedatabase.app/'
})

# ======================================================
# 🧠 OLT CONFIGURATION (ADD AS MANY AS YOU WANT)
# ======================================================
OLT_LIST = [
   {
        "name": "KUNJABAN-OLT",
        "ip": "10.210.27.15",
        "community": "public",
        "firebase_dataset": "kjnoltdata",
        "key_prefix": "kjn"
    },
    {
        "name": "BANERJEEPARA-OLT",
        "ip": "10.210.27.17",
        "community": "public",
        "firebase_dataset": "bproltdata",
        "key_prefix": "bpr"
    },
     {
        "name": "GB-OLT",
        "ip": "10.215.158.65",
        "community": "public",
        "firebase_dataset": "gboltdata",
        "key_prefix": "gb"
    },
     {
        "name": "DHALESWAR-OLT",
        "ip": "10.215.158.64",
        "community": "public",
        "firebase_dataset": "dlsoltdata",
        "key_prefix": "dls"
    },
     
     {
        "name": "KAMAN CHOWMUHANI-OLT",
        "ip": "10.215.158.55",
        "community": "public",
        "firebase_dataset": "kcwoltdata",
        "key_prefix": "kcw"
    },
     {
        "name": "USHABAZAR-OLT",
        "ip": "10.215.158.49",
        "community": "public",
        "firebase_dataset": "aptoltdata",
        "key_prefix": "apt"
    }
   
]

# ======================================================
# 🔁 SNMP FUNCTIONS
# ======================================================
def walk_table(ip, comm, base_oid):
    results = []
    try:
        for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
            SnmpEngine(),
            CommunityData(comm),
            UdpTransportTarget((ip, 161), timeout=2, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False
        ):
            if errorIndication or errorStatus:
                break
            results.append((str(varBinds[0][0]), str(varBinds[0][1])))
    except Exception as e:
        print(f"❌ SNMP WALK ERROR on {ip}: {e}")
    return results


def get_single(ip, comm, oid):
    try:
        for (errorIndication, errorStatus, errorIndex, varBinds) in getCmd(
            SnmpEngine(),
            CommunityData(comm),
            UdpTransportTarget((ip, 161), timeout=2, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        ):
            if errorIndication or errorStatus:
                return 'N/A'
            return str(varBinds[0][1])
    except:
        return 'N/A'

# ======================================================
# 🚀 ONU STATUS COLLECTOR (PER OLT)
# ======================================================
def get_all_onu_status(olt):
    olt_ip = olt["ip"]
    community = olt["community"]
    dataset = olt["firebase_dataset"]
    prefix = olt["key_prefix"]

    fb_ref = db.reference(dataset)

    print(f"\n🟢 {olt['name']} ({olt_ip}) → Firebase [{dataset}]")
    print("=" * 80)

    interfaces = walk_table(olt_ip, community, '1.3.6.1.2.1.2.2.1.2')
    mac_table = walk_table(olt_ip, community, '1.3.6.1.4.1.37950.1.1.5.12.2.1.2.1.5')
    mac_map = {}

    for oid, mac in mac_table:
        parts = oid.split('.')
        # pon_no = "0/"+parts[-2]
        pon_no = parts[-2]
        onu_id = parts[-1]

        mac_clean = mac.replace(' ', ':').upper()
        mac_map[(int(pon_no), int(onu_id))] = mac_clean


    for oid, desc in interfaces:
        ifIndex = oid.split('.')[-1]
        ifOperStatus = get_single(
            olt_ip,
            community,
            f'1.3.6.1.2.1.2.2.1.8.{ifIndex}'
        )

        pon_match = re.search(r'epon(\d+)', desc.lower())
        onu_match = re.search(r'onu(\d+)', desc.lower())

        if not (pon_match and onu_match):
            continue

        pon_no = int(pon_match.group(1))
        onu_id = int(onu_match.group(1))

        status_str = 'UP' if ifOperStatus == '1' else 'DOWN'
        macaddress = mac_map.get((pon_no, onu_id), "")
        
        
        def format_mac(hexmac):
            if not hexmac:
                return ""
            hexmac = hexmac.replace("0X", "").replace("0x", "").upper()
            return ":".join(hexmac[i:i+2] for i in range(0, 12, 2))
        

        mac = format_mac(macaddress)
        # Firebase Key Format → kjnpon1onu3
        fb_key = f"{prefix}pon{pon_no}onu{onu_id}"
        lastevent = fb_ref.child(fb_key).get()

        # Safe defaults
        onustatus = lastevent.get('onustatus') if lastevent else None
        eventtime = lastevent.get('eventtime') if lastevent else None
        synctime = lastevent.get('synctime') if lastevent else None

        # Fix: Always assign these variables

        
        if status_str == "UP" and onustatus in ["LOS/Fibre break", "Powered-Off"]:
            updatedonustatus = "ONLINE"
            updatedeventtime = ""
        else:
            updatedonustatus = onustatus or "UNKNOWN"
            updatedeventtime = eventtime or ""

        

        fb_ref.child(fb_key).update({
            "database": dataset,
            "key":fb_key,
            "pon_no":"0/"+str(pon_no),
            "onu_id":str(onu_id),
            "macaddress": mac,
            "currentstatus": status_str,
            "synctime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "onustatus":updatedonustatus,
            "eventtime":updatedeventtime

        })

        print(f"✅ {fb_key:<18} | {status_str} | {mac}")

# ======================================================
# ⏰ MAIN LOOP
# ======================================================
if __name__ == "__main__":
    while True:
        print(f"\n⏰ Scan started at {datetime.now().strftime('%H:%M:%S')}")
        
        for olt in OLT_LIST:
            get_all_onu_status(olt)

        print("\n💤 Waiting 600 seconds...\n")
        time.sleep(600)
