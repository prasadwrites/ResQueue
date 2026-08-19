"""Admin Agent — validates administrative control, privilege overrides and
propagation of admin actions.

Provisions its own target users (victim, mallory) so it runs independently of
the User Agent, even in parallel.
"""
from common import Client, TestLog

ADMIN_EMAIL = "admin@resqueue.local"
ADMIN_PASSWORD = "AdminPass!234"
PW = "TargetPass!234"


def main():
    log = TestLog("Admin Agent")
    admin, victim, mallory = Client(), Client(), Client()

    # ------------------------------------------------------------ admin login
    status, data = admin.login(ADMIN_EMAIL, ADMIN_PASSWORD)
    log.check("Admin: authentication", "Login with seeded admin account",
              "200, is_admin=true", status, data, 200,
              lambda d: d.get("is_admin") is True)

    # provision targets
    victim.signup_and_login("victim@example.com", PW)
    mallory.signup_and_login("mallory@example.com", PW)
    _, vq = victim.call("POST", "/api/queues", {"name": "Victim Private Queue",
                                               "type": "private"})
    vq_id = vq.get("queue_id", "")
    _, mq = mallory.call("POST", "/api/queues", {"name": "Mallory Public Queue",
                                                "type": "public"})
    mq_id = mq.get("queue_id", "")
    mallory.call("POST", f"/api/queues/{mq_id}/join")
    log.record("Setup", "Provision victim + mallory accounts and their queues",
               "two users, one private + one public queue",
               f"victim queue {vq_id}, mallory queue {mq_id}",
               bool(victim.token and mallory.token and vq_id and mq_id))

    # ------------------------------------------------- visibility & inspection
    status, users = admin.call("GET", "/api/admin/users")
    emails = [u["email"] for u in users] if isinstance(users, list) else []
    log.record("Admin: visibility", "List ALL user accounts",
               "every registered account visible",
               f"{len(emails)} accounts incl. victim/mallory: "
               f"{'victim@example.com' in emails and 'mallory@example.com' in emails}",
               "victim@example.com" in emails and "mallory@example.com" in emails)

    status, data = admin.call("GET", f"/api/queues/{vq_id}")
    log.check("Admin: visibility", "Inspect victim's PRIVATE queue (never shared with admin)",
              "200 — admin sees any queue", status, data, 200,
              lambda d: d.get("type") == "private")

    status, allq = admin.call("GET", "/api/admin/queues")
    qids = [q["queue_id"] for q in allq] if isinstance(allq, list) else []
    log.record("Admin: visibility", "List ALL queues (public + private)",
               "both test queues present",
               f"{len(qids)} queues, contains both: {vq_id in qids and mq_id in qids}",
               vq_id in qids and mq_id in qids)

    # ------------------------------------------------- privilege enforcement
    status, data = mallory.call("GET", "/api/admin/users")
    log.check("Admin: security", "Regular user calls admin endpoint",
              "403 admin required", status, data, 403)

    status, data = Client().call("GET", "/api/admin/queues")
    log.check("Admin: security", "Unauthenticated call to admin endpoint",
              "401 auth required", status, data, 401)

    # ------------------------------------------------- queue moderation
    status, data = admin.call("POST", f"/api/admin/queues/{mq_id}/suspend")
    log.check("Admin: queue control", "Suspend mallory's queue",
              "200 suspended", status, data, 200)

    status, data = victim.call("POST", f"/api/queues/{mq_id}/join")
    log.check("Admin: queue control", "User tries to join SUSPENDED queue",
              "403 queue suspended (action propagated)", status, data, 403)

    status, data = admin.call("POST", f"/api/admin/queues/{mq_id}/restore")
    joined_after, _ = victim.call("POST", f"/api/queues/{mq_id}/join")
    log.record("Admin: queue control", "Restore queue, then user joins",
               "restore 200, join succeeds afterwards",
               f"restore HTTP {status}, join HTTP {joined_after}",
               status == 200 and joined_after == 201)

    status, data = admin.call("DELETE", f"/api/queues/{mq_id}/members/victim@example.com")
    log.check("Admin: override", "Admin removes a member from a queue admin does NOT own",
              "200 — admin overrides owner-only rule", status, data, 200)

    # ------------------------------------------------- account moderation
    status, data = admin.call("POST", "/api/admin/users/victim@example.com/suspend")
    log.check("Admin: account control", "Suspend victim's account",
              "200 suspended", status, data, 200)

    status, data = victim.call("GET", "/api/queues")
    log.check("Admin: account control", "Suspended user uses their EXISTING token",
              "401 — suspension propagates to live sessions", status, data, 401)

    status, data = Client().login("victim@example.com", PW)
    log.check("Admin: account control", "Suspended user tries to log in again",
              "403 account suspended", status, data, 403)

    status, data = admin.call("POST", "/api/admin/users/victim@example.com/restore")
    relogin, _ = victim.login("victim@example.com", PW)
    log.record("Admin: account control", "Restore victim, then victim logs in",
               "restore 200, login 200",
               f"restore HTTP {status}, login HTTP {relogin}",
               status == 200 and relogin == 200)

    status, data = admin.call("DELETE", "/api/admin/users/mallory@example.com")
    log.check("Admin: account control", "Delete mallory's account",
              "200 deleted", status, data, 200)

    status, data = mallory.call("GET", "/api/queues")
    log.check("Admin: account control", "Deleted user's token is dead",
              "401 — deletion propagates to sessions", status, data, 401)

    status, data = admin.call("GET", f"/api/queues/{mq_id}")
    log.check("Admin: account control",
              "Deleted user's queue is gone too (cascade)",
              "404 — owned queues removed with account", status, data, 404)

    # ------------------------------------------------- admin self-protection
    status, data = admin.call("POST", f"/api/admin/users/{ADMIN_EMAIL}/suspend")
    log.check("Admin: self-protection", "Suspend the admin account itself",
              "400 refused", status, data, 400)

    status, data = admin.call("DELETE", f"/api/admin/users/{ADMIN_EMAIL}")
    log.check("Admin: self-protection", "Delete the admin account itself",
              "400 refused", status, data, 400)

    # cleanup so reruns start clean-ish
    admin.call("DELETE", f"/api/queues/{vq_id}")

    log.save("admin_results.json")


if __name__ == "__main__":
    main()
