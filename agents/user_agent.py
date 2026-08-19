"""User Agent — exercises every user-level ResQueue workflow and edge case.

Provisions its own actors (alice = queue owner, bob = participant, carol =
uninvited outsider) so it can run independently of the Admin Agent.
"""
from common import Client, TestLog

PW = "UserPass!234"


def main():
    log = TestLog("User Agent")
    alice, bob, carol = Client(), Client(), Client()

    # ---------------------------------------------------------- 1. accounts
    status, data = alice.register("alice@example.com", PW)
    log.check("Account: registration", "Register alice@example.com with valid email",
              "201 account created, verification code emailed", status, data, 201)

    status, data = alice.register("not-an-email", PW)
    log.check("Account: registration", "Register with invalid email 'not-an-email'",
              "400 rejected", status, data, 400)

    status, data = alice.register("weak@example.com", "short")
    log.check("Account: registration", "Register with 5-char password",
              "400 rejected (min 8 chars)", status, data, 400)

    status, data = alice.register("alice@example.com", PW)
    log.check("Account: registration", "Register duplicate email alice@example.com",
              "409 conflict", status, data, 409)

    code = alice.fetch_code("alice@example.com")
    log.record("Account: verification", "Receive verification code via simulated outbox",
               "6-digit code delivered to alice's inbox",
               f"code received: {code}", code is not None and len(code) == 6)

    status, data = alice.login("alice@example.com", PW)
    log.check("Account: verification", "Login BEFORE verifying email",
              "403 email not verified", status, data, 403)

    status, data = alice.verify("alice@example.com", "000000" if code != "000000" else "111111")
    log.check("Account: verification", "Verify with WRONG code",
              "400 invalid code", status, data, 400)

    status, data = alice.verify("alice@example.com", code)
    log.check("Account: verification", "Verify with correct code",
              "200 email verified", status, data, 200)

    status, data = alice.login("alice@example.com", "WrongPass!999")
    log.check("Account: login", "Login with wrong password",
              "401 invalid credentials", status, data, 401)

    status, data = alice.login("alice@example.com", PW)
    log.check("Account: login", "Login with correct email + password",
              "200 with session token", status, data, 200,
              lambda d: bool(d.get("token")))

    # provision the other two actors via the same (already-proven) flow
    bob.signup_and_login("bob@example.com", PW)
    carol.signup_and_login("carol@example.com", PW)
    log.record("Account: provisioning", "Onboard bob and carol via full signup flow",
               "both logged in with tokens",
               f"bob token: {bool(bob.token)}, carol token: {bool(carol.token)}",
               bob.token and carol.token)

    # ------------------------------------------------------ 2. queue creation
    status, pub = alice.call("POST", "/api/queues",
                             {"name": "Alice Public Deli", "type": "public",
                              "expiry_date": "2027-01-01T00:00:00"})
    pub_id = pub.get("queue_id", "")
    log.check("Queue: creation", "Create public queue with name + expiry",
              "201; unique 10-digit queue id assigned", status, pub, 201,
              lambda d: len(d.get("queue_id", "")) == 10 and d["queue_id"].isdigit()
              and d.get("expiry_date") == "2027-01-01T00:00:00")

    status, priv = alice.call("POST", "/api/queues",
                              {"name": "Alice Private Club", "type": "private"})
    priv_id = priv.get("queue_id", "")
    log.check("Queue: creation", "Create private queue",
              "201 with type=private", status, priv, 201,
              lambda d: d.get("type") == "private")

    status, data = alice.call("POST", "/api/queues", {"name": ""})
    log.check("Queue: creation", "Create queue with empty name",
              "400 name required", status, data, 400)

    status, data = alice.call("POST", "/api/queues",
                              {"name": "Bad expiry", "expiry_date": "next tuesday"})
    log.check("Queue: creation", "Create queue with malformed expiry date",
              "400 invalid expiry", status, data, 400)

    status, handle_q = alice.call("POST", "/api/queues",
                                  {"name": ">Handle Test", "type": "public"})
    handle_id = handle_q.get("queue_id", "")
    log.check("Queue: reference handle", "Create a queue named '>Handle Test'",
              "201; leading '>' stripped, stored name is 'Handle Test'", status, handle_q, 201,
              lambda d: d.get("name") == "Handle Test")

    # ------------------------------------------------------ 3. default board + discovery
    status, data = bob.call("GET", "/api/queues")
    log.check("Queue: default view", "Bob's default board right after Alice creates two queues",
              "empty — nothing created, joined, or invited yet", status, data, 200,
              lambda d: d["created_by_me"] == [] and d["joined_by_me"] == [] and d["invited"] == [])

    status, data = bob.call("GET", f"/api/queues/search?q={pub_id}")
    log.check("Queue: search", "Bob searches by Alice's public queue's 10-digit ID",
              "200, exact match found", status, data, 200,
              lambda d: len(d) == 1 and d[0]["queue_id"] == pub_id)

    status, data = bob.call("GET", "/api/queues/search?q=Alice Public")
    log.check("Queue: search", "Bob searches by a substring of the public queue's name",
              "200, match found", status, data, 200,
              lambda d: any(x["queue_id"] == pub_id for x in d))

    status, data = bob.call("GET", "/api/queues/search?q=alice@example.com")
    log.check("Queue: search", "Bob searches by owner email alice@example.com",
              "200, finds her public queue (only)", status, data, 200,
              lambda d: any(x["queue_id"] == pub_id for x in d)
              and all(x["type"] == "public" for x in d))

    status, data = bob.call("GET", f"/api/queues/search?q={priv_id}")
    log.check("Queue: search", "Bob searches by the PRIVATE queue's ID before being invited",
              "200, empty — not discoverable", status, data, 200,
              lambda d: d == [])

    status, data = bob.call("GET", "/api/queues/search?q=Alice Private")
    log.check("Queue: search", "Bob searches by the PRIVATE queue's name",
              "200, empty — name search only covers public queues", status, data, 200,
              lambda d: d == [])

    status, data = bob.call("GET", "/api/queues/search?q=>Handle Test")
    log.check("Queue: reference handle", "Bob searches using the '>Handle Test' reference form",
              "200, finds the queue by name after stripping '>'", status, data, 200,
              lambda d: any(x["queue_id"] == handle_id for x in d))

    status, data = bob.call("GET", f"/api/queues/search?q=>{pub_id}")
    log.check("Queue: reference handle", "Bob searches '>' + the public queue's numeric ID",
              "200, empty — '>' forces a name lookup, bypassing ID auto-detection", status, data, 200,
              lambda d: d == [])

    status, data = carol.call("GET", f"/api/queues/{priv_id}")
    log.check("Queue: visibility", "Carol (uninvited) inspects private queue directly by ID",
              "404 not found (existence hidden)", status, data, 404)

    status, data = carol.call("POST", f"/api/queues/{priv_id}/join")
    log.check("Queue: visibility", "Carol (uninvited) tries to JOIN private queue",
              "404 rejected", status, data, 404)

    status, data = alice.call("POST", f"/api/queues/{priv_id}/share",
                              {"email": "bob@example.com"})
    log.check("Queue: sharing", "Alice explicitly invites bob (by email) to the private queue",
              "200 shared (notification emailed)", status, data, 200)

    status, data = carol.call("POST", f"/api/queues/{priv_id}/share",
                              {"email": "carol@example.com"})
    log.check("Queue: sharing", "Carol (non-owner) tries to share Alice's private queue",
              "403/404 rejected", status, data, 404 if status == 404 else 403)

    status, data = bob.call("GET", f"/api/queues/{priv_id}")
    log.check("Queue: sharing", "Bob inspects private queue AFTER being invited",
              "200 queue visible", status, data, 200)

    status, data = bob.call("GET", "/api/queues")
    invited_ids = [q["queue_id"] for q in data.get("invited", [])]
    log.record("Queue: default view", "Bob's default board after the invite, before joining",
               "private queue appears under 'invited', not 'joined'",
               f"invited: {invited_ids}", priv_id in invited_ids)

    # ------------------------------------------------------ 4. participation
    status, data = bob.call("POST", f"/api/queues/{pub_id}/join")
    log.check("Queue: participation", "Bob joins public queue",
              "201 joined at position 1", status, data, 201,
              lambda d: d.get("your_position") == 1)

    status, data = bob.call("POST", f"/api/queues/{pub_id}/join")
    log.check("Queue: participation", "Bob joins the SAME queue again",
              "409 already a member", status, data, 409)

    status, data = bob.call("POST", f"/api/queues/{priv_id}/join")
    log.check("Queue: participation", "Bob joins private queue after invite",
              "201 joined", status, data, 201)

    carol.call("POST", f"/api/queues/{pub_id}/join")
    status, data = carol.call("GET", f"/api/queues/{pub_id}")
    log.check("Queue: participation",
              "Carol checks her position, member count and owner",
              "position 2 of 2 members, owner alice", status, data, 200,
              lambda d: d.get("your_position") == 2 and d.get("total_members") == 2
              and d.get("owner") == "alice@example.com")

    status, data = bob.call("GET", "/api/queues")
    joined = [q["queue_id"] for q in data.get("joined_by_me", [])]
    log.record("Queue: participation", "Bob lists queues he has JOINED",
               "both queues in joined_by_me",
               f"joined: {joined}", pub_id in joined and priv_id in joined)

    status, data = alice.call("GET", "/api/queues")
    mine = [q["queue_id"] for q in data.get("created_by_me", [])]
    log.record("Queue: ownership", "Alice lists queues she CREATED",
               "both queues in created_by_me",
               f"created: {mine}", pub_id in mine and priv_id in mine)

    # ------------------------------------------------- 4b. wait time + member roster
    status, data = bob.call("POST", f"/api/queues/{pub_id}/members/wait-time",
                            {"emails": ["bob@example.com"], "minutes": 15})
    log.check("Queue: wait time", "Bob (non-owner) tries to set his own wait time",
              "403 owner only", status, data, 403)

    status, data = alice.call("POST", f"/api/queues/{pub_id}/members/wait-time",
                              {"emails": ["nobody@example.com"], "minutes": 5})
    log.check("Queue: wait time", "Alice targets an email that never joined the queue",
              "400 rejected — not a member", status, data, 400)

    status, data = alice.call("POST", f"/api/queues/{pub_id}/members/wait-time",
                              {"emails": ["bob@example.com"], "minutes": 10})
    log.check("Queue: wait time", "Alice sets bob's personal wait time to 10 minutes",
              "200, bob's member_details shows 10", status, data, 200,
              lambda d: next(m["wait_minutes"] for m in d["member_details"]
                             if m["email"] == "bob@example.com") == 10)

    status, data = alice.call("POST", f"/api/queues/{pub_id}/members/wait-time",
                              {"emails": ["carol@example.com"], "minutes": 25})
    log.check("Queue: wait time", "Alice sets carol's personal wait time to 25 (different from bob's)",
              "200, bob stays 10, carol becomes 25 — proves per-member times differ", status, data, 200,
              lambda d: {m["email"]: m["wait_minutes"] for m in d["member_details"]} ==
              {"bob@example.com": 10, "carol@example.com": 25})

    status, data = bob.call("GET", f"/api/queues/{pub_id}")
    log.check("Queue: wait time", "Bob (joinee) sees only HIS OWN quoted time",
              "200, your_wait_minutes == 10, no roster leaked", status, data, 200,
              lambda d: d.get("your_wait_minutes") == 10 and "member_details" not in d)

    status, data = carol.call("GET", f"/api/queues/{pub_id}")
    log.check("Queue: wait time", "Carol (joinee) sees her own quoted time, distinct from bob's",
              "200, your_wait_minutes == 25", status, data, 200,
              lambda d: d.get("your_wait_minutes") == 25)

    status, data = alice.call("POST", f"/api/queues/{pub_id}/members/wait-time",
                              {"emails": ["bob@example.com", "carol@example.com"], "minutes": -5})
    log.check("Queue: wait time", "Alice tries a negative wait time for a selected group",
              "400 rejected", status, data, 400)

    status, data = alice.call("POST", f"/api/queues/{pub_id}/members/wait-time",
                              {"emails": ["bob@example.com", "carol@example.com"], "minutes": None})
    log.check("Queue: wait time", "Alice selects BOTH members and clears their wait time in one group edit",
              "200, both cleared back to null", status, data, 200,
              lambda d: all(m["wait_minutes"] is None for m in d["member_details"]))

    status, data = alice.call("GET", f"/api/queues/{pub_id}")
    log.check("Queue: roster", "Alice (owner) lists accounts who joined her queue",
              "200, member_details includes bob + carol with join timestamps", status, data, 200,
              lambda d: {m["email"] for m in d.get("member_details", [])} == {
                  "bob@example.com", "carol@example.com"}
              and all(m.get("joined_at") for m in d["member_details"]))

    status, data = bob.call("GET", f"/api/queues/{pub_id}")
    log.check("Queue: roster", "Bob (non-owner member) does NOT get the owner-only roster detail",
              "200, no member_details field for a non-owner", status, data, 200,
              lambda d: "member_details" not in d)

    # ------------------------------------------------- 5. removal / withdrawal
    status, data = carol.call("DELETE", f"/api/queues/{pub_id}/members/bob@example.com")
    log.check("Queue: moderation", "Carol (non-owner) tries to remove bob",
              "403 owner only", status, data, 403)

    status, data = alice.call("DELETE", f"/api/queues/{pub_id}/members/carol@example.com")
    log.check("Queue: moderation", "Alice (owner) removes carol from her queue",
              "200 removed", status, data, 200)

    status, data = bob.call("POST", f"/api/queues/{priv_id}/withdraw")
    log.check("Queue: participation", "Bob withdraws himself from private queue",
              "200 withdrawn", status, data, 200)

    status, data = bob.call("POST", f"/api/queues/{priv_id}/withdraw")
    log.check("Queue: participation", "Bob withdraws again (not a member)",
              "404 not a member", status, data, 404)

    # ------------------------------------------------------ 6. expiry + delete
    status, expired = alice.call("POST", "/api/queues",
                                 {"name": "Expired queue", "type": "public",
                                  "expiry_date": "2020-01-01T00:00:00"})
    exp_id = expired.get("queue_id", "")
    status, data = bob.call("POST", f"/api/queues/{exp_id}/join")
    log.check("Queue: expiry", "Bob joins a queue whose expiry date has passed",
              "403 queue expired", status, data, 403)

    status, data = bob.call("DELETE", f"/api/queues/{pub_id}")
    log.check("Queue: deletion", "Bob (non-owner) tries to delete Alice's queue",
              "403 owner only", status, data, 403)

    status, data = alice.call("DELETE", f"/api/queues/{exp_id}")
    log.check("Queue: deletion", "Alice deletes her own queue",
              "200 deleted", status, data, 200)

    status, data = alice.call("GET", f"/api/queues/{exp_id}")
    log.check("Queue: deletion", "Inspect deleted queue",
              "404 gone", status, data, 404)

    # unauthenticated access
    status, data = Client().call("GET", "/api/queues")
    log.check("Security", "List queues with NO auth token",
              "401 authentication required", status, data, 401)

    log.save("user_results.json")


if __name__ == "__main__":
    main()
