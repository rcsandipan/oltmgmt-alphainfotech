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
"dlsoltdata",
"gboltdata",
"kcwoltdata",
"kjnoltdata",
"ongcoltdata"
]
OLT_LOCATION_MAP = {
    "aptoltdata": "Ushabazar",
    "bproltdata": "Banerjee Para",
    "dlsoltdata": "Dhaleswar",
    "gboltdata": "GB",
    "kcwoltdata": "Kaman Chowmuhani",
    "kjnoltdata": "Kunjaban",
    "ongcoltdata": "ONGC Colony"
}

TARGET_NODE = "combinedlos"

FILTER_STATUSES = {
    "LOS/Fibre break"
}

INTERVAL_SECONDS = 1 * 60
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

                # if not isinstance(onu_data, dict):
                #     continue

                # onustatus = onu_data.get("onustatus")

                # if onustatus in FILTER_STATUSES:
                #     unique_key = f"{source}_{onu_key}"

                #     filtered_records[unique_key] = {
                #         **onu_data
                        
                #     }
                    # ✅ Only process actual ONU records
                if not isinstance(onu_data, dict):
                    continue

                # ✅ Must have onustatus key
                if "onustatus" not in onu_data:
                    continue

                onustatus = onu_data["onustatus"]

                if onustatus in FILTER_STATUSES:
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
