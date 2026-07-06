
# OPTRONIX OLT 

import firebase_admin
from firebase_admin import credentials, db
from pysnmp.hlapi import *
import re
import os
from datetime import datetime
import time

# 🔥 FIREBASE SETUP - UPDATE YOUR CREDENTIALS
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://oltmgmt-default-rtdb.asia-southeast1.firebasedatabase.app/'
})

fb_ref = db.reference('ongcoltdata')

def parse_onu_from_descr(desc):
    """Optronix FIXED: EPON0/1:1 → pon1onu1"""
    desc_upper = desc.upper()
    
    # Optronix: EPON0/1:1, EPON0/2:54
    epon_match = re.search(r'EPON[0-9/]+:(\d+)', desc_upper)
    
    if epon_match:
        # Extract PON from "EPON0/1:" → use last number before colon
        pon_part = re.search(r'EPON([0-9/]+):', desc_upper)
        if pon_part:
            pon_str = pon_part.group(1).split('/')[-1]  # "0/1" → "1"
            pon_no = pon_str if pon_str.isdigit() else '1'
            onu_id = epon_match.group(1)
            return pon_no, onu_id, True
    
    # Keep Syrotech fallback
    desc_lower = desc.lower()
    epon_pon = re.search(r'epon([0-9/]+)', desc_lower)
    onu_match = re.search(r'onu(\d+)', desc_lower)
    if epon_pon and onu_match:
        pon_str = epon_pon.group(1).replace('/', '')
        pon_no = str(int(pon_str) if pon_str.isdigit() else 1)
        onu_id = onu_match.group(1)
        return pon_no, onu_id, True
    
    return None, None, False


def get_all_onu_status(olt_ip, community='public'):
    """FULL ONU MONITOR - Multi-Vendor (Syrotech + Optronix + BDCOM)"""
    print(f"🟢 OLT {olt_ip} → FIREBASE LIVE SYNC")
    print("=" * 80)
    
    interfaces = walk_table(olt_ip, community, '1.3.6.1.2.1.2.2.1.2')
    mac_table = walk_table(olt_ip, community, '1.3.6.1.4.1.37950.1.1.5.12.1.25.1.5')
    mac_map = {}
    for oid, mac in mac_table:
        parts = oid.split('.')
        pon_no = parts[-2]
        onu_id = parts[-1]

        mac_clean = mac.upper()
        mac_map[(int(pon_no), int(onu_id))] = mac_clean

    active_onus = 0
    pon_stats = {}
    
    for oid, desc in interfaces:
        ifIndex = oid.split('.')[-1]
        ifOperStatus = get_single(olt_ip, community, f'1.3.6.1.2.1.2.2.1.8.{ifIndex}')
        
        pon_no, onu_id, is_valid = parse_onu_from_descr(desc)
        
        # macaddress = mac_map.get((pon_no, onu_id), "")
        
        
        def format_mac(hexmac):
            if not hexmac:
                return ""
            hexmac = hexmac.upper()
            return hexmac
        

        
        fb_key = f"ongcpon{pon_no}onu{onu_id}"
        lastevent = fb_ref.child(fb_key).get()

        # Safe defaults
        onustatus = lastevent.get('onustatus') if lastevent else None
        eventtime = lastevent.get('eventtime') if lastevent else None


        if is_valid:
            status = 1 if ifOperStatus == '1' else 2
            status_str = 'UP' if status == 1 else 'DOWN'
            macaddress = mac_map.get((int(pon_no), int(onu_id)), "")
            mac = format_mac(macaddress)
            
            if status == 1: 
                active_onus += 1

            if status_str == "UP" and onustatus in ["LOS/Fibre break", "Powered-Off"]:
                updatedonustatus = "ONLINE"
                updatedeventtime = ""
            else:
                updatedonustatus = onustatus or "UNKNOWN"
                updatedeventtime = eventtime or ""
            

            
            fb_key = f"ongcpon{pon_no}onu{onu_id}"
            onu_data = {  "pon_no":"0/"+str(pon_no),
                          "onu_id":str(onu_id),
                          "key":fb_key,
                          "database":"ongcoltdata",
                         "macaddress": mac,
                        'currentstatus': status_str,
                        'synctime':datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "onustatus":updatedonustatus,
                        "eventtime":updatedeventtime
                        }
            
            fb_ref.child(fb_key).update(onu_data)
            print(f"✅ {fb_key:<15} | {desc[:30]:<30} | {mac}|{status_str:<3} → Firebase")
            
            # PON stats
            if pon_no not in pon_stats:
                pon_stats[pon_no] = {'total': 0, 'active': 0}
            pon_stats[pon_no]['total'] += 1
            if status == 1: 
                pon_stats[pon_no]['active'] += 1
            else:
                print(f"⚠️  SKIP: {desc[:40]} (not EPON/ONU)")
    
    print(f"\n📊 SUMMARY: {active_onus} active ONUs")
    for pon, stats in pon_stats.items():
        print(f"   PON{pon}: {stats['active']}/{stats['total']} UP")

def test_olt_format(olt_ip, community='public'):
    """DEBUG: Show exact ifDescr format from OLT"""
    print(f"\n🔍 TESTING OLT FORMAT: {olt_ip}")
    interfaces = walk_table(olt_ip, community, '1.3.6.1.2.1.2.2.1.2')
    
    print("ifDescr samples containing 'epon' or 'onu':")
    epon_count = 0
    for oid, desc in interfaces:
        if any(x in desc.lower() for x in ['epon', 'onu']):
            pon_no, onu_id, matched = parse_onu_from_descr(desc)
            status = "✅" if matched else "❌"
            print(f"  {status} {desc} → pon{pon_no}onu{onu_id}")
            epon_count += 1
    
    print(f"Total EPON/ONU interfaces: {epon_count}")
    return epon_count > 0

def walk_table(ip, comm, base_oid):
    results = []
    try:
        for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
            SnmpEngine(),
            CommunityData(comm),
            UdpTransportTarget((ip, 161)),
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False
        ):
            if errorIndication or errorStatus: 
                print(f"SNMP Error: {errorIndication or errorStatus}")
                break
            results.append((str(varBinds[0][0]), str(varBinds[0][1])))
    except Exception as e:
        print(f"SNMP Walk failed: {e}")
    return results

def get_single(ip, comm, oid):
    try:
        for (errorIndication, errorStatus, errorIndex, varBinds) in getCmd(
            SnmpEngine(),
            CommunityData(comm),
            UdpTransportTarget((ip, 161)),
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        ):
            if errorIndication or errorStatus: 
                return 'N/A'
            return str(varBinds[0][1])
    except:
        return 'N/A'

# 🚀 MAIN EXECUTION
if __name__ == "__main__":
    # CONFIG: Add your OLT IPs here
    olt_configs = [
        
        {'ip': '10.215.158.69', 'community': 'public'},  # Optronix
    ]
    
    # Step 1: TEST format first
    print("🧪 STEP 1: Testing OLT formats...")
    working_olts = []
    for config in olt_configs:
        if test_olt_format(config['ip'], config['community']):
            working_olts.append(config)
    
    if not working_olts:
        print("❌ No working OLTs found. Check IP/community/SNMP access.")
        exit()
    
    # Step 2: Continuous monitoring
    print("\n🚀 STEP 2: Starting continuous monitoring...")
    while True:
        print(f"\n⏰ Scan at {datetime.now().strftime('%H:%M:%S')}")
        for config in working_olts:
            get_all_onu_status(config['ip'], config['community'])
        print("💤 Waiting 600 seconds...\n")
        time.sleep(600)
