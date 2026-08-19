"""Quick manual smoke test for: member listing w/ timestamps, wait-time,
default board (created/joined/invited), and search — run once, print results."""
from common import Client

PW = "SmokeTest!234"


def show(label, status, data):
    print(f"\n-- {label} [{status}] --")
    print(data)


owner, joinee, outsider = Client(), Client(), Client()
owner.signup_and_login("smoke-owner@example.com", PW)
joinee.signup_and_login("smoke-joinee@example.com", PW)
outsider.signup_and_login("smoke-outsider@example.com", PW)

status, pub = owner.call("POST", "/api/queues", {"name": "Smoke Public Line", "type": "public"})
pub_id = pub["queue_id"]
status, priv = owner.call("POST", "/api/queues", {"name": "Smoke Private Room", "type": "private"})
priv_id = priv["queue_id"]

# wait time
status, data = owner.call("POST", f"/api/queues/{pub_id}/wait-time", {"minutes": 12})
show("owner sets wait time = 12", status, data)

status, data = joinee.call("POST", f"/api/queues/{pub_id}/wait-time", {"minutes": 99})
show("non-owner tries to set wait time", status, data)

status, data = joinee.call("POST", f"/api/queues/{pub_id}/join")
show("joinee joins public queue", status, data)
print("  -> joinee sees wait_minutes on join response:", data.get("wait_minutes"))

status, data = joinee.call("GET", f"/api/queues/{pub_id}")
show("joinee inspects queue, sees wait_minutes", status, data)
print("  -> member_details present for non-owner? (should be absent):", "member_details" in data)

status, data = owner.call("GET", f"/api/queues/{pub_id}")
show("owner inspects own queue -- expects member_details with joined_at", status, data)

# invite flow
status, data = owner.call("POST", f"/api/queues/{priv_id}/share", {"email": "smoke-joinee@example.com"})
show("owner invites joinee to private queue", status, data)

status, data = joinee.call("GET", "/api/queues")
show("joinee's default board (created/joined/invited)", status, data)
invited_ids = [q["queue_id"] for q in data.get("invited", [])]
print("  -> private queue appears under invited:", priv_id in invited_ids)

status, data = outsider.call("GET", "/api/queues")
show("outsider's default board (should be all empty)", status, data)

# search
status, data = outsider.call("GET", f"/api/queues/search?q={pub_id}")
show("search by 10-digit id (public, should find it)", status, data)

status, data = outsider.call("GET", f"/api/queues/search?q={priv_id}")
show("search by 10-digit id of PRIVATE queue outsider has no access to", status, data)

status, data = outsider.call("GET", "/api/queues/search?q=smoke-owner@example.com")
show("search by owner email (should find public only)", status, data)

status, data = outsider.call("GET", "/api/queues/search?q=Smoke Public")
show("search by name substring", status, data)

status, data = outsider.call("GET", "/api/queues/search?q=Smoke Private")
show("search by name substring matching a PRIVATE queue name (should be empty)", status, data)
