import os
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, render_template
from flask_pymongo import PyMongo
from flask_cors import CORS
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
from flask import send_from_directory
from difflib import SequenceMatcher

def restrict_to_localhost():
    allowed_ips = ["127.0.0.1", "::1"]
    if request.remote_addr not in allowed_ips:
        return jsonify({"error": "Access denied"}), 403

app = Flask(__name__)
CORS(app)

@app.route("/")
def home():
    return render_template("index.html")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# ================= MONGODB =================
app.config["MONGO_URI"] = "mongodb+srv://ovia_krishna:ovia123@lostandfound.prc7ls9.mongodb.net/lost_found_db?retryWrites=true&w=majority"
mongo = PyMongo(app)

# ================= EMAIL CONFIG =================
EMAIL_ADDRESS = "lostandfoundmanagement82@gmail.com"
EMAIL_PASSWORD = "byvhettbenmgahdj"

def send_email(to, subject, body):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        print("Email sent to:", to)
    except Exception as e:
        print("Email error:", e)

# ================= AUDIT LOG =================
def log_history(item_id, from_status, to_status, actor="system", note=""):
    mongo.db.history.insert_one({
        "itemId":     ObjectId(item_id),
        "fromStatus": from_status,
        "toStatus":   to_status,
        "actor":      actor,
        "note":       note,
        "timestamp":  datetime.utcnow()
    })

# ================= SIMILARITY =================
def similarity_score(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def find_matches(lost_name, lost_description, threshold=0.35):
    found_items = mongo.db.items.find({"status": "FOUND"})
    matches = []
    for item in found_items:
        lost_text  = f"{lost_name} {lost_description}".strip()
        found_text = f"{item.get('name','')} {item.get('publicDescription','')}".strip()
        score = similarity_score(lost_text, found_text)
        if score >= threshold:
            matches.append({
                "_id":               str(item["_id"]),
                "name":              item.get("name", ""),
                "category":          item.get("category", ""),
                "publicDescription": item.get("publicDescription", ""),
                "score":             round(score * 100)
            })
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches

def notify_lost_reporters(found_item_id, found_name, found_desc):
    searching = mongo.db.lost_reports.find({"status": "SEARCHING"})
    for report in searching:
        score = similarity_score(
            f"{report.get('name','')} {report.get('description','')}",
            f"{found_name} {found_desc}"
        )
        if score >= 0.35:
            send_email(
                report["email"],
                "Possible Match Found - Lost & Found",
                f"""Hello,\n\nA new item was just reported found that may be yours!\n\nYour lost item: {report.get('name')}\nFound item: {found_name}\nDescription: {found_desc}\nMatch Confidence: {round(score * 100)}%\n\nPlease visit the Lost & Found portal to verify and submit a claim.\n\nThank you,\nLost & Found Team"""
            )
            mongo.db.lost_reports.update_one(
                {"_id": report["_id"]},
                {"$set": {"status": "MATCHED", "matchedItemId": str(found_item_id)}}
            )

# ================= PUBLIC: ITEMS =================
@app.route("/items", methods=["GET"])
def get_items():
    try:
        items = mongo.db.items.find({"status": {"$ne": "DONATED"}})
        result = []
        for i in items:
            result.append({
                "_id":               str(i["_id"]),
                "name":              i.get("name"),
                "category":          i.get("category"),
                "publicDescription": i.get("publicDescription"),
                "status":            i.get("status")
            })
        return jsonify(result)
    except Exception as e:
        print("Fetch items error:", e)
        return jsonify([]), 500

@app.route("/donations", methods=["GET"])
def donations():
    try:
        items = mongo.db.items.find({"status": "DONATED"})
        result = []
        for i in items:
            i["_id"] = str(i["_id"])
            result.append(i)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": "Failed to fetch donations"}), 500

# ================= PUBLIC: CATEGORIES =================
@app.route("/categories", methods=["GET"])
def get_categories_public():
    try:
        cats = mongo.db.categories.find({})
        result = [{"_id": str(c["_id"]), "name": c.get("name"), "description": c.get("description", "")} for c in cats]
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

# ================= PUBLIC: LOST REPORT =================
@app.route("/lost", methods=["POST"])
def report_lost():
    try:
        data  = request.json
        name  = data.get("name", "").strip()
        desc  = data.get("description", "").strip()
        email = data.get("email", "").strip()
        date  = data.get("dateLost", "")

        if not name or not email:
            return jsonify({"error": "Name and email are required"}), 400

        result = mongo.db.lost_reports.insert_one({
            "name":        name,
            "category":    data.get("category", ""),
            "description": desc,
            "email":       email,
            "dateLost":    date,
            "status":      "SEARCHING",
            "createdAt":   datetime.utcnow()
        })

        matches = find_matches(name, desc)
        if matches:
            best = matches[0]
            send_email(email, "Possible Match Found - Lost & Found",
                f"""Hello,\n\nWe found a possible match for your lost item "{name}"!\n\nMatched Item: {best['name']}\nCategory: {best['category']}\nDescription: {best['publicDescription']}\nMatch Confidence: {best['score']}%\n\nPlease visit our Lost & Found portal to verify and submit a claim.\n\nThank you,\nLost & Found Team""")
            mongo.db.lost_reports.update_one(
                {"_id": result.inserted_id},
                {"$set": {"status": "MATCHED", "matchedItemId": best["_id"]}}
            )

        return jsonify({"success": True, "matches": matches})
    except Exception as e:
        print("Lost report error:", e)
        return jsonify({"error": "Server error"}), 500

# ================= PUBLIC: CLAIM =================
@app.route("/claim", methods=["POST"])
def submit_claim():
    try:
        item_id = request.form.get("itemId")
        proof   = request.form.get("proof")
        email   = request.form.get("email")
        image   = request.files.get("image")

        if not item_id or not proof or not email:
            return jsonify({"error": "Missing required fields"}), 400

        item = mongo.db.items.find_one({"_id": ObjectId(item_id)})
        if not item:
            return jsonify({"error": "Item not found"}), 404

        image_filename = None
        if image:
            filename       = secure_filename(image.filename)
            image_filename = f"{datetime.utcnow().timestamp()}_{filename}"
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))

        mongo.db.claims.insert_one({
            "itemId":    ObjectId(item_id),
            "proof":     proof,
            "email":     email,
            "image":     image_filename,
            "status":    "PENDING",
            "createdAt": datetime.utcnow()
        })

        mongo.db.items.update_one({"_id": ObjectId(item_id)}, {"$set": {"status": "PENDING"}})
        log_history(item_id, "FOUND", "PENDING", actor=email, note="Claim submitted")
        return jsonify({"success": True})
    except Exception as e:
        print("Claim error:", e)
        return jsonify({"error": "Server error"}), 500

# ================= PUBLIC: MESSAGES =================
@app.route("/messages", methods=["POST"])
def send_message():
    try:
        data    = request.json
        name    = data.get("name", "").strip()
        email   = data.get("email", "").strip()
        subject = data.get("subject", "").strip()
        body    = data.get("message", "").strip()

        if not name or not email or not body:
            return jsonify({"error": "Name, email and message are required"}), 400

        mongo.db.messages.insert_one({
            "name":      name,
            "email":     email,
            "subject":   subject,
            "message":   body,
            "read":      False,
            "createdAt": datetime.utcnow()
        })
        return jsonify({"success": True})
    except Exception as e:
        print("Message error:", e)
        return jsonify({"error": "Server error"}), 500

# ================= PUBLIC: PAGE CONTENT =================
@app.route("/page/<page_name>", methods=["GET"])
def get_page(page_name):
    try:
        doc = mongo.db.pages.find_one({"page": page_name})
        if doc:
            doc["_id"] = str(doc["_id"])
            return jsonify(doc)
        return jsonify({}), 200
    except Exception as e:
        return jsonify({}), 500

# ================= ADMIN: FOUND ITEM =================
@app.route("/found", methods=["POST"])
def add_found():
    try:
        data   = request.json
        result = mongo.db.items.insert_one({
            "name":               data.get("name"),
            "category":           data.get("category"),
            "publicDescription":  data.get("publicDescription"),
            "privateDescription": data.get("privateDescription"),
            "dateFound":          data.get("dateFound"),
            "status":             "FOUND",
            "createdAt":          datetime.utcnow()
        })
        log_history(result.inserted_id, None, "FOUND", actor="staff", note="Item reported found")
        notify_lost_reporters(result.inserted_id, data.get("name",""), data.get("publicDescription",""))
        return jsonify({"success": True})
    except Exception as e:
        print("Add item error:", e)
        return jsonify({"success": False}), 500

# ================= ADMIN: ITEM MANAGEMENT =================
@app.route("/admin/items", methods=["GET"])
def admin_get_items():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        items  = mongo.db.items.find(sort=[("createdAt", -1)])
        result = []
        for i in items:
            result.append({
                "_id":                str(i["_id"]),
                "name":               i.get("name"),
                "category":           i.get("category"),
                "publicDescription":  i.get("publicDescription"),
                "privateDescription": i.get("privateDescription"),
                "dateFound":          i.get("dateFound"),
                "status":             i.get("status"),
                "createdAt":          i["createdAt"].strftime("%Y-%m-%d") if i.get("createdAt") else ""
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

@app.route("/admin/items/<item_id>", methods=["GET"])
def admin_get_item(item_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        i = mongo.db.items.find_one({"_id": ObjectId(item_id)})
        if not i:
            return jsonify({"error": "Not found"}), 404
        i["_id"] = str(i["_id"])
        if i.get("createdAt"):
            i["createdAt"] = i["createdAt"].strftime("%Y-%m-%d")
        return jsonify(i)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/items/<item_id>", methods=["PUT"])
def admin_update_item(item_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        data = request.json
        mongo.db.items.update_one(
            {"_id": ObjectId(item_id)},
            {"$set": {
                "name":               data.get("name"),
                "category":           data.get("category"),
                "publicDescription":  data.get("publicDescription"),
                "privateDescription": data.get("privateDescription"),
                "dateFound":          data.get("dateFound"),
                "status":             data.get("status")
            }}
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/admin/items/<item_id>", methods=["DELETE"])
def admin_delete_item(item_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        mongo.db.items.delete_one({"_id": ObjectId(item_id)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ================= ADMIN: CLAIMS =================
@app.route("/admin/claims", methods=["GET"])
def view_claims():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    claims = mongo.db.claims.find({"status": "PENDING"})
    result = []
    for c in claims:
        item = mongo.db.items.find_one({"_id": c["itemId"]})
        result.append({
            "_id":   str(c["_id"]),
            "email": c.get("email"),
            "proof": c.get("proof"),
            "image": c.get("image"),
            "itemId": {
                "_id":                str(item["_id"]) if item else "",
                "name":               item.get("name") if item else "",
                "privateDescription": item.get("privateDescription", "") if item else ""
            }
        })
    return jsonify(result)

@app.route("/admin/claim/approve/<claim_id>", methods=["POST"])
def approve_claim(claim_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        claim = mongo.db.claims.find_one({"_id": ObjectId(claim_id)})
        if not claim:
            return jsonify({"success": False}), 404
        mongo.db.claims.update_one({"_id": ObjectId(claim_id)}, {"$set": {"status": "APPROVED"}})
        mongo.db.items.update_one({"_id": claim["itemId"]}, {"$set": {"status": "CLAIMED"}})
        log_history(claim["itemId"], "PENDING", "CLAIMED", actor="admin", note="Claim approved by admin")
        item = mongo.db.items.find_one({"_id": claim["itemId"]})
        send_email(claim["email"], "Claim Approved - Lost & Found",
            f"""Hello,\n\nYour claim for "{item.get('name')}" has been APPROVED.\n\nPlease contact the office to collect your item.\n\nThank you,\nLost & Found Team""")
        return jsonify({"success": True})
    except Exception as e:
        print("Approve error:", e)
        return jsonify({"success": False}), 500

@app.route("/admin/claim/reject/<claim_id>", methods=["POST"])
def reject_claim(claim_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        claim = mongo.db.claims.find_one({"_id": ObjectId(claim_id)})
        if not claim:
            return jsonify({"success": False}), 404
        mongo.db.claims.update_one({"_id": ObjectId(claim_id)}, {"$set": {"status": "REJECTED"}})
        mongo.db.items.update_one({"_id": claim["itemId"]}, {"$set": {"status": "FOUND"}})
        log_history(claim["itemId"], "PENDING", "FOUND", actor="admin", note="Claim rejected, item re-listed")
        item = mongo.db.items.find_one({"_id": claim["itemId"]})
        send_email(claim["email"], "Claim Rejected - Lost & Found",
            f"""Hello,\n\nYour claim for "{item.get('name')}" has been REJECTED.\n\nThe provided proof did not match.\n\nThank you,\nLost & Found Team""")
        return jsonify({"success": True})
    except Exception as e:
        print("Reject error:", e)
        return jsonify({"success": False}), 500

# ================= ADMIN: LOST REPORTS =================
@app.route("/admin/lost-reports", methods=["GET"])
def get_lost_reports():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        reports = mongo.db.lost_reports.find(sort=[("createdAt", -1)])
        result  = []
        for r in reports:
            result.append({
                "_id":         str(r["_id"]),
                "name":        r.get("name"),
                "category":    r.get("category"),
                "description": r.get("description"),
                "email":       r.get("email"),
                "dateLost":    r.get("dateLost"),
                "status":      r.get("status"),
                "createdAt":   r["createdAt"].strftime("%Y-%m-%d")
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

# ================= ADMIN: CATEGORY MANAGEMENT =================
@app.route("/admin/categories", methods=["GET"])
def get_categories():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        cats   = mongo.db.categories.find({})
        result = []
        for c in cats:
            result.append({
                "_id":         str(c["_id"]),
                "name":        c.get("name"),
                "description": c.get("description", ""),
                "createdAt":   c["createdAt"].strftime("%Y-%m-%d") if c.get("createdAt") else ""
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

@app.route("/admin/categories", methods=["POST"])
def add_category():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        data = request.json
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Name is required"}), 400
        existing = mongo.db.categories.find_one({"name": {"$regex": f"^{name}$", "$options": "i"}})
        if existing:
            return jsonify({"error": "Category already exists"}), 400
        mongo.db.categories.insert_one({
            "name":        name,
            "description": data.get("description", ""),
            "createdAt":   datetime.utcnow()
        })
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/categories/<cat_id>", methods=["PUT"])
def update_category(cat_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        data = request.json
        mongo.db.categories.update_one(
            {"_id": ObjectId(cat_id)},
            {"$set": {"name": data.get("name"), "description": data.get("description", "")}}
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/categories/<cat_id>", methods=["DELETE"])
def delete_category(cat_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        mongo.db.categories.delete_one({"_id": ObjectId(cat_id)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= ADMIN: USER MANAGEMENT =================
@app.route("/admin/users", methods=["GET"])
def get_users():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        users  = mongo.db.users.find(sort=[("createdAt", -1)])
        result = []
        for u in users:
            result.append({
                "_id":       str(u["_id"]),
                "name":      u.get("name"),
                "email":     u.get("email"),
                "role":      u.get("role", "public"),
                "phone":     u.get("phone", ""),
                "createdAt": u["createdAt"].strftime("%Y-%m-%d") if u.get("createdAt") else ""
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

@app.route("/admin/users", methods=["POST"])
def add_user():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        data  = request.json
        name  = data.get("name", "").strip()
        email = data.get("email", "").strip()
        if not name or not email:
            return jsonify({"error": "Name and email are required"}), 400
        existing = mongo.db.users.find_one({"email": email})
        if existing:
            return jsonify({"error": "User with this email already exists"}), 400
        mongo.db.users.insert_one({
            "name":      name,
            "email":     email,
            "phone":     data.get("phone", ""),
            "role":      data.get("role", "public"),
            "createdAt": datetime.utcnow()
        })
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/users/<user_id>", methods=["PUT"])
def update_user(user_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        data = request.json
        mongo.db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "name":  data.get("name"),
                "email": data.get("email"),
                "phone": data.get("phone", ""),
                "role":  data.get("role", "public")
            }}
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        mongo.db.users.delete_one({"_id": ObjectId(user_id)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= ADMIN: MESSAGES =================
@app.route("/admin/messages", methods=["GET"])
def get_messages():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        msgs   = mongo.db.messages.find(sort=[("createdAt", -1)])
        result = []
        for m in msgs:
            result.append({
                "_id":       str(m["_id"]),
                "name":      m.get("name"),
                "email":     m.get("email"),
                "subject":   m.get("subject", ""),
                "message":   m.get("message"),
                "read":      m.get("read", False),
                "createdAt": m["createdAt"].strftime("%Y-%m-%d %H:%M") if m.get("createdAt") else ""
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

@app.route("/admin/messages/<msg_id>/read", methods=["POST"])
def mark_message_read(msg_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        mongo.db.messages.update_one({"_id": ObjectId(msg_id)}, {"$set": {"read": True}})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/messages/<msg_id>", methods=["DELETE"])
def delete_message(msg_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        mongo.db.messages.delete_one({"_id": ObjectId(msg_id)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= ADMIN: PAGE MANAGEMENT =================
@app.route("/admin/pages/<page_name>", methods=["GET"])
def get_page_admin(page_name):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        doc = mongo.db.pages.find_one({"page": page_name})
        if doc:
            doc["_id"] = str(doc["_id"])
            return jsonify(doc)
        return jsonify({"page": page_name}), 200
    except Exception as e:
        return jsonify({}), 500

@app.route("/admin/pages/<page_name>", methods=["POST"])
def save_page(page_name):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        data            = request.json
        data["page"]    = page_name
        data["updatedAt"] = datetime.utcnow()
        mongo.db.pages.update_one({"page": page_name}, {"$set": data}, upsert=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= ADMIN: STATS =================
@app.route("/admin/stats", methods=["GET"])
def get_stats():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        from collections import defaultdict
        status_results   = list(mongo.db.items.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}]))
        category_results = list(mongo.db.items.aggregate([{"$group": {"_id": "$category", "count": {"$sum": 1}}}, {"$sort": {"count": -1}}]))
        claim_results    = list(mongo.db.claims.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}]))

        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_items   = mongo.db.items.find({"createdAt": {"$gte": seven_days_ago}})
        daily_counts   = defaultdict(int)
        for item in recent_items:
            day = item["createdAt"].strftime("%Y-%m-%d")
            daily_counts[day] += 1

        timeline = []
        for i in range(7):
            day = (datetime.utcnow() - timedelta(days=6 - i)).strftime("%Y-%m-%d")
            timeline.append({"date": day, "count": daily_counts.get(day, 0)})

        return jsonify({
            "statusBreakdown":   {r["_id"]: r["count"] for r in status_results},
            "categoryBreakdown": {r["_id"]: r["count"] for r in category_results if r["_id"]},
            "claimBreakdown":    {r["_id"]: r["count"] for r in claim_results},
            "timeline":          timeline,
            "totals": {
                "items":    mongo.db.items.count_documents({}),
                "claims":   mongo.db.claims.count_documents({}),
                "pending":  mongo.db.claims.count_documents({"status": "PENDING"}),
                "donated":  mongo.db.items.count_documents({"status": "DONATED"}),
                "users":    mongo.db.users.count_documents({}),
                "messages": mongo.db.messages.count_documents({"read": False})
            }
        })
    except Exception as e:
        print("Stats error:", e)
        return jsonify({"error": "Failed to fetch stats"}), 500

# ================= ADMIN: HISTORY =================
@app.route("/admin/history/<item_id>", methods=["GET"])
def get_item_history(item_id):
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        logs   = mongo.db.history.find({"itemId": ObjectId(item_id)}, sort=[("timestamp", 1)])
        result = []
        for log in logs:
            result.append({
                "fromStatus": log.get("fromStatus"),
                "toStatus":   log.get("toStatus"),
                "actor":      log.get("actor"),
                "note":       log.get("note"),
                "timestamp":  log["timestamp"].strftime("%Y-%m-%d %H:%M UTC")
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

@app.route("/admin/history", methods=["GET"])
def get_all_history():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        logs   = mongo.db.history.find({}, sort=[("timestamp", -1)], limit=50)
        result = []
        for log in logs:
            item = mongo.db.items.find_one({"_id": log["itemId"]})
            result.append({
                "itemName":   item.get("name") if item else "Unknown",
                "fromStatus": log.get("fromStatus"),
                "toStatus":   log.get("toStatus"),
                "actor":      log.get("actor"),
                "note":       log.get("note"),
                "timestamp":  log["timestamp"].strftime("%Y-%m-%d %H:%M UTC")
            })
        return jsonify(result)
    except Exception as e:
        return jsonify([]), 500

# ================= ADMIN: IMAGES =================
@app.route("/admin/images", methods=["GET"])
def get_uploaded_images():
    restricted = restrict_to_localhost()
    if restricted:
        return restricted
    try:
        files = os.listdir(app.config["UPLOAD_FOLDER"])
        return jsonify(files)
    except Exception as e:
        return jsonify([])

# ================= SERVER =================
if __name__ == "__main__":
    app.run(port=3000, debug=True)
    