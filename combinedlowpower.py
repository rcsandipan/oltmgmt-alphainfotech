import firebase_admin
from firebase_admin import credentials, db
import time
from datetime import datetime

# ---------------- CONFIG ----------------
SERVICE_ACCOUNT_FILE = "serviceAccountKey.json"
DATABASE_URL = "https://oltmgmt-default-rtdb.asia-southeast1.firebasedatabase.app"

SOURCE_NODES = [
"aptoltdata",
"bproltdata",
"gboltdata",
"kcwoltdata",
"kjnoltdata",
"ongcoltdata",
"dlsoltdata"
]
OLT_LOCATION_MAP = {
    "aptoltdata": "Ushabazar",
    "bproltdata": "Banerjee Para",
    "gboltdata": "GB",
    "kcwoltdata": "Kaman Chowmuhani",
    "kjnoltdata": "Kunjaban",
    "ongcoltdata": "ONGC Colony",
    "dlsoltdata": "Dhaleswar"
}

TARGET_NODE = "combinedlowpower"

# FILTER_STATUSES = {
#     "LOS/Fibre break"
# }

INTERVAL_SECONDS = 1 * 600
# ---------------------------------------


# -------- Firebase Init --------
cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)
firebase_admin.initialize_app(cred, {
    "databaseURL": DATABASE_URL
})


def collect_and_push():
    print(f"\n🔄 Scan started @ {datetime.now()}")

    filtered_records = {}

    for source in SOURCE_NODES:
        try:
            source_ref = db.reference(source)
            source_data = source_ref.get()

            if not isinstance(source_data, dict):
                continue

            # 🔁 Iterate EACH ONU inside the source node
            for onu_key, onu_data in source_data.items():

              
                    # ✅ Only process actual ONU records
                if not isinstance(onu_data, dict):
                    continue

                # ✅ Must have onustatus key
                if "rxpower" not in onu_data:
                    continue
                rx_value = onu_data["rxpower"]
                if rx_value is None or str(rx_value).strip() == "":
                    continue

                onurxpower = float(onu_data["rxpower"])


                if onurxpower <-26:
                    unique_key = f"{source}_{onu_key}"

                    filtered_records[unique_key] = {
                      **onu_data,
                      "olt":OLT_LOCATION_MAP.get(source, "UNKNOWN")
                    }

        except Exception as e:
            print(f"❌ Error reading {source}: {e}")

    target_ref = db.reference(TARGET_NODE)

    # 🧹 Clear old data
    target_ref.delete()

    # ⬆️ Push fresh data
    
    target_ref.set(filtered_records)


    print(f"✅ {len(filtered_records)} records pushed")


# -------- Scheduler --------
if __name__ == "__main__":
    while True:
        collect_and_push()
        time.sleep(INTERVAL_SECONDS)
