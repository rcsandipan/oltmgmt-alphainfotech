import logging
import re
from datetime import datetime, timedelta
import sys
import socket
from collections import defaultdict
import threading

from pysnmp.entity import engine, config
from pysnmp.carrier.asyncore.dgram import udp
from pysnmp.entity.rfc3413 import ntfrcv

import firebase_admin
from firebase_admin import credentials, db

# =========================
# GLOBAL VARIABLES FOR STATE TRACKING
# =========================
# Store last event for each ONU (pon_no + onu_id)
# key: (pon_no, onu_id), value: list of (timestamp, event_oid, event_datetime)
onu_history = defaultdict(list)
history_lock = threading.Lock()
CLEANUP_INTERVAL = 300  # Clean old entries every 5 minutes

# Event OID to status mapping
EVENT_STATUS_MAP = {
    "1.3.6.1.4.1.37950.1.1.5.10.13.5.51": "ONLINE",
    "1.3.6.1.4.1.37950.1.1.5.10.13.5.23": "LOS/Fibre break",
    "1.3.6.1.4.1.37950.1.1.5.10.13.5.30": "Powered-Off",
    # Add more mappings as needed
}

# =========================
# HELPER FUNCTIONS FOR STATE MANAGEMENT
# =========================
def parse_olt_event_time(ts_string):
    """
    Parse OLT event time string (YYYYMMDDHHMMSS) to datetime object
    Returns: datetime object or None if parsing fails
    """
    try:
        return datetime.strptime(ts_string, "%Y%m%d%H%M%S")
    except Exception as e:
        logging.error(f"Failed to parse event time '{ts_string}': {e}")
        return None

def cleanup_old_entries():
    """Remove entries older than 2 minutes from history"""
    now = datetime.now()
    cutoff = now - timedelta(minutes=2)  # Keep slightly more than needed
    
    with history_lock:
        for key in list(onu_history.keys()):
            # Filter out old entries - CORRECTED SYNTAX
            onu_history[key] = [
                (ts, event_oid, event_dt) for ts, event_oid, event_dt in onu_history[key] 
                if (event_dt and event_dt > cutoff)
            ]
            # Remove key if no entries left
            if not onu_history[key]:
                del onu_history[key]

def determine_final_status(current_event_oid, pon_no, onu_id, event_datetime):
    """
    Determine final status considering the 30-second rule
    MODIFIED LOGIC: If LOS (5.23) occurs within 30 seconds after Powered-Off (5.30), treat as Powered-Off
    
    Args:
        current_event_oid: The current event OID
        pon_no: PON number (e.g., "0/3")
        onu_id: ONU ID (e.g., "15")
        event_datetime: datetime object from OLT event time
    
    Returns: (status, is_special_transition, transition_type)
    """
    if event_datetime is None:
        # Fallback to current time if OLT time not available
        event_datetime = datetime.now()
        print(f"⚠️ Using local time for {pon_no}/{onu_id} as OLT time not available")
    
    key = (pon_no, onu_id)
    
    with history_lock:
        # Get recent events for this ONU (within 40 seconds for safety margin)
        recent_events = []
        for ts, event, evt_dt in onu_history.get(key, []):
            if evt_dt:  # Only consider events with valid datetime
                time_diff = (event_datetime - evt_dt).total_seconds()
                if 0 <= time_diff <= 40:  # Check within 40 seconds window
                    recent_events.append((evt_dt, event))
        
        # Add current event to history
        onu_history[key].append((
            datetime.now().timestamp(),  # Local timestamp for cleanup
            current_event_oid,
            event_datetime
        ))
        
        # Keep only last 10 events per ONU
        if len(onu_history[key]) > 10:
            onu_history[key] = onu_history[key][-10:]
    
    # If no recent events, use current event mapping
    if not recent_events:
        status = EVENT_STATUS_MAP.get(current_event_oid, "UNKNOWN")
        return status, False, None
    
    # Check if current event is LOS (5.23) and there was a Powered-Off (5.30) within 30 seconds BEFORE it
    if current_event_oid == "1.3.6.1.4.1.37950.1.1.5.10.13.5.23":
        # Look for recent Powered-Off events
        powered_off_events = []
        for evt_dt, event in recent_events:
            if event == "1.3.6.1.4.1.37950.1.1.5.10.13.5.30":
                time_diff = (event_datetime - evt_dt).total_seconds()
                if 0 <= time_diff <= 30:  # Powered-Off within 30 seconds before current LOS
                    powered_off_events.append((evt_dt, time_diff))
        
        if powered_off_events:
            # Sort by most recent Powered-Off
            powered_off_events.sort(reverse=True)
            latest_powered_off_time, time_diff = powered_off_events[0]
            
            print(f"🔀 Powered-Off → LOS transition detected:")
            print(f"   Powered-Off at: {latest_powered_off_time.strftime('%H:%M:%S')}")
            print(f"   LOS at: {event_datetime.strftime('%H:%M:%S')}")
            print(f"   Time difference: {time_diff:.1f} seconds")
            print(f"   Decision: Treating as Powered-Off (LOS occurred after device was powered off)")
            
            # Treat as Powered-Off (LOS occurred after device was already off)
            return "Powered-Off", True, "PWR_OFF_THEN_LOS"
    
    # Also check the reverse: if current event is Powered-Off (5.30) and there was LOS (5.23) within 30 seconds
    # (This maintains your original LOS → Powered-Off logic if needed)
    elif current_event_oid == "1.3.6.1.4.1.37950.1.1.5.10.13.5.30":
        los_events = []
        for evt_dt, event in recent_events:
            if event == "1.3.6.1.4.1.37950.1.1.5.10.13.5.23":
                time_diff = (event_datetime - evt_dt).total_seconds()
                if 0 <= time_diff <= 30:  # LOS within 30 seconds before current Powered-Off
                    los_events.append((evt_dt, time_diff))
        
        if los_events:
            # Sort by most recent LOS
            los_events.sort(reverse=True)
            latest_los_time, time_diff = los_events[0]
            
            print(f"🔀 LOS → Powered-Off transition detected:")
            print(f"   LOS at: {latest_los_time.strftime('%H:%M:%S')}")
            print(f"   Powered-Off at: {event_datetime.strftime('%H:%M:%S')}")
            print(f"   Time difference: {time_diff:.1f} seconds")
            print(f"   Decision: Treating as Powered-Off (device turned off after LOS)")
            
            # Treat as Powered-Off
            return "Powered-Off", True, "LOS_THEN_PWR_OFF"
    
    # Normal mapping (no special transitions)
    status = EVENT_STATUS_MAP.get(current_event_oid, "UNKNOWN")
    return status, False, None

# =========================
# NETWORK DIAGNOSTIC
# =========================
def network_diagnostic():
    print("🔍 Network Diagnostic:")
    print(f"Python: {sys.version}")
    
    hostname = socket.gethostname()
    print(f"Hostname: {hostname}")
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            s.bind(('0.0.0.0', 166))
            print("✅ Can bind to 0.0.0.0:166")
            local_ip = '0.0.0.0'
        except:
            print("⚠️ Cannot bind to 0.0.0.0:166, trying all interfaces...")
            s.bind(('0.0.0.0', 166))
            local_ip = '0.0.0.0'
            print(f"✅ Bound to all interfaces on port 166")
        
        s.close()
        return local_ip
    except Exception as e:
        print(f"❌ Binding failed: {e}")
        return None

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename='received_traps.log',
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# =========================
# FIREBASE INIT
# =========================
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    "databaseURL": "https://oltmgmt-default-rtdb.asia-southeast1.firebasedatabase.app"
})

# =========================
# EXISTING HELPER FUNCTIONS
# =========================
def hex_to_mac(hex_str):
    if not hex_str:
        return None
    hex_str = hex_str.replace("0x", "")
    return ":".join(hex_str[i:i+2] for i in range(0, len(hex_str), 2)).upper()

def parse_event_time(ts):
    """
    Parse event time for display (string format)
    Returns: Formatted string or None
    """
    try:
        dt = datetime.strptime(ts, "%Y%m%d%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def write_raw_trap_to_file(varBinds):
    with open("received_traps.txt", "a", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write(f"Received At : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for name, val in varBinds:
            f.write(f"{name.prettyPrint()} = {val.prettyPrint()}\n")
        f.write("\n" + "=" * 60 + "\n\n")

def parse_pon_message(message):
    if not message:
        return None
    message = message.strip()
    
    pattern = re.compile(
        r"PON\s+(\d+/\d+)\s+"
        r"ONU\s+(\d+)\s+"
        r"(?:llid\s+\d+\s+)?"
        r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})"
        r"(?:\s*(.*))?$",
        re.IGNORECASE
    )
    
    match = pattern.search(message)
    if not match:
        return None
    
    pon_no = match.group(1)
    onu_id = match.group(2)
    mac = match.group(3).upper()
    tail_text = match.group(4)
    
    if tail_text:
        tail_text = tail_text.strip().rstrip(".").upper()
    
    if not tail_text:
        is_los = False
    elif tail_text == "MPCP TIMEOUT":
        is_los = True
    else:
        return None
    
    return {
        "pon_no": pon_no,
        "onu_id": onu_id,
        "macaddress": mac,
        "isLOS": is_los
    }

# =========================
# ENHANCED SNMP TRAP CALLBACK
# =========================
def cbFun(snmpEngine, stateRef, contextEngineId, contextName, varBinds, cbCtx=None):
    print("\n" + "="*50)
    current_local_time = datetime.now()
    print(f"📥 TRAP RECEIVED at {current_local_time.strftime('%H:%M:%S')}")
    print(f"Community: {contextName}")
    print(f"Number of OIDs: {len(varBinds)}")
    
    # Write raw trap first
    write_raw_trap_to_file(varBinds)
    
    trap_data = {}
    for name, val in varBinds:
        oid = name.prettyPrint()
        value = val.prettyPrint()
        trap_data[oid] = value
        
        # Print first 5 OIDs for debugging
        if len(trap_data) <= 5:
            print(f"  {oid} = {value}")
    
    logging.info(f"Trap received with community: {contextName}")
    logging.info(f"Total OIDs: {len(varBinds)}")
    
    # Get event data
    event_time_raw = trap_data.get("1.3.6.1.4.1.37950.1.1.5.10.13.2.8.0")
    message = trap_data.get("1.3.6.1.4.1.37950.1.1.5.10.13.2.10.0", "")
    event_oid = trap_data.get("1.3.6.1.4.1.37950.1.1.5.10.13.2.5.0", "")
    
    print(f"Event OID: {event_oid}")
    print(f"Event time (raw): {event_time_raw}")
    print(f"Raw message: {repr(message)}")
    
    # Parse OLT event time to datetime object
    event_datetime = None
    if event_time_raw:
        event_datetime = parse_olt_event_time(event_time_raw)
        if event_datetime:
            print(f"Event time (parsed): {event_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print(f"⚠️ Could not parse event time, using local time")
            event_datetime = current_local_time
    else:
        print(f"⚠️ No event time in trap, using local time")
        event_datetime = current_local_time
    
    # Parse PON message
    parsed_msg = parse_pon_message(message)
    if not parsed_msg:
        print("⚠️ Trap ignored (not LOS / format mismatch)")
        return
    
    # Determine final status with modified 30-second rule (using OLT event time)
    final_status, is_special_transition, transition_type = determine_final_status(
        event_oid, 
        parsed_msg["pon_no"], 
        parsed_msg["onu_id"], 
        event_datetime  # Using OLT time, not local time
    )
    
    # Create event object
    event_object = {
        "eventtime": event_datetime.strftime("%Y-%m-%d %H:%M:%S"),  # OLT time
        "pon_no": parsed_msg["pon_no"],
        "onu_id": parsed_msg["onu_id"],
        "macaddress": parsed_msg["macaddress"],
        "event_oid": event_oid,
        "onu_status": final_status,
        "isLOS": parsed_msg["isLOS"],
        "is_special_transition": is_special_transition,
        "transition_type": transition_type,
        "received_time": current_local_time.strftime("%Y-%m-%d %H:%M:%S"),  # Local time
        "raw_message": message,
        "event_time_raw": event_time_raw  # Keep raw value for debugging
    }
    
    # Log status determination
    if is_special_transition:
        print(f"🔀 Status transition detected: {transition_type}")
    print(f"📊 Final status: {final_status}")

    
    try:
        # Push to Firebase - Main events log
        # db.reference("snmp_events").push(event_object)
        print("✅ Pushed to Firebase events log:")
        print(f"  PON: {parsed_msg['pon_no']}, ONU: {parsed_msg['onu_id']}")
        print(f"  MAC: {parsed_msg['macaddress']}")
        print(f"  Status: {final_status}")
        print(f"  Event Time: {event_datetime.strftime('%H:%M:%S')}")

        # Also update the specific ONU status in gboltdata structure
        pon_main = parsed_msg["pon_no"].split("/")[1]
        onu = parsed_msg['onu_id']
        path = f"kjnoltdata/kjnpon{pon_main}onu{onu}"
        event_time = event_datetime.strftime("%Y-%m-%d %H:%M:%S")
       
        db.reference(path).update({
            "pon_no": parsed_msg["pon_no"],
            "onu_id": parsed_msg["onu_id"],
            "macaddress": parsed_msg["macaddress"],
            "onustatus": final_status,
            "eventtime": event_time,
            "last_updated": current_local_time.strftime("%Y-%m-%d %H:%M:%S"),
            "eventtimeraw":event_time_raw
        })
        print(f"✅ Updated ONU status at: {path}")
        
    except Exception as e:
        print(f"❌ Firebase error: {e}")
        logging.error(f"Firebase push failed: {e}")
    
    print("="*50 + "\n")
    
    # Periodic cleanup of old history entries
    if len(onu_history) > 1000:  # Clean if too many entries
        cleanup_old_entries()

# =========================
# SCHEDULED CLEANUP THREAD
# =========================
def periodic_cleanup():
    """Run cleanup every CLEANUP_INTERVAL seconds"""
    import time
    while True:
        time.sleep(CLEANUP_INTERVAL)
        cleanup_old_entries()
        print(f"🧹 Cleaned up old history entries. Current entries: {len(onu_history)}")

# =========================
# MAIN EXECUTION
# =========================
if __name__ == "__main__":
    print("🚀 Starting SNMP Trap Receiver with Modified Status Logic")
    print("="*60)
    print("Event OID Mapping:")
    print(f"  5.51 → {EVENT_STATUS_MAP.get('1.3.6.1.4.1.37950.1.1.5.10.13.5.51', 'ONLINE')}")
    print(f"  5.23 → {EVENT_STATUS_MAP.get('1.3.6.1.4.1.37950.1.1.5.10.13.5.23', 'LOS/Fibre break')}")
    print(f"  5.30 → {EVENT_STATUS_MAP.get('1.3.6.1.4.1.37950.1.1.5.10.13.5.30', 'Powered-Off')}")
    print("\n📋 Modified 30-Second Rules:")
    print("  1. LOS (5.23) within 30s after Powered-Off (5.30) = Powered-Off")
    print("  2. Powered-Off (5.30) within 30s after LOS (5.23) = Powered-Off")
    print("  (Both cases result in Powered-Off status)")
    print("="*60)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    print("🧹 Started periodic cleanup thread")
    
    # Run diagnostic
    bind_ip = network_diagnostic()
    if not bind_ip:
        print("❌ Cannot bind to any interface. Exiting.")
        sys.exit(1)
    
    # Create SNMP engine
    snmpEngine = engine.SnmpEngine()
    
    # Configure transport
    print(f"\n🎧 Configuring SNMP trap receiver on {bind_ip}:166")
    
    config.addTransport(
        snmpEngine,
        udp.domainName,
        udp.UdpTransport().openServerMode((bind_ip, 166))
    )
    
    # Configure community
    print("🔑 Configuring SNMPv1 with community: public")
    config.addV1System(snmpEngine, 'my-area', 'public')
    
    # Also add a catch-all for any community
    config.addVacmUser(
        snmpEngine, 
        1,                    # securityModel (1 = SNMPv1)
        'my-area',            # securityName
        'noAuthNoPriv',       # securityLevel
        (1, 3, 6, 1),        # contextEngineId (dot prefix)
        (1, 3, 6, 1)         # contextName (dot prefix)
    )
    
    # Register callback
    ntfrcv.NotificationReceiver(snmpEngine, cbFun)
    
    # Start dispatcher
    try:
        print("\n⏳ Waiting for traps...")
        print("Press Ctrl+C to stop\n")
        snmpEngine.transportDispatcher.jobStarted(1)
        snmpEngine.transportDispatcher.runDispatcher()
    except KeyboardInterrupt:
        print("\n🛑 Stopping trap receiver...")
        print(f"Final history size: {len(onu_history)}")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        logging.error(f"Dispatcher error: {e}")